"""
apps/api/serializers.py — Serializers DRF pour TutoBuilder Vision.
"""

from rest_framework import serializers
from apps.studio.models import Project, Job, Segment


# ─────────────────────────────────────────
#  SEGMENT
# ─────────────────────────────────────────

class SegmentSerializer(serializers.ModelSerializer):
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
    class Meta:
        model = Segment
        fields = ["text", "text_translated"]


# ─────────────────────────────────────────
#  JOB
# ─────────────────────────────────────────

class JobListSerializer(serializers.ModelSerializer):
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
#  REQUÊTES ACTION
# ─────────────────────────────────────────

class UploadVideoSerializer(serializers.Serializer):
    project_id = serializers.UUIDField()
    video_file = serializers.FileField()
    stt_engine = serializers.CharField(default="faster_whisper", max_length=50)
    tts_engine = serializers.CharField(default="elevenlabs", max_length=50)
    language   = serializers.CharField(default="fr", max_length=10)

    def validate_video_file(self, value):
        from django.conf import settings
        if value.size > settings.MAX_UPLOAD_SIZE:
            raise serializers.ValidationError(
                f"Fichier trop volumineux (max {settings.MAX_UPLOAD_SIZE_MB} Mo)."
            )
        content_type = getattr(value, "content_type", "")
        if content_type and not content_type.startswith("video/"):
            raise serializers.ValidationError(
                "Type de fichier non supporté. Formats acceptés : mp4, avi, mkv, mov, webm."
            )
        return value


class TranscribeRequestSerializer(serializers.Serializer):
    stt_engine = serializers.CharField(default="faster_whisper", max_length=50)
    language   = serializers.CharField(default="fr", max_length=10)


class SynthesizeRequestSerializer(serializers.Serializer):
    tts_engine = serializers.CharField(default="elevenlabs", max_length=50)
    voice      = serializers.CharField(default="narrateur_pro", max_length=100)
    language   = serializers.CharField(default="fr", max_length=10)


class TaskStatusSerializer(serializers.Serializer):
    task_id  = serializers.CharField(read_only=True)
    state    = serializers.CharField(read_only=True)
    progress = serializers.IntegerField(read_only=True)
    detail   = serializers.DictField(read_only=True, required=False)
    result   = serializers.DictField(read_only=True, required=False)
    error    = serializers.CharField(read_only=True, required=False)