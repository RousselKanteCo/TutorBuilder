"""
apps/api/views.py — Vues API REST (DRF).
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


# ─────────────────────────────────────────
#  HEALTH & PROVIDERS
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
#  PROJECTS
# ─────────────────────────────────────────

class ProjectViewSet(viewsets.ModelViewSet):
    serializer_class   = ProjectSerializer
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

    # ── POST /api/jobs/ — Upload vidéo ──
    @extend_schema(request=UploadVideoSerializer, tags=["jobs"],
                   summary="Upload une vidéo et crée un job")
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
        logger.info(f"Job créé : {job.pk} — {job.video_filename}")
        return Response(JobDetailSerializer(job).data, status=status.HTTP_201_CREATED)

    # ── POST /api/jobs/<id>/transcribe/ ──
    @extend_schema(request=TranscribeRequestSerializer, tags=["jobs"],
                   summary="Lance la transcription STT")
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
            task    = task_transcribe.delay(str(job.pk), str(job.video_file.path), job.stt_engine, job.language)
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])

        return Response({"job_id": str(job.pk), "task_id": task_id, "status": "queued",
                         "message": f"Transcription lancée ({job.language})"})

    # ── POST /api/jobs/<id>/recalculate/ — NOUVEAU ──
    @extend_schema(tags=["jobs"], summary="Redistribue les timecodes selon les durées TTS estimées")
    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        """
        Appelé lors de la sauvegarde du script.

        1. Lit les segments actuels en DB (texte édité)
        2. Estime la durée TTS de chaque segment
        3. Redistribue les timecodes pour que durée ≈ TTS
        4. Persiste les nouveaux timecodes en DB
        5. Invalide l'ancien plan de synthèse (synthesis_plan.json)
        6. Retourne les nouveaux timecodes pour mise à jour de l'UI
        """
        import subprocess
        import os
        from apps.studio.tasks import _redistribute_timecodes

        job = self.get_object()
        segments = list(
            job.segments.order_by("index").values("index", "start_ms", "end_ms", "text")
        )
        if not segments:
            return Response({"detail": "Aucun segment à recalculer."}, status=400)

        lang = job.language or "fr"
        new_timecodes = _redistribute_timecodes(segments, lang=lang)

        # Persister en DB
        updated = 0
        for tc in new_timecodes:
            n = Segment.objects.filter(job=job, index=tc["index"]).update(
                start_ms=tc["new_start_ms"],
                end_ms=tc["new_end_ms"],
            )
            updated += n

        # Invalider l'ancien plan de synthèse (les timecodes ont changé)
        plan_path = str(job.output_dir / "synthesis_plan.json")
        if os.path.exists(plan_path):
            try:
                os.remove(plan_path)
            except Exception:
                pass

        logger.info(f"Timecodes redistribués — job {job.pk}, {updated} segments mis à jour.")

        return Response({
            "updated":   updated,
            "segments":  new_timecodes,
            "message":   f"{updated} timecodes recalculés selon la longueur des textes.",
        })

    # ── POST /api/jobs/<id>/synthesize/ ──
    @extend_schema(request=SynthesizeRequestSerializer, tags=["jobs"],
                   summary="Lance la synthèse TTS")
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
            task    = task_synthesize.delay(str(job.pk), segments_data, job.tts_engine, job.tts_voice, job.language)
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])

        return Response({"job_id": str(job.pk), "task_id": task_id, "status": "queued",
                         "message": f"Synthèse lancée ({len(segments_data)} segments)"})

    # ── POST /api/jobs/<id>/export/ ──
    @extend_schema(tags=["jobs"], summary="Lance le montage vidéo final")
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
    @extend_schema(tags=["jobs"], summary="Liste les segments du job")
    @action(detail=True, methods=["get"], url_path="segments")
    def segments(self, request, pk=None):
        job = self.get_object()
        return Response(SegmentSerializer(job.segments.order_by("index"), many=True).data)
    
    # ── POST /api/jobs/<id>/split_segments/ ──
    @extend_schema(tags=["jobs"], summary="Découpe les segments trop longs et redistribue les timecodes")
    @action(detail=True, methods=["post"], url_path="split_segments")
    def split_segments(self, request, pk=None):
        import re
        from apps.studio.tasks import _redistribute_timecodes

        job = self.get_object()
        incoming = request.data.get("segments", [])
        if not incoming:
            return Response({"detail": "Aucun segment fourni."}, status=400)

        MAX_CHARS = 120

        def split_text_smart(text, max_chars=MAX_CHARS):
            text = text.strip()
            if len(text) <= max_chars:
                return [text]
            sentences = re.split(r'(?<=[.!?])\s+', text)
            chunks, current = [], ""
            for sent in sentences:
                if not current:
                    current = sent
                elif len(current) + 1 + len(sent) <= max_chars:
                    current += " " + sent
                else:
                    chunks.append(current)
                    current = sent
            if current:
                chunks.append(current)
            mid = []
            for chunk in chunks:
                if len(chunk) <= max_chars:
                    mid.append(chunk)
                else:
                    parts = re.split(r'(?<=,)\s+', chunk)
                    cur2 = ""
                    for p in parts:
                        if not cur2:
                            cur2 = p
                        elif len(cur2) + 1 + len(p) <= max_chars:
                            cur2 += " " + p
                        else:
                            mid.append(cur2)
                            cur2 = p
                    if cur2:
                        mid.append(cur2)
            result = []
            for chunk in mid:
                while len(chunk) > max_chars:
                    cut = chunk.rfind(' ', 0, max_chars)
                    if cut == -1:
                        cut = max_chars
                    result.append(chunk[:cut].strip())
                    chunk = chunk[cut:].strip()
                if chunk:
                    result.append(chunk)
            return result

        job.segments.all().delete()
        raw_segments = []
        global_index = 0

        for seg_data in incoming:
            orig_start = int(seg_data.get("start_ms", seg_data.get("start", 0)))
            orig_end   = int(seg_data.get("end_ms",   seg_data.get("end", orig_start + 3000)))
            text       = (seg_data.get("text") or "").strip()
            orig_dur   = max(orig_end - orig_start, 100)
            parts      = split_text_smart(text)

            if len(parts) == 1:
                # Pas de découpage — on garde le timecode original exact
                raw_segments.append({
                    "index":    global_index,
                    "start_ms": orig_start,
                    "end_ms":   orig_end,
                    "text":     parts[0],
                })
                global_index += 1
            else:
                # Découpage : on répartit la plage vidéo originale
                # proportionnellement à la longueur des textes
                # → chaque sous-segment pointe vers sa propre portion de vidéo
                total_chars = sum(len(p) for p in parts) or 1
                cursor = orig_start
                for i, part in enumerate(parts):
                    is_last = (i == len(parts) - 1)
                    ratio   = len(part) / total_chars
                    sub_dur = max(int(orig_dur * ratio), 200)
                    sub_end = orig_end if is_last else min(cursor + sub_dur, orig_end)
                    raw_segments.append({
                        "index":    global_index,
                        "start_ms": cursor,   # ← portion de vidéo propre à ce sous-segment
                        "end_ms":   sub_end,
                        "text":     part,
                    })
                    cursor = sub_end
                    global_index += 1

        # Redistribuer les timecodes audio (durée TTS estimée)
        # mais conserver les start_ms/end_ms vidéo originaux dans un champ séparé
        lang = job.language or "fr"
        redistributed = _redistribute_timecodes(raw_segments, lang=lang)

        # Persister : start_ms/end_ms = timecodes VIDÉO originaux (pour l'export)
        # On stocke aussi les timecodes TTS redistribués pour l'audio
        new_segments = []
        for i, tc in enumerate(redistributed):
            raw = raw_segments[i]
            s = Segment.objects.create(
                job=job,
                index=tc["index"],
                # Timecodes vidéo = portion originale proportionnelle
                start_ms=raw["start_ms"],
                end_ms=raw["end_ms"],
                text=tc["text"],
            )
            new_segments.append({
                "id":       str(s.pk),
                "index":    tc["index"],
                "start_ms": raw["start_ms"],   # affiché dans l'UI (timecode vidéo)
                "end_ms":   raw["end_ms"],
                "text":     tc["text"],
            })

        logger.info(f"split_segments — job {job.pk}: {len(incoming)} → {len(new_segments)} segments")
        return Response({
            "original_count": len(incoming),
            "new_count":      len(new_segments),
            "segments":       new_segments,
        })

# ─────────────────────────────────────────
#  SEGMENTS
# ─────────────────────────────────────────

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


# ─────────────────────────────────────────
#  TÂCHES CELERY
# ─────────────────────────────────────────

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