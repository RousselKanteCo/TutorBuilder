"""
apps/studio/views/burn.py — Endpoint burn sous-titres intégrés.
"""
import logging
import os
import threading
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from django.http import FileResponse
from ..models import Job

logger = logging.getLogger(__name__)


class BurnSubtitlesView(APIView):
    """
    POST /api/jobs/<job_id>/export/burn/
    Intègre les sous-titres dans la vidéo et retourne le fichier.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        final_path = job.output_dir / "final.mp4"
        vtt_path   = job.output_dir / "subtitles.vtt"

        if not final_path.exists():
            return Response({"error": "Assemblez la vidéo d'abord."}, status=status.HTTP_400_BAD_REQUEST)

        font_size = request.data.get("font_size", 28)
        position  = request.data.get("position", 2)

        # Burn en synchrone (petit fichier)
        burned_path = job.output_dir / "final_with_subs.mp4"

        from ..tasks.task_export import _burn_subtitles_sync
        ok = _burn_subtitles_sync(
            str(final_path),
            str(job.output_dir / "subtitles.ass"),
            str(burned_path),
            font_size=font_size,
            position=position,
        )

        if not ok or not burned_path.exists():
            return Response({"error": "Intégration des sous-titres échouée."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return FileResponse(
            open(str(burned_path), 'rb'),
            content_type='video/mp4',
            as_attachment=True,
            filename='tutorbuilder_avec_sous_titres.mp4',
        )