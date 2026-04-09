"""
apps/studio/models.py — Modèles de données TutoBuilder Vision.

Hiérarchie :
    Project (1) ──< Job (N) ──< Segment (N)
"""

import uuid
import os
from django.db import models
from django.contrib.auth.models import User
from django.utils.translation import gettext_lazy as _
from django.conf import settings


# ─────────────────────────────────────────
#  UTILITAIRES
# ─────────────────────────────────────────

def video_upload_path(instance, filename):
    """
    Tous les uploads dans media/uploads/<uuid>.<ext>
    - Nom UUID : pas de collision, pas d'espaces, pas d'exposition du nom client
    - Extension conservée pour que ffprobe détecte le format correctement
    - Dossier unique centralisé, facile à nettoyer
    """
    ext = os.path.splitext(filename)[1].lower() or ".mp4"
    return f"uploads/{uuid.uuid4()}{ext}"


# ─────────────────────────────────────────
#  PROJECT
# ─────────────────────────────────────────

class Project(models.Model):
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner       = models.ForeignKey(User, on_delete=models.CASCADE, related_name="projects")
    name        = models.CharField(_("nom"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = _("projet")
        verbose_name_plural = _("projets")
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    @property
    def latest_job(self):
        return self.jobs.order_by("-created_at").first()


# ─────────────────────────────────────────
#  JOB
# ─────────────────────────────────────────

class Job(models.Model):

    class Status(models.TextChoices):
        PENDING      = "pending",      _("En attente")
        UPLOADING    = "uploading",    _("Upload en cours")
        EXTRACTING   = "extracting",   _("Extraction audio")
        TRANSCRIBING = "transcribing", _("Transcription")
        TRANSCRIBED  = "transcribed",  _("Transcription terminée")
        SYNTHESIZING = "synthesizing", _("Synthèse vocale")
        DONE         = "done",         _("Terminé")
        ERROR        = "error",        _("Erreur")

    class STTEngine(models.TextChoices):
        WHISPER        = "whisper",        _("Whisper (OpenAI)")
        FASTER_WHISPER = "faster_whisper", _("Faster-Whisper (recommandé)")
        VOSK           = "vosk",           _("Vosk (hors-ligne léger)")

    class TTSEngine(models.TextChoices):
        ELEVENLABS = "elevenlabs", _("ElevenLabs (meilleure qualité)")
        COQUI      = "coqui",     _("Coqui TTS (local gratuit)")
        BARK       = "bark",      _("Bark / Suno (local expressif)")
        PYTTSX3    = "pyttsx3",   _("Windows TTS (hors-ligne)")

    class Language(models.TextChoices):
        FR = "fr", _("Français")
        EN = "en", _("English")
        ES = "es", _("Español")
        DE = "de", _("Deutsch")
        IT = "it", _("Italiano")
        PT = "pt", _("Português")

    # ── Identifiants ──────────────────────────────────────────────────────
    id      = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, on_delete=models.CASCADE, related_name="jobs", verbose_name=_("projet")
    )

    # ── Fichier source ────────────────────────────────────────────────────
    video_file = models.FileField(
        _("fichier vidéo"),
        upload_to=video_upload_path,   # UUID centralisé dans media/uploads/
        max_length=500,
    )
    video_filename = models.CharField(
        _("nom original"), max_length=255, blank=True,
        help_text="Nom original affiché dans l'interface",
    )
    video_duration_ms = models.PositiveIntegerField(_("durée (ms)"), null=True, blank=True)

    # ── Configuration ─────────────────────────────────────────────────────
    stt_engine = models.CharField(
        _("moteur STT"), max_length=20,
        choices=STTEngine.choices, default=STTEngine.FASTER_WHISPER,
    )
    tts_engine = models.CharField(
        _("moteur TTS"), max_length=20,
        choices=TTSEngine.choices, default=TTSEngine.ELEVENLABS,
    )
    tts_voice  = models.CharField(_("voix TTS"), max_length=100, default="narrateur_pro")
    language   = models.CharField(
        _("langue"), max_length=5,
        choices=Language.choices, default=Language.FR,
    )

    # ── État ──────────────────────────────────────────────────────────────
    status        = models.CharField(
        _("statut"), max_length=20,
        choices=Status.choices, default=Status.PENDING,
    )
    error_message = models.TextField(_("message d'erreur"), blank=True)
    celery_task_id = models.CharField(_("ID tâche Celery"), max_length=255, blank=True)

    # ── Données dérivées ──────────────────────────────────────────────────
    waveform_data   = models.JSONField(_("waveform"), default=list, blank=True)
    thumbnail_paths = models.JSONField(_("vignettes"), default=list, blank=True)

    # ── Horodatages ───────────────────────────────────────────────────────
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = _("job")
        verbose_name_plural = _("jobs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Job {self.pk} — {self.get_status_display()}"

    @property
    def output_dir(self):
        return settings.OUTPUTS_ROOT / str(self.pk)

    @property
    def wav_path(self):
        return self.output_dir / "audio.wav"

    @property
    def segments_count(self):
        return self.segments.count()

    def set_status(self, status: str, error: str = ""):
        self.status = status
        if error:
            self.error_message = error
        if status == Job.Status.DONE:
            from django.utils import timezone
            self.completed_at = timezone.now()
        self.save(update_fields=["status", "error_message", "completed_at", "updated_at"])


# ─────────────────────────────────────────
#  SEGMENT
# ─────────────────────────────────────────

class Segment(models.Model):

    job    = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="segments")
    index  = models.PositiveSmallIntegerField(_("index"))
    start_ms = models.PositiveIntegerField(_("début (ms)"))
    end_ms   = models.PositiveIntegerField(_("fin (ms)"))
    text          = models.TextField(_("texte original"))
    text_translated = models.TextField(_("texte traduit"), blank=True)
    audio_file    = models.CharField(_("fichier audio TTS"), max_length=500, blank=True)

    class Meta:
        verbose_name = _("segment")
        verbose_name_plural = _("segments")
        ordering = ["job", "index"]
        unique_together = [["job", "index"]]

    def __str__(self):
        return f"[{self.start_ms}ms] {self.text[:60]}"

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms

    @property
    def start_timecode(self):
        total_s = self.start_ms // 1000
        return f"{total_s // 60:02d}:{total_s % 60:02d}"

    @classmethod
    def bulk_create_from_stt(cls, job: Job, stt_segments: list) -> int:
        cls.objects.filter(job=job).delete()
        objs = [
            cls(
                job=job,
                index=i,
                start_ms=seg["start"],
                end_ms=seg["end"],
                text=seg["text"].strip(),
            )
            for i, seg in enumerate(stt_segments)
            if seg.get("text", "").strip()
        ]
        cls.objects.bulk_create(objs, batch_size=500)
        return len(objs)