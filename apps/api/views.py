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
#  HELPERS — DÉTECTION DE SILENCES DANS L'AUDIO SOURCE
# ═══════════════════════════════════════════════════════════════════════════════

def detect_silences(wav_path: str, noise_db: float = -35, min_duration: float = 0.25) -> list[dict]:
    """
    Détecte les silences dans le fichier WAV source via ffmpeg.
    Retourne une liste de {start_s, end_s, mid_s} pour chaque silence trouvé.
    Ces points de silence sont les meilleurs endroits pour couper entre les segments.
    """
    cmd = [
        "ffmpeg", "-i", wav_path,
        "-af", f"silencedetect=noise={noise_db}dB:d={min_duration}",
        "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    output = result.stderr

    silences = []
    starts = []
    for line in output.split("\n"):
        if "silence_start:" in line:
            try:
                t = float(line.split("silence_start:")[1].strip())
                starts.append(t)
            except ValueError:
                pass
        elif "silence_end:" in line and starts:
            try:
                parts = line.split("silence_end:")[1].strip().split("|")
                end_t = float(parts[0].strip())
                silences.append({
                    "start_s": starts[-1],
                    "end_s":   end_t,
                    "mid_s":   (starts[-1] + end_t) / 2,
                })
            except (ValueError, IndexError):
                pass

    return silences


def find_best_cut_point(silences: list[dict], target_s: float,
                        search_window_s: float = 3.0) -> float | None:
    """
    Trouve le silence le plus proche d'un timecode cible dans une fenêtre donnée.
    C'est ici qu'on coupe proprement entre deux segments.
    """
    candidates = [
        s for s in silences
        if abs(s["mid_s"] - target_s) <= search_window_s
    ]
    if not candidates:
        return None
    best = min(candidates, key=lambda s: abs(s["mid_s"] - target_s))
    return best["end_s"]  # on coupe à la FIN du silence (début de la parole suivante)


def estimate_speech_duration_ms(text: str, lang: str = "fr") -> float:
    """Estime la durée de parole d'un texte en ms."""
    SPEECH_RATE = {
        "fr": 13.5, "en": 15.5, "es": 15.0, "de": 12.0,
        "it": 14.5, "pt": 14.5, "default": 14.0,
    }
    clean     = re.sub(r"\s+", " ", (text or "").strip())
    effective = len(re.sub(r"[^\w\s]", "", clean))
    rate      = SPEECH_RATE.get((lang or "fr").lower()[:2], SPEECH_RATE["default"])
    base_ms   = (effective / rate) * 1000.0
    pauses    = (
        clean.count(".") * 260 + clean.count("?") * 260 +
        clean.count("!") * 260 + clean.count(",") * 110 +
        clean.count(";") * 180 + clean.count(":") * 140
    )
    return max(400.0, base_ms + pauses)


def smart_split_text(text: str, max_chars: int = 120) -> list[str]:
    """
    Découpe un texte long en segments cohérents.
    Respecte les fins de phrase, puis les virgules, puis les espaces.
    Ne découpe PAS si le texte est court (< max_chars).
    """
    text = text.strip()
    if len(text) <= max_chars:
        return [text]

    # 1. Essayer de couper aux fins de phrase
    sentences = re.split(r'(?<=[.!?])\s+', text)
    chunks, current = [], ""
    for sent in sentences:
        test = (current + " " + sent).strip() if current else sent
        if len(test) <= max_chars:
            current = test
        else:
            if current:
                chunks.append(current)
            current = sent
    if current:
        chunks.append(current)

    # 2. Pour les chunks encore trop longs, couper aux virgules
    result = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            result.append(chunk)
            continue
        parts = re.split(r'(?<=,)\s+', chunk)
        cur2 = ""
        for p in parts:
            test2 = (cur2 + " " + p).strip() if cur2 else p
            if len(test2) <= max_chars:
                cur2 = test2
            else:
                if cur2:
                    result.append(cur2)
                cur2 = p
        if cur2:
            result.append(cur2)

    # 3. Dernier recours : couper aux espaces
    final = []
    for chunk in result:
        while len(chunk) > max_chars:
            cut = chunk.rfind(" ", 0, max_chars)
            if cut == -1:
                cut = max_chars
            final.append(chunk[:cut].strip())
            chunk = chunk[cut:].strip()
        if chunk:
            final.append(chunk)

    return final if final else [text]


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
            task    = task_transcribe.delay(
                str(job.pk), str(job.video_file.path), job.stt_engine, job.language
            )
            task_id = task.id

        job.celery_task_id = task_id
        job.save(update_fields=["celery_task_id"])

        return Response({"job_id": str(job.pk), "task_id": task_id, "status": "queued",
                         "message": f"Transcription lancée ({job.language})"})

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
            task    = task_synthesize.delay(
                str(job.pk), segments_data, job.tts_engine, job.tts_voice, job.language
            )
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

    # ── POST /api/jobs/<id>/split_segments/ ──────────────────────────────────
    #
    #  LOGIQUE INTELLIGENTE v2 :
    #
    #  Quand l'utilisateur édite/fusionne/découpe des segments :
    #  1. On détecte les vrais silences dans l'audio source
    #  2. Pour chaque segment (éventuellement découpé), on cherche
    #     le silence naturel le plus proche dans la vidéo pour
    #     aligner le timecode de fin
    #  3. Résultat : les coupures vidéo tombent aux vraies pauses,
    #     pas au milieu d'une phrase
    #
    @extend_schema(tags=["jobs"], summary="Sauvegarde les segments édités avec alignement intelligent")
    @action(detail=True, methods=["post"], url_path="split_segments")
    def split_segments(self, request, pk=None):
        job      = self.get_object()
        incoming = request.data.get("segments", [])
        if not incoming:
            return Response({"detail": "Aucun segment fourni."}, status=400)

        lang = job.language or "fr"

        # ── 1. Charger les silences de l'audio source ─────────────────────
        wav_path = str(job.wav_path) if hasattr(job, "wav_path") else None
        silences = []
        if wav_path and os.path.exists(wav_path):
            try:
                silences = detect_silences(wav_path, noise_db=-35, min_duration=0.2)
                logger.info(f"split_segments — {len(silences)} silences détectés dans l'audio")
            except Exception as e:
                logger.warning(f"Détection silences échouée : {e}")

        # ── 2. Traiter chaque segment entrant ─────────────────────────────
        job.segments.all().delete()
        raw_segments = []
        global_index = 0

        for seg_data in incoming:
            orig_start_ms = int(seg_data.get("start_ms", seg_data.get("start", 0)))
            orig_end_ms   = int(seg_data.get("end_ms",   seg_data.get("end", orig_start_ms + 3000)))
            text          = (seg_data.get("text") or "").strip()
            orig_dur_ms   = max(orig_end_ms - orig_start_ms, 200)

            # Découper le texte si trop long (> 120 chars)
            parts = smart_split_text(text, max_chars=120)

            if len(parts) == 1:
                # ── Segment non découpé ──
                # Aligner la FIN sur le silence naturel le plus proche
                aligned_end_ms = orig_end_ms
                if silences:
                    cut = find_best_cut_point(
                        silences,
                        target_s=orig_end_ms / 1000.0,
                        search_window_s=2.0,
                    )
                    if cut is not None:
                        aligned_end_ms = int(cut * 1000)

                raw_segments.append({
                    "index":    global_index,
                    "start_ms": orig_start_ms,
                    "end_ms":   aligned_end_ms,
                    "text":     parts[0],
                })
                global_index += 1

            else:
                # ── Segment découpé en plusieurs parties ──
                # Répartir proportionnellement à la durée TTS estimée,
                # puis aligner chaque coupure sur un silence naturel
                est_durations = [estimate_speech_duration_ms(p, lang) for p in parts]
                total_est_ms  = sum(est_durations) or 1
                cursor_ms     = orig_start_ms

                for i, (part, est_ms) in enumerate(zip(parts, est_durations)):
                    is_last = (i == len(parts) - 1)

                    if is_last:
                        sub_end_ms = orig_end_ms
                    else:
                        # Timecode de fin idéal basé sur la proportion de durée TTS
                        ideal_end_ms = orig_start_ms + int(
                            orig_dur_ms * sum(est_durations[:i+1]) / total_est_ms
                        )
                        # Chercher un silence naturel autour de ce point
                        if silences:
                            cut = find_best_cut_point(
                                silences,
                                target_s=ideal_end_ms / 1000.0,
                                search_window_s=1.5,
                            )
                            sub_end_ms = int(cut * 1000) if cut else ideal_end_ms
                        else:
                            sub_end_ms = ideal_end_ms

                        # S'assurer qu'on ne dépasse pas la fin du segment parent
                        sub_end_ms = min(sub_end_ms, orig_end_ms - 100)
                        sub_end_ms = max(sub_end_ms, cursor_ms + 200)

                    raw_segments.append({
                        "index":    global_index,
                        "start_ms": cursor_ms,
                        "end_ms":   sub_end_ms,
                        "text":     part,
                    })
                    cursor_ms = sub_end_ms
                    global_index += 1

        # ── 3. Vérifier la cohérence : pas de chevauchements ──────────────
        for i in range(len(raw_segments) - 1):
            cur  = raw_segments[i]
            nxt  = raw_segments[i + 1]
            # S'assurer que le suivant commence bien après la fin du précédent
            if nxt["start_ms"] < cur["end_ms"]:
                nxt["start_ms"] = cur["end_ms"]
            # Gap minimum de 50ms entre deux segments
            if nxt["start_ms"] - cur["end_ms"] < 50:
                nxt["start_ms"] = cur["end_ms"] + 50

        # ── 4. Persister en base ───────────────────────────────────────────
        new_segments = []
        for raw in raw_segments:
            s = Segment.objects.create(
                job=job,
                index=raw["index"],
                start_ms=raw["start_ms"],
                end_ms=raw["end_ms"],
                text=raw["text"],
            )
            new_segments.append({
                "id":       str(s.pk),
                "index":    raw["index"],
                "start_ms": raw["start_ms"],
                "end_ms":   raw["end_ms"],
                "text":     raw["text"],
            })

        logger.info(
            f"split_segments v2 — job {job.pk}: "
            f"{len(incoming)} → {len(new_segments)} segments, "
            f"{len(silences)} silences utilisés"
        )

        return Response({
            "original_count": len(incoming),
            "new_count":      len(new_segments),
            "segments":       new_segments,
            "silences_used":  len(silences),
        })

    # ── POST /api/jobs/<id>/recalculate/ ──
    @extend_schema(tags=["jobs"], summary="Redistribue les timecodes selon les durées TTS estimées")
    @action(detail=True, methods=["post"], url_path="recalculate")
    def recalculate(self, request, pk=None):
        from apps.studio.tasks import _redistribute_timecodes

        job = self.get_object()
        segments = list(
            job.segments.order_by("index").values("index", "start_ms", "end_ms", "text")
        )
        if not segments:
            return Response({"detail": "Aucun segment à recalculer."}, status=400)

        lang = job.language or "fr"
        new_timecodes = _redistribute_timecodes(segments, lang=lang)

        updated = 0
        for tc in new_timecodes:
            n = Segment.objects.filter(job=job, index=tc["index"]).update(
                start_ms=tc["new_start_ms"],
                end_ms=tc["new_end_ms"],
            )
            updated += n

        plan_path = str(job.output_dir / "synthesis_plan.json")
        if os.path.exists(plan_path):
            try:
                os.remove(plan_path)
            except Exception:
                pass

        return Response({
            "updated":  updated,
            "segments": new_timecodes,
            "message":  f"{updated} timecodes recalculés.",
        })
    
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