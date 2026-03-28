"""
apps/studio/models.py — Modèles de données TutoBuilder Vision.

Remplacement des simples dicts de server.py par un ORM Django complet.

Hiérarchie :
    Project (1) ──< Job (N) ──< Segment (N)

    Project : conteneur logique (ex: "Tutoriel Photoshop v2")
    Job     : une exécution (upload + transcription + TTS)
    Segment : un fragment de texte avec timestamps (issu de la transcription)
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
    Chemin d'upload dynamique pour les vidéos.
    → media/jobs/<job_uuid>/video/<filename>
    Évite la collision de noms et facilite la suppression par job.
    """
    ext = os.path.splitext(filename)[1].lower()
    return f"jobs/{instance.pk}/video/source{ext}"


# ─────────────────────────────────────────
#  PROJECT
# ─────────────────────────────────────────

class Project(models.Model):
    """
    Conteneur logique pour regrouper plusieurs jobs d'un même tutoriel.
    Équivalent conceptuel des données de travail (video_path, segments)
    stockées dans la fenêtre PyQt6 MonumentV8.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    owner = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="projects",
        verbose_name=_("propriétaire"),
    )
    name = models.CharField(_("nom"), max_length=255)
    description = models.TextField(_("description"), blank=True)
    created_at = models.DateTimeField(_("créé le"), auto_now_add=True)
    updated_at = models.DateTimeField(_("mis à jour le"), auto_now=True)

    class Meta:
        verbose_name = _("projet")
        verbose_name_plural = _("projets")
        ordering = ["-updated_at"]

    def __str__(self):
        return self.name

    @property
    def latest_job(self):
        """Retourne le dernier job du projet."""
        return self.jobs.order_by("-created_at").first()


# ─────────────────────────────────────────
#  JOB
# ─────────────────────────────────────────

class Job(models.Model):
    """
    Représente une exécution complète du pipeline :
    upload vidéo → extraction audio → transcription → TTS.

    Équivalent du couple (job_id + task_id) de server.py,
    enrichi avec l'état persistant en base.
    """

    class Status(models.TextChoices):
        PENDING       = "pending",       _("En attente")
        UPLOADING     = "uploading",     _("Upload en cours")
        EXTRACTING    = "extracting",    _("Extraction audio")
        TRANSCRIBING  = "transcribing",  _("Transcription")
        TRANSCRIBED   = "transcribed",   _("Transcription terminée")
        SYNTHESIZING  = "synthesizing",  _("Synthèse vocale")
        DONE          = "done",          _("Terminé")
        ERROR         = "error",         _("Erreur")

    class STTEngine(models.TextChoices):
        WHISPER        = "whisper",         _("Whisper (OpenAI)")
        FASTER_WHISPER = "faster_whisper",  _("Faster-Whisper")
        VOSK           = "vosk",            _("Vosk (local léger)")

    class TTSEngine(models.TextChoices):
        COQUI       = "coqui",       _("Coqui TTS (local)")
        PIPER       = "piper",       _("Piper TTS (local, rapide)")
        ELEVENLABS  = "elevenlabs",  _("ElevenLabs (cloud)")
        BARK        = "bark",        _("Bark / Suno (local expressif)")

    class Language(models.TextChoices):
        FR = "fr", _("Français")
        EN = "en", _("English")
        ES = "es", _("Español")
        DE = "de", _("Deutsch")

    # ── Identifiants ──
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project,
        on_delete=models.CASCADE,
        related_name="jobs",
        verbose_name=_("projet"),
    )

    # ── Fichier source ──
    video_file = models.FileField(
        _("fichier vidéo"),
        upload_to=video_upload_path,
        max_length=500,
    )
    video_filename = models.CharField(
        _("nom original"), max_length=255, blank=True,
        help_text="Nom original du fichier uploadé, affiché dans l'UI",
    )
    video_duration_ms = models.PositiveIntegerField(
        _("durée (ms)"), null=True, blank=True,
    )

    # ── Configuration STT ──
    stt_engine = models.CharField(
        _("moteur STT"),
        max_length=20,
        choices=STTEngine.choices,
        default=STTEngine.FASTER_WHISPER,
    )

    # ── Configuration TTS ──
    tts_engine = models.CharField(
        _("moteur TTS"),
        max_length=20,
        choices=TTSEngine.choices,
        default=TTSEngine.COQUI,
    )
    tts_voice = models.CharField(
        _("voix TTS"), max_length=100, default="default",
    )

    # ── Langue ──
    language = models.CharField(
        _("langue"),
        max_length=5,
        choices=Language.choices,
        default=Language.FR,
    )

    # ── État du pipeline ──
    status = models.CharField(
        _("statut"),
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    error_message = models.TextField(_("message d'erreur"), blank=True)

    # ── Référence Celery (pour suivre la tâche en cours) ──
    celery_task_id = models.CharField(
        _("ID tâche Celery"), max_length=255, blank=True,
    )

    # ── Données dérivées (waveform, vignettes) ──
    waveform_data = models.JSONField(
        _("données waveform"),
        default=list,
        blank=True,
        help_text="Liste de floats [0.0→1.0] pour l'affichage Canvas",
    )
    thumbnail_paths = models.JSONField(
        _("chemins vignettes"),
        default=list,
        blank=True,
        help_text="Chemins relatifs des vignettes extraites",
    )

    # ── Horodatages ──
    created_at = models.DateTimeField(_("créé le"), auto_now_add=True)
    updated_at = models.DateTimeField(_("mis à jour le"), auto_now=True)
    completed_at = models.DateTimeField(_("terminé le"), null=True, blank=True)

    class Meta:
        verbose_name = _("job")
        verbose_name_plural = _("jobs")
        ordering = ["-created_at"]

    def __str__(self):
        return f"Job {self.pk} — {self.get_status_display()}"

    @property
    def output_dir(self):
        """Dossier de sortie pour ce job (audio TTS, vidéo finale)."""
        return settings.OUTPUTS_ROOT / str(self.pk)

    @property
    def wav_path(self):
        """Chemin de l'audio WAV extrait."""
        return self.output_dir / "audio.wav"

    @property
    def segments_count(self):
        return self.segments.count()

    def set_status(self, status: str, error: str = ""):
        """Met à jour le statut et sauvegarde."""
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
    """
    Fragment de texte avec timestamps, issu de la transcription STT.

    Équivalent de la liste de dicts :
        [{"start": 1200, "end": 3400, "text": "Bonjour..."}]
    de stt_providers.py, persistée en base.
    """

    job = models.ForeignKey(
        Job,
        on_delete=models.CASCADE,
        related_name="segments",
        verbose_name=_("job"),
    )
    index = models.PositiveSmallIntegerField(
        _("index"),
        help_text="Position du segment dans la séquence (0-based)",
    )
    start_ms = models.PositiveIntegerField(_("début (ms)"))
    end_ms = models.PositiveIntegerField(_("fin (ms)"))
    text = models.TextField(_("texte"))
    text_translated = models.TextField(
        _("texte traduit"), blank=True,
        help_text="Version traduite du segment (étape 3 du pipeline)",
    )

    # Chemin vers le fichier audio TTS généré pour ce segment
    audio_file = models.CharField(
        _("fichier audio"), max_length=500, blank=True,
        help_text="Chemin relatif du fichier WAV/MP3 généré par TTS",
    )

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
        """Retourne le timecode au format mm:ss."""
        total_s = self.start_ms // 1000
        return f"{total_s // 60:02d}:{total_s % 60:02d}"

    @classmethod
    def bulk_create_from_stt(cls, job: Job, stt_segments: list) -> int:
        """
        Crée tous les segments en une seule requête SQL depuis
        la liste retournée par stt_provider.transcrire().

        Args:
            job:           Instance Job parente
            stt_segments:  [{"start": ms, "end": ms, "text": "..."}]

        Returns:
            Nombre de segments créés
        """
        # Vider les anciens segments si re-transcription
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