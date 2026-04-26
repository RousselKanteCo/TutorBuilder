"""
apps/studio/views/transcribe.py
"""
import logging
import threading
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from ..models import Job

logger = logging.getLogger(__name__)

class TranscribeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if not job.video_file:
            return Response({"error": "Aucun fichier vidéo associé."}, status=status.HTTP_400_BAD_REQUEST)

        # Reset automatique si bloqué en transcribing/extracting/error
        if job.status in (Job.Status.TRANSCRIBING, Job.Status.EXTRACTING, Job.Status.ERROR):
            job.status = Job.Status.PENDING
            job.save(update_fields=["status"])

        langue     = request.data.get("language", job.language or "fr")
        stt_engine = request.data.get("stt_engine", "faster_whisper")

        from ..tasks.task_transcribe import task_transcribe

        def _run():
            task_transcribe(str(job.pk), stt_engine, langue)

        threading.Thread(target=_run, daemon=True).start()
        logger.info(f"Transcription lancée : job={job.pk}")
        return Response({"task_id": "thread", "status": "started", "message": "Transcription lancée."})