"""
apps/studio/views/export.py — Endpoint export vidéo.
"""

import logging
import threading
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from ..models import Job, Segment

logger = logging.getLogger(__name__)


class ExportView(APIView):
    """
    POST /api/jobs/<job_id>/export/
    Lance l'assemblage vidéo final.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        # Vérifications
        if not Segment.objects.filter(job=job).exists():
            return Response(
                {"error": "Aucun segment. Transcrivez d'abord."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        segments_avec_audio = Segment.objects.filter(
            job=job
        ).exclude(audio_file="").exclude(audio_file__isnull=True)

        if not segments_avec_audio.exists():
            return Response(
                {"error": "Aucune voix générée. Lancez la synthèse vocale d'abord."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Reset automatique si bloqué
        if job.status in (Job.Status.SYNTHESIZING, Job.Status.ERROR):
            job.status = Job.Status.DONE
            job.save(update_fields=["status"])

        burn_subtitles = request.data.get("burn_subtitles", False)
        subtitle_style = request.data.get("subtitle_style", {})

        from ..tasks.task_export import task_export

        def _run():
            task_export(str(job.pk), burn_subtitles, subtitle_style)

        threading.Thread(target=_run, daemon=True).start()
        logger.info(f"Export lancé : job={job.pk}")

        return Response({
            "status":  "started",
            "message": "Assemblage vidéo lancé.",
        })


class ExportStatusView(APIView):
    """
    GET /api/jobs/<job_id>/export/status/
    Retourne le statut de l'export et l'URL de téléchargement.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        from pathlib import Path
        from django.conf import settings

        final_path = job.output_dir / "final.mp4"
        ass_path   = job.output_dir / "subtitles.ass"
        vtt_path   = job.output_dir / "subtitles.vtt"

        download_url = ""
        vtt_url      = ""

        if final_path.exists():
            try:
                rel = final_path.relative_to(settings.OUTPUTS_ROOT)
                download_url = f"/outputs/{str(rel).replace(chr(92), '/')}"
            except ValueError:
                pass

        if vtt_path.exists():
            try:
                rel = vtt_path.relative_to(settings.OUTPUTS_ROOT)
                vtt_url = f"/outputs/{str(rel).replace(chr(92), '/')}"
            except ValueError:
                pass

        subbed_path  = job.output_dir / "final_subtitled.mp4"
        subtitled_url = ""
        if subbed_path.exists() and subbed_path.stat().st_size > 10_000:
            try:
                rel = subbed_path.relative_to(settings.OUTPUTS_ROOT)
                subtitled_url = f"/outputs/{str(rel).replace(chr(92), '/')}"
            except ValueError:
                pass

        return Response({
            "status":        job.status,
            "has_video":     final_path.exists(),
            "has_subtitles": vtt_path.exists(),
            "download_url":  download_url,
            "vtt_url":       vtt_url,
            "subtitled_url": subtitled_url,
            "error":         job.error_message if hasattr(job, 'error_message') else "",
        })