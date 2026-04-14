"""
apps/api/views.py — Vues API REST (DRF).
"""

import os
import re
import logging
import subprocess
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

from drf_spectacular.utils import extend_schema

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


# ═══════════════════════════════════════════════════════════════════════════════
#  HEALTH & PROVIDERS
# ═══════════════════════════════════════════════════════════════════════════════

class HealthView(APIView):
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
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["providers"])
    def get(self, request):
        from stt_providers import STTProviderFactory
        from tts_providers import TTSProviderFactory
        return Response({"stt": STTProviderFactory.lister(), "tts": TTSProviderFactory.lister()})


# ═══════════════════════════════════════════════════════════════════════════════
#  PROJECTS
# ═══════════════════════════════════════════════════════════════════════════════

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class   = ProjectSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Project.objects.filter(
            owner=self.request.user
        ).prefetch_related("jobs").order_by("-updated_at")

    def perform_create(self, serializer):
        serializer.save(owner=self.request.user)


# ═══════════════════════════════════════════════════════════════════════════════
#  JOBS
# ═══════════════════════════════════════════════════════════════════════════════

class JobViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser, rest_framework.parsers.JSONParser]

    def get_queryset(self):
        return Job.objects.filter(
            project__owner=self.request.user
        ).select_related("project").order_by("-created_at")

    def get_serializer_class(self):
        if self.action in ("list",):
            return JobListSerializer
        return JobDetailSerializer

    # ── POST /api/jobs/ ──
    @extend_schema(request=UploadVideoSerializer, tags=["jobs"])
    def create(self, request):
        ser = UploadVideoSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        project = get_object_or_404(
            Project, pk=ser.validated_data["project_id"], owner=request.user,
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
        job.output_dir.mkdir(parents=True, exist_ok=True)
        return Response(JobDetailSerializer(job).data, status=status.HTTP_201_CREATED)

    # ── POST /api/jobs/<id>/transcribe/ ──
    @extend_schema(request=TranscribeRequestSerializer, tags=["jobs"])
    @action(detail=True, methods=["post"], url_path="transcribe")
    def transcribe(self, request, pk=None):
        job = self.get_object()
        if job.status not in (Job.Status.PENDING, Job.Status.TRANSCRIBED, Job.Status.ERROR):
            return Response(
                {"detail": f"Job en cours ({job.get_status_display()})."},
                status=status.HTTP_409_CONFLICT,
            )
        ser = TranscribeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        job.stt_engine = ser.validated_data.get("stt_engine", job.stt_engine)
        job.language   = ser.validated_data.get("language", job.language)
        job.save(update_fields=["stt_engine", "language"])

        from apps.studio.tasks import task_transcribe
        from django.conf import settings as djs

        if getattr(djs, "CELERY_TASK_ALWAYS_EAGER", False):
            import threading
            job.set_status(Job.Status.EXTRACTING)
            threading.Thread(
                target=task_transcribe,
                args=(str(job.pk), str(job.video_file.path), job.stt_engine, job.language),
                daemon=True,
            ).start()
            task_id = "eager-" + str(job.pk)[:8]
        else:
            task    = task_transcribe.delay(
                str(job.pk), str(job.video_file.path), job.stt_engine, job.language
            )
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])
        return Response({"job_id": str(job.pk), "task_id": task_id, "status": "queued"})

    # ── POST /api/jobs/<id>/synthesize/ ──
    @extend_schema(request=SynthesizeRequestSerializer, tags=["jobs"])
    @action(detail=True, methods=["post"], url_path="synthesize")
    def synthesize(self, request, pk=None):
        job = self.get_object()
        if job.status not in (Job.Status.TRANSCRIBED, Job.Status.DONE, Job.Status.ERROR):
            return Response(
                {"detail": "Transcription requise avant la synthèse."},
                status=status.HTTP_409_CONFLICT,
            )
        if not job.segments.exists():
            return Response({"detail": "Aucun segment disponible."}, status=400)

        ser = SynthesizeRequestSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        job.tts_engine = ser.validated_data.get("tts_engine", job.tts_engine)
        job.tts_voice  = ser.validated_data.get("voice", job.tts_voice)
        job.language   = ser.validated_data.get("language", job.language)
        job.save(update_fields=["tts_engine", "tts_voice", "language"])

        segments_data = list(job.segments.values("index", "start_ms", "end_ms", "text"))

        from apps.studio.tasks import task_synthesize
        from django.conf import settings as djs

        if getattr(djs, "CELERY_TASK_ALWAYS_EAGER", False):
            import threading
            job.set_status(Job.Status.SYNTHESIZING)
            threading.Thread(
                target=task_synthesize,
                args=(str(job.pk), segments_data, job.tts_engine, job.tts_voice, job.language),
                daemon=True,
            ).start()
            task_id = "eager-" + str(job.pk)[:8]
        else:
            task    = task_synthesize.delay(
                str(job.pk), segments_data, job.tts_engine, job.tts_voice, job.language
            )
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])
        return Response({"job_id": str(job.pk), "task_id": task_id, "status": "queued",
                         "message": f"Synthèse lancée ({len(segments_data)} segments)"})

    # ── POST /api/jobs/<id>/export/ ──
    @extend_schema(tags=["jobs"])
    @action(detail=True, methods=["post"], url_path="export")
    def export(self, request, pk=None):
        job = self.get_object()
        if not job.segments.exists():
            return Response({"detail": "Transcription requise."}, status=400)
        if not job.segments.filter(audio_file__gt="").exists():
            return Response({"detail": "Synthèse vocale requise."}, status=400)

        subtitle_style = request.data.get("subtitle_style", {})

        from apps.studio.tasks import task_export
        from django.conf import settings as djs

        if getattr(djs, "CELERY_TASK_ALWAYS_EAGER", False):
            import threading
            threading.Thread(
                target=task_export,
                args=(str(job.pk),),
                kwargs={"subtitle_style": subtitle_style},
                daemon=True,
            ).start()
            task_id = "eager-export-" + str(job.pk)[:8]
        else:
            task    = task_export.delay(str(job.pk), subtitle_style=subtitle_style)
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])
        return Response({"job_id": str(job.pk), "task_id": task_id, "status": "queued",
                         "message": "Montage vidéo lancé."})

    # ── GET /api/jobs/<id>/segments/ ──
    @extend_schema(tags=["jobs"])
    @action(detail=True, methods=["get"], url_path="segments")
    def segments(self, request, pk=None):
        job = self.get_object()
        return Response(SegmentSerializer(job.segments.order_by("index"), many=True).data)

    # ── POST /api/jobs/<id>/split_segments/ ──────────────────────────────
    #
    #  LOGIQUE v9 :
    #   - L'user ne change QUE le texte — timecodes originaux intouchables
    #   - Si un segment a < 3 mots → fusionner avec le précédent en base
    #     et redécouper les timecodes proportionnellement aux longueurs de texte
    #
    #  Exemple :
    #   Seg N   : [00:10 → 00:15] "...cette adresse"   (17 chars)
    #   Seg N+1 : [00:15 → 00:16] "demandée."          (10 chars, 1 mot)
    #
    #   Durée totale = 6s
    #   Cut = 00:10 + 6s * 17/27 = 00:13.8
    #   Résultat :
    #     Seg N   : [00:10 → 00:13.8] "...cette adresse"
    #     Seg N+1 : [00:13.8 → 00:16] "demandée."
    #   → TTS génère 2 fichiers séparés, naturels, sans répétition !
    #
    @extend_schema(tags=["jobs"])
    @action(detail=True, methods=["post"], url_path="split_segments")
    def split_segments(self, request, pk=None):
        job      = self.get_object()
        incoming = request.data.get("segments", [])
        if not incoming:
            return Response({"detail": "Aucun segment fourni."}, status=400)

        MIN_WORDS = 3

        # ── 1. Récupérer les timecodes originaux depuis la base ───────────
        original_tc = {
            s.index: {"start_ms": s.start_ms, "end_ms": s.end_ms}
            for s in job.segments.all()
        }

        # ── 2. Construire la liste avec timecodes originaux ───────────────
        segments_work = []
        for seg_data in incoming:
            idx  = seg_data.get("index", 0)
            text = (seg_data.get("text") or "").strip()
            orig = original_tc.get(idx, {
                "start_ms": seg_data.get("start_ms", 0),
                "end_ms":   seg_data.get("end_ms", 3000),
            })
            segments_work.append({
                "index":    idx,
                "start_ms": orig["start_ms"],
                "end_ms":   orig["end_ms"],
                "text":     text,
            })

        # ── 3. Redécouper les segments courts ────────────────────────────
        #
        # Si seg[i] a < MIN_WORDS mots ET qu'il y a un précédent :
        #   - Durée totale = start[i-1] → end[i]
        #   - Coupure proportionnelle aux longueurs de texte
        #
        for i in range(1, len(segments_work)):
            texte = segments_work[i]["text"]
            mots  = [m for m in texte.split() if m]

            if len(mots) < MIN_WORDS and segments_work[i-1]["text"].strip():
                start_total  = segments_work[i-1]["start_ms"]
                end_total    = segments_work[i]["end_ms"]
                duree_totale = max(end_total - start_total, 200)

                len_prev  = max(len(segments_work[i-1]["text"]), 1)
                len_cur   = max(len(texte), 1)
                len_total = len_prev + len_cur

                cut_ms = start_total + int(duree_totale * len_prev / len_total)
                cut_ms = max(start_total + 100, min(cut_ms, end_total - 100))

                segments_work[i-1]["end_ms"] = cut_ms
                segments_work[i]["start_ms"] = cut_ms

                logger.info(
                    f"split_segments: seg {segments_work[i]['index']} court "
                    f"({len(mots)} mots) → redécoupage "
                    f"[{start_total}→{cut_ms}ms | {cut_ms}→{end_total}ms]"
                )

        # ── 4. Persister en base ──────────────────────────────────────────
        job.segments.all().delete()
        new_segments = []
        for i, seg in enumerate(segments_work):
            if not seg["text"]:
                continue
            s = Segment.objects.create(
                job=job,
                index=i,
                start_ms=seg["start_ms"],
                end_ms=seg["end_ms"],
                text=seg["text"],
            )
            new_segments.append({
                "id":       str(s.pk),
                "index":    i,
                "start_ms": seg["start_ms"],
                "end_ms":   seg["end_ms"],
                "text":     seg["text"],
            })

        logger.info(f"split_segments v9 — job {job.pk}: {len(new_segments)} segments")

        return Response({
            "original_count": len(incoming),
            "new_count":      len(new_segments),
            "segments":       new_segments,
            "silences_used":  0,
        })

    # ── POST /api/jobs/<id>/reset/ ──
    @action(detail=True, methods=["post"], url_path="reset")
    def reset(self, request, pk=None):
        from pathlib import Path
        from django.conf import settings
        import shutil

        job  = self.get_object()
        step = int(request.data.get("step", 2))

        if step == 2:
            job.set_status(Job.Status.TRANSCRIBED)
            tts_dir = job.output_dir / "tts"
            if tts_dir.exists():
                shutil.rmtree(str(tts_dir))
            plan = job.output_dir / "synthesis_plan.json"
            if plan.exists():
                plan.unlink()
            # Supprimer aussi la vidéo finale
            exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
            if exports_dir.exists():
                shutil.rmtree(str(exports_dir))

        elif step == 3:
            job.set_status(Job.Status.DONE)
            exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
            if exports_dir.exists():
                shutil.rmtree(str(exports_dir))
            for f in ["assembled.mp4", "composite.wav", "subtitles.ass"]:
                fp = job.output_dir / f
                if fp.exists():
                    fp.unlink()

        elif step == 1:
            job.set_status(Job.Status.PENDING)

        return Response({"status": "ok", "new_status": job.status})


