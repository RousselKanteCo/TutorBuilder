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
    Stockage : media/uploads/<uuid>.<ext>
    - Nom UUID : pas de collision, pas d'espaces
    - Extension conservée pour que ffprobe détecte le format
    - Nom original conservé dans video_filename
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
        verbose_name        = _("projet")
        verbose_name_plural = _("projets")
        ordering            = ["-updated_at"]

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
        FASTER_WHISPER = "faster_whisper", _("Faster-Whisper (recommandé)")

    class TTSEngine(models.TextChoices):
        ELEVENLABS = "elevenlabs", _("ElevenLabs (premium)")
        CARTESIA   = "cartesia",   _("Cartesia Sonic (ultra-rapide)")

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
        Project, on_delete=models.CASCADE,
        related_name="jobs", verbose_name=_("projet"),
    )

    # ── Titre (saisi par l'user) ───────────────────────────────────────────
    title = models.CharField(
        _("titre"), max_length=255, blank=True,
        help_text="Titre saisi par l'utilisateur dans l'interface",
    )

    # ── Fichier source ────────────────────────────────────────────────────
    video_file = models.FileField(
        _("fichier vidéo"),
        upload_to=video_upload_path,   # UUID dans media/uploads/
        max_length=500,
    )
    # Nom original du fichier — affiché dans l'interface, jamais modifié
    video_filename = models.CharField(
        _("nom original du fichier"), max_length=255, blank=True,
        help_text="Nom exact du fichier uploadé par l'utilisateur",
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
    status         = models.CharField(
        _("statut"), max_length=20,
        choices=Status.choices, default=Status.PENDING,
    )
    error_message  = models.TextField(_("message d'erreur"), blank=True)
    celery_task_id = models.CharField(_("ID tâche Celery"), max_length=255, blank=True)

    # ── Données dérivées ──────────────────────────────────────────────────
    waveform_data   = models.JSONField(_("waveform"), default=list, blank=True)
    thumbnail_paths = models.JSONField(_("vignettes"), default=list, blank=True)
    subtitled_url   = models.CharField(_("URL vidéo sous-titrée"), max_length=500, blank=True, default="")

    # ── Horodatages ───────────────────────────────────────────────────────
    created_at   = models.DateTimeField(auto_now_add=True)
    updated_at   = models.DateTimeField(auto_now=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name        = _("job")
        verbose_name_plural = _("jobs")
        ordering            = ["-created_at"]

    def __str__(self):
        return f"{self.title or self.video_filename or str(self.pk)[:8]} — {self.get_status_display()}"

    @property
    def display_name(self):
        """Nom affiché : titre si renseigné, sinon nom du fichier original."""
        return self.title or self.video_filename or f"Job {str(self.pk)[:8]}"

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
#  VOICE PROFILE
# ─────────────────────────────────────────

class VoiceProfile(models.Model):
    """
    Profil de vitesse réelle d'une voix TTS.
    WPM mesuré après chaque synthèse pour affiner les calculs.
    """

    class TTSEngine(models.TextChoices):
        ELEVENLABS = "elevenlabs", _("ElevenLabs")
        CARTESIA   = "cartesia",   _("Cartesia")

    voice_id     = models.CharField(_("identifiant voix"), max_length=100)
    tts_engine   = models.CharField(_("moteur TTS"), max_length=20, choices=TTSEngine.choices)
    wpm_default  = models.FloatField(_("WPM par défaut"), default=145.0)
    wpm_measured = models.FloatField(_("WPM mesuré réel"), null=True, blank=True)
    nb_samples   = models.PositiveIntegerField(_("nombre de mesures"), default=0)
    updated_at   = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name        = _("profil voix")
        verbose_name_plural = _("profils voix")
        unique_together     = [["voice_id", "tts_engine"]]

    def __str__(self):
        return f"{self.tts_engine} — {self.voice_id} ({self.wpm_effective:.0f} WPM)"

    @property
    def wpm_effective(self):
        """WPM à utiliser pour les calculs — mesuré si dispo, sinon défaut."""
        return self.wpm_measured if self.wpm_measured else self.wpm_default

    def update_wpm(self, nb_words: int, duration_s: float):
        """
        Met à jour le WPM mesuré avec une moyenne glissante.
        Appelé après chaque synthèse réussie.
        """
        if nb_words < 3 or duration_s < 0.5:
            return  # trop court pour être fiable

        new_wpm = (nb_words / duration_s) * 60.0

        if self.wpm_measured and self.nb_samples > 0:
            # Moyenne pondérée — les nouvelles mesures ont plus de poids
            self.wpm_measured = (self.wpm_measured * self.nb_samples + new_wpm) / (self.nb_samples + 1)
        else:
            self.wpm_measured = new_wpm

        self.nb_samples += 1
        self.save(update_fields=["wpm_measured", "nb_samples", "updated_at"])

    @classmethod
    def get_wpm(cls, voice_id: str, tts_engine: str) -> float:
        """Retourne le WPM effectif pour une voix donnée."""
        try:
            profile = cls.objects.get(voice_id=voice_id, tts_engine=tts_engine)
            return profile.wpm_effective
        except cls.DoesNotExist:
            return 145.0  # défaut ElevenLabs

    @classmethod
    def get_or_create_profile(cls, voice_id: str, tts_engine: str) -> "VoiceProfile":
        """Récupère ou crée un profil voix."""
        profile, _ = cls.objects.get_or_create(
            voice_id   = voice_id,
            tts_engine = tts_engine,
            defaults   = {"wpm_default": 145.0},
        )
        return profile

class Segment(models.Model):

    job      = models.ForeignKey(Job, on_delete=models.CASCADE, related_name="segments")
    index    = models.PositiveSmallIntegerField(_("index"))
    start_ms = models.PositiveIntegerField(_("début (ms)"))
    end_ms   = models.PositiveIntegerField(_("fin (ms)"))
    text            = models.TextField(_("texte original"))
    text_translated = models.TextField(_("texte traduit"), blank=True)
    audio_file      = models.CharField(_("fichier audio TTS"), max_length=500, blank=True)

    # ── Sync vidéo ────────────────────────────────────────────────────────
    actual_tts_ms  = models.FloatField(_("durée TTS réelle (ms)"), null=True, blank=True)
    speed_factor   = models.FloatField(_("facteur vitesse vidéo"), default=1.0,
                                        help_text="< 1.0 = ralenti, > 1.0 = accéléré, 1.0 = normal")
    speed_forced   = models.BooleanField(_("vitesse forcée par l'user"), default=False,
                                          help_text="Si True, on ne recalcule pas automatiquement")
    trim_start_ms  = models.IntegerField(default=0,
                                          help_text="Point IN dans la vidéo source (ms)")
    trim_end_ms    = models.IntegerField(default=0,
                                          help_text="Point OUT dans la vidéo source (ms). 0 = utiliser end_ms")

    class Meta:
        verbose_name        = _("segment")
        verbose_name_plural = _("segments")
        ordering            = ["job", "index"]
        unique_together     = [["job", "index"]]

    def __str__(self):
        return f"[{self.start_ms}ms] {self.text[:60]}"

    @property
    def duration_ms(self):
        return self.end_ms - self.start_ms

    @property
    def effective_start_ms(self):
        """Point IN réel dans la vidéo source."""
        return self.trim_start_ms if self.trim_start_ms > 0 else self.start_ms

    @property
    def effective_end_ms(self):
        """Point OUT réel dans la vidéo source."""
        return self.trim_end_ms if self.trim_end_ms > 0 else self.end_ms

    @property
    def effective_duration_ms(self):
        """Durée effective après trim."""
        return self.effective_end_ms - self.effective_start_ms

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
                start_ms=seg.get("start_ms", seg.get("start", 0)),
                end_ms=seg.get("end_ms", seg.get("end", 0)),
                text=seg["text"].strip(),
            )
            for i, seg in enumerate(stt_segments)
            if seg.get("text", "").strip()
        ]
        cls.objects.bulk_create(objs, batch_size=500)
        return len(objs)