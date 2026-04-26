"""
apps/studio/serializers.py — Serializers pour l'API TutoBuilder.
"""

import os
from rest_framework import serializers
from .models import Job, Project


# ─────────────────────────────────────────
#  CONSTANTES VALIDATION
# ─────────────────────────────────────────

MAX_FILE_SIZE_BYTES = 200 * 1024 * 1024  # 200 Mo

ACCEPTED_MIME_TYPES = {
    "video/mp4",
    "video/x-msvideo",
    "video/quicktime",
    "video/x-matroska",
    "video/webm",
    "video/x-ms-wmv",
    "video/mpeg",
}

ACCEPTED_EXTENSIONS = {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".webm", ".mpeg", ".mpg"}


# ─────────────────────────────────────────
#  UPLOAD JOB
# ─────────────────────────────────────────

class JobUploadSerializer(serializers.Serializer):
    """
    Valide et crée un Job à partir d'un upload vidéo.

    Champs attendus (multipart/form-data) :
        video_file  — fichier vidéo obligatoire
        project_id  — UUID du projet obligatoire
        title       — titre du job (optionnel)
    """

    video_file = serializers.FileField()
    project_id = serializers.UUIDField()
    title      = serializers.CharField(max_length=255, required=False, allow_blank=True)

    # ── Validation fichier ────────────────────────────────────────────────

    def validate_video_file(self, file):
        # Taille
        if file.size > MAX_FILE_SIZE_BYTES:
            size_mb = file.size / (1024 * 1024)
            raise serializers.ValidationError(
                f"Fichier trop volumineux ({size_mb:.0f} Mo). Maximum autorisé : 200 Mo."
            )

        # Taille minimale — fichier corrompu
        if file.size < 1024:
            raise serializers.ValidationError(
                "Ce fichier semble corrompu ou vide."
            )

        # Extension
        ext = os.path.splitext(file.name)[1].lower()
        if ext not in ACCEPTED_EXTENSIONS:
            raise serializers.ValidationError(
                f"Extension non supportée : '{ext}'. "
                f"Formats acceptés : MP4, MKV, AVI, MOV, WMV, WebM."
            )

        # MIME type
        content_type = getattr(file, "content_type", "")
        if content_type and content_type not in ACCEPTED_MIME_TYPES:
            raise serializers.ValidationError(
                f"Type de fichier non supporté : '{content_type}'. "
                "Importez une vidéo valide."
            )

        return file

    # ── Validation projet ─────────────────────────────────────────────────

    def validate_project_id(self, value):
        request = self.context.get("request")
        owner   = request.user if request else None

        try:
            project = Project.objects.get(pk=value, owner=owner)
        except Project.DoesNotExist:
            raise serializers.ValidationError(
                "Projet introuvable ou vous n'y avez pas accès."
            )

        self._project = project
        return value

    # ── Création du Job ───────────────────────────────────────────────────

    def create(self, validated_data):
        file       = validated_data["video_file"]
        project    = self._project
        title      = validated_data.get("title", "").strip()

        job = Job.objects.create(
            project        = project,
            title          = title,
            video_file     = file,
            video_filename = file.name,   # nom original conservé
            status         = Job.Status.PENDING,
        )

        return job


# ─────────────────────────────────────────
#  RÉPONSE JOB
# ─────────────────────────────────────────

class JobResponseSerializer(serializers.ModelSerializer):
    """Sérialise un Job pour la réponse API après création."""

    display_name = serializers.ReadOnlyField()

    class Meta:
        model  = Job
        fields = [
            "id",
            "title",
            "video_filename",
            "display_name",
            "status",
            "created_at",
        ]