# ═══════════════════════════════════════════════════════════════════════════════
#  SEGMENTS
# ═══════════════════════════════════════════════════════════════════════════════

class SegmentUpdateView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(request=SegmentUpdateSerializer, tags=["segments"])
    def patch(self, request, pk):
        segment = get_object_or_404(
            Segment, pk=pk, job__project__owner=request.user,
        )
        ser = SegmentUpdateSerializer(segment, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        return Response(SegmentSerializer(segment).data)


# ═══════════════════════════════════════════════════════════════════════════════
#  TÂCHES CELERY
# ═══════════════════════════════════════════════════════════════════════════════

class TaskStatusView(APIView):
    permission_classes = [IsAuthenticated]

    @extend_schema(tags=["tasks"])
    def get(self, request, task_id):
        from config.celery import app as celery_app
        result   = celery_app.AsyncResult(task_id)
        response = {"task_id": task_id, "state": result.state, "progress": 0}

        if result.state == "PENDING":
            response["progress"] = 0
        elif result.state in ("EXTRACTING_AUDIO", "TRANSCRIBING", "GENERATING"):
            info = result.info or {}
            response["progress"] = info.get("progress", 0)
            response["detail"]   = info
        elif result.state == "SUCCESS":
            response["progress"] = 100
            response["result"]   = result.result
        elif result.state == "FAILURE":
            response["progress"] = 0
            response["error"]    = str(result.result)

        return Response(response)