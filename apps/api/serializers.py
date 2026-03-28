"""
apps/api/serializers.py — Serializers DRF pour TutoBuilder Vision.

Remplacent les modèles Pydantic de server.py (TranscribeRequest,
SynthesizeRequest, JobResponse) avec la validation complète DRF.
"""

from rest_framework import serializers
from apps.studio.models import Project, Job, Segment


# ─────────────────────────────────────────
#  SEGMENT
# ─────────────────────────────────────────

class SegmentSerializer(serializers.ModelSerializer):
    """
    Sérialisation d'un segment de transcription.
    Format compatible avec stt_providers.transcrire() :
        {"start": ms, "end": ms, "text": "..."}
    """
    start_timecode = serializers.ReadOnlyField()
    duration_ms = serializers.ReadOnlyField()

    class Meta:
        model = Segment
        fields = [
            "id", "index",
            "start_ms", "end_ms", "duration_ms",
            "start_timecode",
            "text", "text_translated",
            "audio_file",
        ]
        read_only_fields = ["id", "index", "start_timecode", "duration_ms"]


class SegmentUpdateSerializer(serializers.ModelSerializer):
    """Pour la modification du script (éditeur de texte du cockpit)."""

    class Meta:
        model = Segment
        fields = ["text", "text_translated"]


# ─────────────────────────────────────────
#  JOB
# ─────────────────────────────────────────

class JobListSerializer(serializers.ModelSerializer):
    """Version légère pour les listes (sans segments)."""
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    segments_count = serializers.ReadOnlyField()
    project_name = serializers.CharField(source="project.name", read_only=True)

    class Meta:
        model = Job
        fields = [
            "id", "project", "project_name",
            "video_filename", "video_duration_ms",
            "stt_engine", "tts_engine", "tts_voice", "language",
            "status", "status_display",
            "segments_count",
            "created_at", "updated_at", "completed_at",
        ]
        read_only_fields = ["id", "status", "created_at", "updated_at", "completed_at"]


class JobDetailSerializer(serializers.ModelSerializer):
    """Version complète avec segments imbriqués."""
    status_display = serializers.CharField(source="get_status_display", read_only=True)
    segments = SegmentSerializer(many=True, read_only=True)
    segments_count = serializers.ReadOnlyField()

    class Meta:
        model = Job
        fields = [
            "id", "project",
            "video_file", "video_filename", "video_duration_ms",
            "stt_engine", "tts_engine", "tts_voice", "language",
            "status", "status_display", "error_message",
            "celery_task_id",
            "waveform_data", "thumbnail_paths",
            "segments", "segments_count",
            "created_at", "updated_at", "completed_at",
        ]
        read_only_fields = [
            "id", "status", "error_message", "celery_task_id",
            "waveform_data", "thumbnail_paths",
            "created_at", "updated_at", "completed_at",
        ]


# ─────────────────────────────────────────
#  PROJECT
# ─────────────────────────────────────────

class ProjectSerializer(serializers.ModelSerializer):
    jobs_count = serializers.SerializerMethodField()
    latest_job_status = serializers.SerializerMethodField()

    class Meta:
        model = Project
        fields = [
            "id", "name", "description",
            "jobs_count", "latest_job_status",
            "created_at", "updated_at",
        ]
        read_only_fields = ["id", "created_at", "updated_at"]

    def get_jobs_count(self, obj):
        return obj.jobs.count()

    def get_latest_job_status(self, obj):
        job = obj.latest_job
        return job.status if job else None


# ─────────────────────────────────────────
#  REQUÊTES ACTION (remplacent Pydantic de server.py)
# ─────────────────────────────────────────

class UploadVideoSerializer(serializers.Serializer):
    """
    POST /api/jobs/ avec le fichier vidéo.
    Équivalent de POST /upload dans server.py.
    """
    project_id = serializers.UUIDField()
    video_file = serializers.FileField()
    stt_engine = serializers.ChoiceField(
        choices=Job.STTEngine.choices,
        default=Job.STTEngine.FASTER_WHISPER,
    )
    tts_engine = serializers.ChoiceField(
        choices=Job.TTSEngine.choices,
        default=Job.TTSEngine.COQUI,
    )
    language = serializers.ChoiceField(
        choices=Job.Language.choices,
        default=Job.Language.FR,
    )

    def validate_video_file(self, value):
        """Valide le type MIME et la taille du fichier."""
        from django.conf import settings

        # Vérification de taille
        if value.size > settings.MAX_UPLOAD_SIZE:
            raise serializers.ValidationError(
                f"Fichier trop volumineux (max {settings.MAX_UPLOAD_SIZE_MB} Mo)."
            )

        # Vérification de type MIME basique
        allowed_types = [
            "video/mp4", "video/avi", "video/x-msvideo",
            "video/quicktime", "video/x-matroska", "video/webm",
        ]
        content_type = getattr(value, "content_type", "")
        if content_type and content_type not in allowed_types:
            # On accepte aussi si le content_type est vide (certains navigateurs)
            if not content_type.startswith("video/"):
                raise serializers.ValidationError(
                    "Type de fichier non supporté. Formats acceptés : mp4, avi, mkv, mov, webm."
                )

        return value


class TranscribeRequestSerializer(serializers.Serializer):
    """
    POST /api/jobs/<id>/transcribe/
    Équivalent de POST /transcribe/{job_id} dans server.py.
    """
    stt_engine = serializers.ChoiceField(
        choices=Job.STTEngine.choices,
        default=Job.STTEngine.FASTER_WHISPER,
    )
    language = serializers.ChoiceField(
        choices=Job.Language.choices,
        default=Job.Language.FR,
    )


class SynthesizeRequestSerializer(serializers.Serializer):
    """
    POST /api/jobs/<id>/synthesize/
    Équivalent de POST /synthesize/{job_id} dans server.py.
    """
    tts_engine = serializers.ChoiceField(
        choices=Job.TTSEngine.choices,
        default=Job.TTSEngine.COQUI,
    )
    voice = serializers.CharField(default="default", max_length=100)
    language = serializers.ChoiceField(
        choices=Job.Language.choices,
        default=Job.Language.FR,
    )


class TaskStatusSerializer(serializers.Serializer):
    """
    GET /api/tasks/<task_id>/
    Équivalent de GET /status/{task_id} dans server.py.
    """
    task_id = serializers.CharField(read_only=True)
    state = serializers.CharField(read_only=True)
    progress = serializers.IntegerField(read_only=True)
    detail = serializers.DictField(read_only=True, required=False)
    result = serializers.DictField(read_only=True, required=False)
    error = serializers.CharField(read_only=True, required=False)
