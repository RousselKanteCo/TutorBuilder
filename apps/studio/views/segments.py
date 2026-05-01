"""
apps/studio/views/segments.py — Endpoints segments.
"""

import logging
import os
from django.http import FileResponse
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from ..models import Job, Segment

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
#  LISTE DES SEGMENTS
# ─────────────────────────────────────────

class SegmentListView(APIView):
    """
    GET /api/jobs/<job_id>/segments/
    Retourne tous les segments actifs d'un job (is_deleted=False).
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        # FIX : exclure les segments supprimés — ils ne doivent plus apparaître
        # dans la timeline après un rechargement de page
        segments = Segment.objects.filter(job=job, is_deleted=False).order_by("index")

        data = []
        for seg in segments:
            from django.conf import settings
            from pathlib import Path
            thumb_url = ""
            miniature_path = Path(settings.MEDIA_ROOT) / "jobs" / str(job.pk) / "miniatures" / f"seg_{seg.index:04d}.jpg"
            if miniature_path.exists():
                thumb_url = f"/media/jobs/{job.pk}/miniatures/seg_{seg.index:04d}.jpg"

            data.append({
                "id":                    seg.pk,
                "index":                 seg.index,
                "start_ms":              seg.start_ms,
                "end_ms":                seg.end_ms,
                "trim_start_ms":         seg.trim_start_ms,
                "trim_end_ms":           seg.trim_end_ms if seg.trim_end_ms > 0 else seg.end_ms,
                "text":                  seg.text,
                "speed_factor":          seg.speed_factor,
                "speed_forced":          seg.speed_forced,
                "thumb_url":             thumb_url,
                "duration_ms":           seg.end_ms - seg.start_ms,
                "effective_duration_ms": seg.effective_duration_ms,
                "has_audio":             bool(seg.audio_file and os.path.exists(str(seg.audio_file))),
                "is_deleted":            seg.is_deleted,
            })

        return Response(data)


# ─────────────────────────────────────────
#  SAUVEGARDER UN SEGMENT
# ─────────────────────────────────────────

class SegmentSaveView(APIView):
    """
    POST /api/jobs/<job_id>/segments/<seg_id>/save/
    Sauvegarde un segment individuel.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id, seg_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
            seg = Segment.objects.get(pk=seg_id, job=job)
        except (Job.DoesNotExist, Segment.DoesNotExist):
            return Response({"error": "Segment introuvable."}, status=status.HTTP_404_NOT_FOUND)

        seg.text          = request.data.get("text",         seg.text)
        seg.speed_factor  = float(request.data.get("speed_factor", seg.speed_factor))
        seg.speed_forced  = bool(request.data.get("speed_forced",  seg.speed_forced))
        seg.start_ms      = int(request.data.get("start_ms",  seg.start_ms))
        seg.end_ms        = int(request.data.get("end_ms",    seg.end_ms))
        # FIX : sauvegarder aussi les trims
        seg.trim_start_ms = int(request.data.get("trim_start_ms", seg.trim_start_ms or 0))
        seg.trim_end_ms   = int(request.data.get("trim_end_ms",   seg.trim_end_ms or 0))

        seg.save(update_fields=[
            "text", "speed_factor", "speed_forced",
            "start_ms", "end_ms",
            "trim_start_ms", "trim_end_ms",
        ])

        logger.info(f"Segment {seg.index} sauvegardé — job={job_id}")
        return Response({"status": "ok", "id": seg.pk})


# ─────────────────────────────────────────
#  SAUVEGARDER TOUS LES SEGMENTS
# ─────────────────────────────────────────

