"""
apps/studio/views/subtitles.py — Génération sous-titres via ElevenLabs STT.
"""
import logging
import threading

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from apps.studio.models import Job

logger = logging.getLogger(__name__)


class GenerateSubtitlesView(APIView):
    """POST /api/jobs/<job_id>/generate-subtitles/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        final_path = job.output_dir / "final.mp4"
        if not final_path.exists():
            return Response(
                {"error": "Aucune vidéo finale — exportez d'abord."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Lancer en thread background
        def run():
            from apps.studio.tasks.task_subtitles import task_generate_subtitles
            task_generate_subtitles(str(job_id))

        t = threading.Thread(target=run, daemon=True)
        t.start()

        logger.info(f"Génération sous-titres lancée — job={job_id}")
        return Response({"status": "started", "message": "Génération des sous-titres lancée."})


class SubtitlesStatusView(APIView):
    """GET /api/jobs/<job_id>/subtitles/status/"""
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        subbed_path = job.output_dir / "final_subtitled.mp4"
        done = subbed_path.exists() and subbed_path.stat().st_size > 10_000

        return Response({
            "done":          done,
            "subtitled_url": job.subtitled_url if done else "",
        })