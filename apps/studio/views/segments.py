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
    Retourne tous les segments d'un job avec thumb_url.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        segments = Segment.objects.filter(job=job).order_by("index")

        data = []
        for seg in segments:
            # Construire thumb_url depuis audio_file path pattern
            from django.conf import settings
            from pathlib import Path
            thumb_url = ""
            miniature_path = Path(settings.MEDIA_ROOT) / "jobs" / str(job.pk) / "miniatures" / f"seg_{seg.index:04d}.jpg"
            if miniature_path.exists():
                thumb_url = f"/media/jobs/{job.pk}/miniatures/seg_{seg.index:04d}.jpg"

            data.append({
                "id":           seg.pk,
                "index":        seg.index,
                "start_ms":     seg.start_ms,
                "end_ms":       seg.end_ms,
                "text":         seg.text,
                "speed_factor": seg.speed_factor,
                "speed_forced": seg.speed_forced,
                "thumb_url":    thumb_url,
                "duration_ms":  seg.end_ms - seg.start_ms,
                "has_audio":    bool(seg.audio_file and os.path.exists(str(seg.audio_file))),
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

        text         = request.data.get("text", seg.text)
        speed_factor = request.data.get("speed_factor", seg.speed_factor)
        speed_forced = request.data.get("speed_forced", seg.speed_forced)
        start_ms     = request.data.get("start_ms", seg.start_ms)
        end_ms       = request.data.get("end_ms", seg.end_ms)

        seg.text         = text
        seg.speed_factor = float(speed_factor)
        seg.speed_forced = bool(speed_forced)
        seg.start_ms     = int(start_ms)
        seg.end_ms       = int(end_ms)
        seg.save(update_fields=["text", "speed_factor", "speed_forced", "start_ms", "end_ms"])

        logger.info(f"Segment {seg.index} sauvegardé — job={job_id}")
        return Response({"status": "ok", "id": seg.pk})


# ─────────────────────────────────────────
#  SAUVEGARDER TOUS LES SEGMENTS
# ─────────────────────────────────────────

class SegmentSaveAllView(APIView):
    """
    POST /api/jobs/<job_id>/segments/save-all/
    Sauvegarde tous les segments — remplace complètement la liste en base.
    Les segments absents de la liste envoyée sont supprimés (fusion, suppression).
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

        # IDs envoyés par le front
        ids_front = set()
        for seg_data in segments_data:
            sid = seg_data.get("id")
            if sid:
                ids_front.add(str(sid))

        # Supprimer les segments absents du front (fusionnés ou supprimés)
        tous = Segment.objects.filter(job=job)
        for seg in tous:
            if str(seg.pk) not in ids_front:
                seg.delete()
                logger.info(f"Segment {seg.pk} supprimé (fusion/suppression)")

        # Mettre à jour les segments présents
        updated = 0
        errors  = []

        for seg_data in segments_data:
            seg_id = seg_data.get("id")
            if not seg_id:
                continue
            try:
                seg = Segment.objects.get(pk=seg_id, job=job)
                seg.text         = seg_data.get("text", seg.text)
                seg.index        = int(seg_data.get("index", seg.index))
                seg.speed_factor = float(seg_data.get("speed_factor", seg.speed_factor))
                seg.speed_forced = bool(seg_data.get("speed_forced", seg.speed_forced))
                seg.start_ms     = int(seg_data.get("start_ms", seg.start_ms))
                seg.end_ms       = int(seg_data.get("end_ms", seg.end_ms))
                seg.save(update_fields=["text", "index", "speed_factor", "speed_forced", "start_ms", "end_ms"])
                updated += 1
            except Segment.DoesNotExist:
                errors.append(f"Segment {seg_id} introuvable.")
            except Exception as e:
                errors.append(f"Segment {seg_id} : {e}")

        logger.info(f"Sauvegarde globale : {updated} segments — job={job_id}")
        return Response({
            "status":  "ok",
            "updated": updated,
            "errors":  errors,
        })


# ─────────────────────────────────────────
#  IMPORT SCRIPT
# ─────────────────────────────────────────

class SegmentImportScriptView(APIView):
    """
    POST /api/jobs/<job_id>/segments/import-script/
    Importe un script texte pour remplacer les textes des segments.

    Règle stricte : le nombre de blocs du fichier doit correspondre
    exactement au nombre de segments — sinon refus.
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

        # Lire le fichier
        try:
            content = file.read().decode("utf-8")
        except Exception:
            return Response(
                {"error": "Impossible de lire le fichier. Vérifiez qu'il est en UTF-8."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Détecter le format et parser
        blocs = self._parse_script(content)

        # Récupérer les segments existants
        segments = list(Segment.objects.filter(job=job).order_by("index"))

        if len(blocs) != len(segments):
            return Response({
                "error": (
                    f"Le fichier contient {len(blocs)} blocs de texte "
                    f"mais le job a {len(segments)} segments. "
                    f"Le nombre doit être identique pour importer."
                ),
                "nb_blocs":    len(blocs),
                "nb_segments": len(segments),
            }, status=status.HTTP_400_BAD_REQUEST)

        # Appliquer les textes — timings inchangés
        from ..models import VoiceProfile
        wpm = VoiceProfile.get_wpm(job.tts_voice, job.tts_engine)

        for seg, texte in zip(segments, blocs):
            seg.text = texte.strip()
            # Recalculer speed_factor si pas forcé
            if not seg.speed_forced:
                from ..tasks.task_transcribe import calculer_speed_factor
                seg.speed_factor = calculer_speed_factor(
                    seg.text, seg.end_ms - seg.start_ms, wpm
                )

        Segment.objects.bulk_update(
            segments,
            ["text", "speed_factor"],
            batch_size=200,
        )

        logger.info(f"Script importé : {len(segments)} segments — job={job_id}")

        # Retourner les segments mis à jour
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
        """
        Parse le fichier texte en blocs.
        Supporte :
          - Format SRT (numéro + timecode + texte)
          - Format paragraphes (séparés par lignes vides)
          - Format ligne par ligne
        """
        content = content.strip()

        # Format SRT
        if "-->" in content:
            blocs = []
            for bloc in content.split("\n\n"):
                lignes = [l.strip() for l in bloc.strip().split("\n")]
                # Ignorer numéro et timecode
                texte_lignes = [
                    l for l in lignes
                    if l and not l.isdigit() and "-->" not in l
                ]
                if texte_lignes:
                    blocs.append(" ".join(texte_lignes))
            return blocs

        # Format paragraphes
        blocs = [b.strip() for b in content.split("\n\n") if b.strip()]
        if len(blocs) > 1:
            return blocs

        # Format ligne par ligne
        lignes = [l.strip() for l in content.split("\n") if l.strip()]
        return lignes


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