class SegmentSaveAllView(APIView):
    """
    POST /api/jobs/<job_id>/segments/save-all/
    Sauvegarde tous les segments actifs envoyés par le frontend.

    FIX : les segments absents de la liste reçue sont marqués is_deleted=True
    au lieu d'être supprimés physiquement. Cela préserve l'historique et
    permet à l'export de les ignorer proprement via _is_deleted().

    Pourquoi is_deleted plutôt que .delete() ?
      - saveAllSegments() côté JS filtre les .deleted → ils sont simplement
        absents de la payload, pas explicitement signalés
      - Un .delete() physique rendait l'opération irréversible (pas d'undo)
      - is_deleted=True est lu par task_export._is_deleted() à l'assemblage
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        segments_data = request.data.get("segments", [])
        if not segments_data:
            return Response({"error": "Aucun segment fourni."}, status=status.HTTP_400_BAD_REQUEST)

        # IDs des segments actifs envoyés par le JS
        ids_recus = set()
        for seg_data in segments_data:
            sid = seg_data.get("id")
            if sid:
                ids_recus.add(str(sid))

        # ── 1. Mettre à jour les segments reçus ──────────────────────────
        updated = 0
        errors  = []

        for seg_data in segments_data:
            seg_id = seg_data.get("id")
            if not seg_id:
                continue
            try:
                seg = Segment.objects.get(pk=seg_id, job=job)
                seg.text          = seg_data.get("text",         seg.text)
                seg.index         = int(seg_data.get("index",        seg.index))
                seg.speed_factor  = float(seg_data.get("speed_factor", seg.speed_factor))
                seg.speed_forced  = bool(seg_data.get("speed_forced",  seg.speed_forced))
                seg.start_ms      = int(seg_data.get("start_ms",  seg.start_ms))
                seg.end_ms        = int(seg_data.get("end_ms",    seg.end_ms))
                seg.trim_start_ms = int(seg_data.get("trim_start_ms", seg.trim_start_ms or 0))
                seg.trim_end_ms   = int(seg_data.get("trim_end_ms",   seg.trim_end_ms or 0))
                seg.is_deleted    = False  # explicitement actif
                seg.save(update_fields=[
                    "text", "index", "speed_factor", "speed_forced",
                    "start_ms", "end_ms", "trim_start_ms", "trim_end_ms",
                    "is_deleted",
                ])
                updated += 1
            except Segment.DoesNotExist:
                errors.append(f"Segment {seg_id} introuvable.")
            except Exception as e:
                errors.append(f"Segment {seg_id} : {e}")

        # ── 2. Marquer is_deleted les segments absents ───────────────────
        # Les segments supprimés côté JS ne sont jamais inclus dans la payload.
        # Leur absence signifie qu'ils ont été supprimés ou fusionnés.
        deleted_count = (
            Segment.objects
            .filter(job=job)
            .exclude(pk__in=ids_recus)
            .update(is_deleted=True)
        )

        if deleted_count:
            logger.info(
                f"save-all job={job_id} : {deleted_count} segment(s) → is_deleted=True"
            )

        logger.info(f"Sauvegarde globale : {updated} segments — job={job_id}")
        return Response({
            "status":         "ok",
            "updated":        updated,
            "deleted_marked": deleted_count,
            "errors":         errors,
        })


# ─────────────────────────────────────────
#  IMPORT SCRIPT
# ─────────────────────────────────────────

class SegmentImportScriptView(APIView):
    """
    POST /api/jobs/<job_id>/segments/import-script/
    Importe un script texte pour remplacer les textes des segments.
    """
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        file = request.FILES.get("script_file")
        if not file:
            return Response({"error": "Aucun fichier fourni."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            content = file.read().decode("utf-8")
        except Exception:
            return Response(
                {"error": "Impossible de lire le fichier. Vérifiez qu'il est en UTF-8."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        blocs = self._parse_script(content)

        # Uniquement les segments actifs
        segments = list(Segment.objects.filter(job=job, is_deleted=False).order_by("index"))

        if len(blocs) != len(segments):
            return Response({
                "error": (
                    f"Le fichier contient {len(blocs)} blocs de texte "
                    f"mais le job a {len(segments)} segments actifs. "
                    f"Le nombre doit être identique pour importer."
                ),
                "nb_blocs":    len(blocs),
                "nb_segments": len(segments),
            }, status=status.HTTP_400_BAD_REQUEST)

        from ..models import VoiceProfile
        wpm = VoiceProfile.get_wpm(job.tts_voice, job.tts_engine)

        for seg, texte in zip(segments, blocs):
            seg.text = texte.strip()
            if not seg.speed_forced:
                from ..tasks.task_transcribe import calculer_speed_factor
                seg.speed_factor = calculer_speed_factor(
                    seg.text, seg.end_ms - seg.start_ms, wpm
                )

        Segment.objects.bulk_update(segments, ["text", "speed_factor"], batch_size=200)

        logger.info(f"Script importé : {len(segments)} segments — job={job_id}")

        return Response({
            "status":   "ok",
            "updated":  len(segments),
            "segments": [{
                "id":           s.pk,
                "index":        s.index,
                "start_ms":     s.start_ms,
                "end_ms":       s.end_ms,
                "text":         s.text,
                "speed_factor": s.speed_factor,
                "speed_forced": s.speed_forced,
            } for s in segments],
        })

    def _parse_script(self, content: str) -> list:
        content = content.strip()
        if "-->" in content:
            blocs = []
            for bloc in content.split("\n\n"):
                lignes = [l.strip() for l in bloc.strip().split("\n")]
                texte_lignes = [
                    l for l in lignes
                    if l and not l.isdigit() and "-->" not in l
                ]
                if texte_lignes:
                    blocs.append(" ".join(texte_lignes))
            return blocs
        blocs = [b.strip() for b in content.split("\n\n") if b.strip()]
        if len(blocs) > 1:
            return blocs
        return [l.strip() for l in content.split("\n") if l.strip()]


# ─────────────────────────────────────────
#  ÉCOUTER L'AUDIO D'UN SEGMENT
# ─────────────────────────────────────────

class SegmentAudioView(APIView):
    """
    GET /api/jobs/<job_id>/segments/<seg_id>/audio/
    Retourne le fichier WAV TTS du segment.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id, seg_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
            seg = Segment.objects.get(pk=seg_id, job=job)
        except (Job.DoesNotExist, Segment.DoesNotExist):
            return Response({"error": "Segment introuvable."}, status=status.HTTP_404_NOT_FOUND)

        if not seg.audio_file or not os.path.exists(str(seg.audio_file)):
            return Response(
                {"error": "Aucun fichier audio pour ce segment."},
                status=status.HTTP_404_NOT_FOUND,
            )

        return FileResponse(
            open(str(seg.audio_file), 'rb'),
            content_type='audio/wav',
            as_attachment=False,
            filename=os.path.basename(str(seg.audio_file)),
        )


