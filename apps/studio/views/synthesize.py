"""
apps/studio/views/synthesize.py
"""
import logging
import threading
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from ..models import Job, Segment

logger = logging.getLogger(__name__)

class SynthesizeView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if not Segment.objects.filter(job=job).exists():
            return Response({"error": "Aucun segment en base. Sauvegardez d'abord."}, status=status.HTTP_400_BAD_REQUEST)

        if not Segment.objects.filter(job=job).exclude(text="").exists():
            return Response({"error": "Tous les segments sont vides."}, status=status.HTTP_400_BAD_REQUEST)

        # Si bloqué en synthesizing ou error → reset automatique pour permettre relance
        if job.status in (Job.Status.SYNTHESIZING, Job.Status.ERROR):
            job.status = Job.Status.TRANSCRIBED
            job.save(update_fields=["status"])

        tts_engine = request.data.get("tts_engine", job.tts_engine or "elevenlabs")
        voice      = request.data.get("voice",      job.tts_voice  or "narrateur_pro")
        langue     = request.data.get("language",   job.language   or "fr")
        segment_ids = request.data.get("segment_ids", None)  # None = tous les segments

        job.tts_engine = tts_engine
        job.tts_voice  = voice
        job.language   = langue
        job.save(update_fields=["tts_engine", "tts_voice", "language"])

        from ..tasks.task_synthesize import task_synthesize

        def _run():
            task_synthesize(str(job.pk), tts_engine, voice, langue, segment_ids=segment_ids)

        threading.Thread(target=_run, daemon=True).start()
        logger.info(f"Synthèse lancée : job={job.pk} segments={segment_ids or 'tous'}")
        return Response({"task_id": "thread", "status": "started", "message": "Synthèse vocale lancée."})


class SetVoiceView(APIView):
    """POST /api/jobs/<job_id>/set-voice/ — Sauvegarde la voix choisie en base."""
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        voice = request.data.get("voice", "narrateur_pro")
        job.tts_voice = voice
        job.save(update_fields=["tts_voice"])
        return Response({"status": "ok", "tts_voice": voice})