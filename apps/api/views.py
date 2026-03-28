"""
apps/api/views.py — Vues API REST (DRF).

Portage complet des endpoints FastAPI de server.py vers Django REST Framework.

Mapping :
    POST /upload              → JobViewSet.create()
    POST /transcribe/{id}     → JobViewSet.transcribe()
    POST /synthesize/{id}     → JobViewSet.synthesize()
    GET  /status/{task_id}    → TaskStatusView.get()
    DELETE /job/{id}          → JobViewSet.destroy()
    GET  /providers           → ProvidersView.get()
    GET  /health              → HealthView.get()
"""

import logging
from django.conf import settings
from django.shortcuts import get_object_or_404
from django.utils import timezone

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.parsers import MultiPartParser, FormParser
import rest_framework.parsers

from drf_spectacular.utils import extend_schema, OpenApiParameter

from apps.studio.models import Project, Job, Segment
from .serializers import (
    ProjectSerializer,
    JobListSerializer, JobDetailSerializer,
    SegmentSerializer, SegmentUpdateSerializer,
    UploadVideoSerializer,
    TranscribeRequestSerializer,
    SynthesizeRequestSerializer,
    TaskStatusSerializer,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
#  HEALTH & PROVIDERS
# ─────────────────────────────────────────

class HealthView(APIView):
    """
    GET /api/health/
    Vérification que l'API, la DB et les workers Celery fonctionnent.
    """
    permission_classes = [AllowAny]

    @extend_schema(tags=["system"])
    def get(self, request):
        from django.db import connection
        from config.celery import app as celery_app

        try:
            connection.ensure_connection()
            db_ok = True
        except Exception:
            db_ok = False

        try:
            celery_app.control.ping(timeout=2)
            workers_ok = True
        except Exception:
            workers_ok = False

        return Response({
            "api": "ok",
            "db": "ok" if db_ok else "down",
            "workers": "ok" if workers_ok else "down",
            "timestamp": timezone.now().isoformat(),
        }, status=200 if (db_ok and workers_ok) else 503)


class ProvidersView(APIView):
    """
    GET /api/providers/
    Liste les providers STT et TTS disponibles avec leur statut.
    Équivalent de GET /providers dans server.py.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["providers"])
    def get(self, request):
        from stt_providers import STTProviderFactory
        from tts_providers import TTSProviderFactory

        return Response({
            "stt": STTProviderFactory.lister(),
            "tts": TTSProviderFactory.lister(),
        })


# ─────────────────────────────────────────
#  PROJECTS
# ─────────────────────────────────────────

class ProjectViewSet(viewsets.ModelViewSet):
    """CRUD complet pour les projets."""
    serializer_class = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Project.objects.filter(
            owner=self.request.user
        ).prefetch_related("jobs").order_by("-updated_at")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


# ─────────────────────────────────────────
#  JOBS
# ─────────────────────────────────────────

class JobViewSet(viewsets.ModelViewSet):
    """
    ViewSet principal — gère l'upload, la transcription et la synthèse.
    Remplace les endpoints /upload, /transcribe, /synthesize de server.py.
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, rest_framework.parsers.JSONParser]

    def get_queryset(self):
        return Job.objects.filter(
            project__owner=self.request.user
        ).select_related("project").order_by("-created_at")

    def get_serializer_class(self):
        if self.action in ("list",):
            return JobListSerializer
        return JobDetailSerializer

    # ── POST /api/jobs/ — Upload vidéo ──
    @extend_schema(
        request=UploadVideoSerializer,
        tags=["jobs"],
        summary="Upload une vidéo et crée un job",
    )
    def create(self, request):
        """
        Équivalent de POST /upload dans server.py.
        Crée un Job et sauvegarde la vidéo.
        """
        ser = UploadVideoSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        project = get_object_or_404(
            Project,
            pk=ser.validated_data["project_id"],
            owner=request.user,
        )

        job = Job.objects.create(
            project=project,
            video_file=ser.validated_data["video_file"],
            video_filename=ser.validated_data["video_file"].name,
            stt_engine=ser.validated_data["stt_engine"],
            tts_engine=ser.validated_data["tts_engine"],
            language=ser.validated_data["language"],
            status=Job.Status.PENDING,
        )

        # Créer le dossier de sortie
        job.output_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"Job créé : {job.pk} — vidéo : {job.video_filename}")

        return Response(
            JobDetailSerializer(job).data,
            status=status.HTTP_201_CREATED,
        )

    # ── POST /api/jobs/<id>/transcribe/ — Lancer la transcription ──
    @extend_schema(
        request=TranscribeRequestSerializer,
        tags=["jobs"],
        summary="Lance la transcription STT",
    )
    @action(detail=True, methods=["post"], url_path="transcribe")
    def transcribe(self, request, pk=None):
        """
        Équivalent de POST /transcribe/{job_id} dans server.py.
        Lance la tâche Celery de transcription.
        """
        job = self.get_object()

        if job.status not in (Job.Status.PENDING, Job.Status.TRANSCRIBED, Job.Status.ERROR):
            return Response(
                {"detail": f"Job en cours ({job.get_status_display()}), veuillez patienter."},
                status=status.HTTP_409_CONFLICT,
            )

        ser = TranscribeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Mise à jour du moteur si changé
        job.stt_engine = ser.validated_data.get("stt_engine", job.stt_engine)
        job.language = ser.validated_data.get("language", job.language)
        job.save(update_fields=["stt_engine", "language"])

        # Lancer la tâche — directement si ALWAYS_EAGER, sinon via Celery
        from apps.studio.tasks import task_transcribe
        from django.conf import settings as django_settings

        if getattr(django_settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            # Mode dev sans Redis : exécution directe dans le thread Django
            import threading
            job.set_status(Job.Status.EXTRACTING)
            t = threading.Thread(
                target=task_transcribe,
                args=(str(job.pk), str(job.video_file.path), job.stt_engine, job.language),
                daemon=True,
            )
            t.start()
            task_id = "eager-" + str(job.pk)[:8]
        else:
            task = task_transcribe.delay(
                str(job.pk),
                str(job.video_file.path),
                job.stt_engine,
                job.language,
            )
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])

        logger.info(f"Transcription lancée — job {job.pk}, task {task_id}")

        return Response({
            "job_id": str(job.pk),
            "task_id": task_id,
            "status": "queued",
            "message": f"Transcription lancée via {job.get_stt_engine_display()} ({job.language})",
        })

    # ── POST /api/jobs/<id>/synthesize/ — Lancer la synthèse vocale ──
    @extend_schema(
        request=SynthesizeRequestSerializer,
        tags=["jobs"],
        summary="Lance la synthèse TTS",
    )
    @action(detail=True, methods=["post"], url_path="synthesize")
    def synthesize(self, request, pk=None):
        """
        Équivalent de POST /synthesize/{job_id} dans server.py.
        Lance la tâche Celery de synthèse vocale.
        """
        job = self.get_object()

        if job.status not in (Job.Status.TRANSCRIBED, Job.Status.DONE, Job.Status.ERROR):
            return Response(
                {"detail": "Transcription requise avant la synthèse."},
                status=status.HTTP_409_CONFLICT,
            )

        if not job.segments.exists():
            return Response(
                {"detail": "Aucun segment disponible pour la synthèse."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ser = SynthesizeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)

        # Mise à jour de la config TTS
        job.tts_engine = ser.validated_data.get("tts_engine", job.tts_engine)
        job.tts_voice = ser.validated_data.get("voice", job.tts_voice)
        job.language = ser.validated_data.get("language", job.language)
        job.save(update_fields=["tts_engine", "tts_voice", "language"])

        # Préparer les segments pour la tâche
        segments_data = list(job.segments.values("index", "start_ms", "end_ms", "text"))

        from apps.studio.tasks import task_synthesize
        from django.conf import settings as django_settings

        if getattr(django_settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            import threading
            job.set_status(Job.Status.SYNTHESIZING)
            t = threading.Thread(
                target=task_synthesize,
                args=(str(job.pk), segments_data, job.tts_engine, job.tts_voice, job.language),
                daemon=True,
            )
            t.start()
            task_id = "eager-" + str(job.pk)[:8]
        else:
            task = task_synthesize.delay(
                str(job.pk),
                segments_data,
                job.tts_engine,
                job.tts_voice,
                job.language,
            )
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])

        logger.info(f"Synthèse lancée — job {job.pk}, task {task_id}, {len(segments_data)} segments")

        return Response({
            "job_id": str(job.pk),
            "task_id": task_id,
            "status": "queued",
            "message": f"Synthèse lancée via {job.get_tts_engine_display()} ({len(segments_data)} segments)",
        })

    # ── POST /api/jobs/<id>/export/ — Montage final ──
    @extend_schema(tags=["jobs"], summary="Lance le montage vidéo final")
    @action(detail=True, methods=["post"], url_path="export")
    def export(self, request, pk=None):
        """
        Lance la tâche de montage final :
        pose les segments TTS aux bons timecodes et fusionne avec la vidéo source.
        """
        job = self.get_object()

        # Vérifier qu'il y a des segments transcrits (synthèse pas obligatoire)
        if not job.segments.exists():
            return Response(
                {"detail": "Transcription requise avant l'export."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Vérifier qu'on a au moins quelques fichiers audio
        has_audio = job.segments.filter(audio_file__gt="").exists()
        if not has_audio:
            return Response(
                {"detail": "Synthèse vocale requise avant l'export. Aucun fichier audio trouvé."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        from apps.studio.tasks import task_export
        from django.conf import settings as django_settings

        if getattr(django_settings, 'CELERY_TASK_ALWAYS_EAGER', False):
            import threading
            t = threading.Thread(
                target=task_export,
                args=(str(job.pk),),
                daemon=True,
            )
            t.start()
            task_id = "eager-export-" + str(job.pk)[:8]
        else:
            task = task_export.delay(str(job.pk))
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])

        logger.info(f"Export lancé — job {job.pk}")

        return Response({
            "job_id":   str(job.pk),
            "task_id":  task_id,
            "status":   "queued",
            "message":  "Montage vidéo lancé.",
        })


    @extend_schema(tags=["jobs"], summary="Liste les segments du job")
    @action(detail=True, methods=["get"], url_path="segments")
    def segments(self, request, pk=None):
        job = self.get_object()
        serializer = SegmentSerializer(job.segments.order_by("index"), many=True)
        return Response(serializer.data)


# ─────────────────────────────────────────
#  SEGMENTS (modification du script)
# ─────────────────────────────────────────

class SegmentUpdateView(APIView):
    """
    PATCH /api/segments/<id>/
    Modification du texte d'un segment depuis l'éditeur de script.
    Équivalent de la zone QTextEdit editable dans main.py.
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(request=SegmentUpdateSerializer, tags=["segments"])
    def patch(self, request, pk):
        segment = get_object_or_404(
            Segment,
            pk=pk,
            job__project__owner=request.user,
        )
        ser = SegmentUpdateSerializer(segment, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(SegmentSerializer(segment).data)


# ─────────────────────────────────────────
#  TÂCHES CELERY (suivi de progression)
# ─────────────────────────────────────────

class TaskStatusView(APIView):
    """
    GET /api/tasks/<task_id>/
    Équivalent de GET /status/{task_id} dans server.py.
    Polling HTTP de secours (le WebSocket est la voie principale).
    """
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["tasks"])
    def get(self, request, task_id):
        from config.celery import app as celery_app
        result = celery_app.AsyncResult(task_id)

        response = {
            "task_id": task_id,
            "state": result.state,
            "progress": 0,
        }

        if result.state == "PENDING":
            response["progress"] = 0
        elif result.state in ("EXTRACTING_AUDIO", "TRANSCRIBING", "GENERATING"):
            info = result.info or {}
            response["progress"] = info.get("progress", 0)
            response["detail"] = info
        elif result.state == "SUCCESS":
            response["progress"] = 100
            response["result"] = result.result
        elif result.state == "FAILURE":
            response["progress"] = 0
            response["error"] = str(result.result)

        return Response(response)