# ─────────────────────────────────────────
#  SET TRIM — IN/OUT d'un segment
# ─────────────────────────────────────────

class SegmentSetTrimView(APIView):
    """POST /api/jobs/<job_id>/segments/<idx>/set-trim/"""
    permission_classes = [IsAuthenticated]

    def post(self, request, job_id, segment_idx):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
            seg = Segment.objects.get(job=job, index=segment_idx)
        except (Job.DoesNotExist, Segment.DoesNotExist):
            return Response({"error": "Introuvable."}, status=status.HTTP_404_NOT_FOUND)

        trim_start = int(request.data.get("trim_start_ms", seg.trim_start_ms))
        trim_end   = int(request.data.get("trim_end_ms",   seg.trim_end_ms))

        trim_start = max(seg.start_ms, min(trim_start, seg.end_ms))
        trim_end   = max(seg.start_ms, min(trim_end,   seg.end_ms))

        if trim_start >= trim_end:
            return Response({"error": "trim_start doit être avant trim_end."}, status=400)

        seg.trim_start_ms = trim_start
        seg.trim_end_ms   = trim_end
        seg.save(update_fields=["trim_start_ms", "trim_end_ms"])

        logger.info(f"Trim seg {segment_idx} : {trim_start}ms → {trim_end}ms — job={job_id}")

        return Response({
            "status":                "ok",
            "trim_start_ms":         seg.trim_start_ms,
            "trim_end_ms":           seg.trim_end_ms,
            "effective_duration_ms": seg.effective_duration_ms,
        })