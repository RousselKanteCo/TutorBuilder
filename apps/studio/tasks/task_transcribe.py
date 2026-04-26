"""
apps/studio/tasks/task_transcribe.py — Tâche transcription.

Flux :
    1. Extraire audio WAV depuis la vidéo
    2. Générer la waveform
    3. Extraire les miniatures (une par segment)
    4. Transcrire avec Faster-Whisper
    5. Créer les Segments en base avec speed_factor calculé
    6. Notifier via WebSocket
"""

import logging
import os
from pathlib import Path
from django.conf import settings

from .utils.audio import (
    extraire_audio_wav,
    extraire_waveform,
    get_video_duration,
    get_video_dimensions,
    extraire_miniature,
)

logger = logging.getLogger(__name__)

# WPM par défaut avant mesure réelle
DEFAULT_WPM    = 145.0
MIN_SEGMENT_S  = 0.3   # durée minimale d'un segment


def calculer_speed_factor(text: str, duration_ms: float, wpm: float) -> float:
    """
    Calcule le facteur de vitesse vidéo pour un segment.

    - Si la voix est plus longue que le segment → ralentir la vidéo (< 1.0)
    - Si la voix est plus courte → accélérer (> 1.0)
    - Texte vide → vitesse selon durée du silence
    """
    if not text or not text.strip():
        duration_s = duration_ms / 1000.0
        if duration_s < 2.0:  return 1.0
        elif duration_s < 5.0: return 2.0
        else: return 4.0  # max x4

    nb_mots       = len(text.strip().split())
    duree_voix_s  = (nb_mots / wpm) * 60.0
    duree_video_s = duration_ms / 1000.0

    if duree_video_s <= 0 or duree_voix_s <= 0:
        return 1.0

    factor = duree_video_s / duree_voix_s
    # Clamper entre 0.25 et 4.0
    return round(max(0.25, min(4.0, factor)), 4)


def ws_send(job_id, msg_type, **kwargs):
    """Envoie un message WebSocket."""
    try:
        from asgiref.sync import async_to_sync
        from channels.layers import get_channel_layer
        cl = get_channel_layer()
        if cl:
            async_to_sync(cl.group_send)(
                f"job_{job_id}", {"type": f"job.{msg_type}", **kwargs}
            )
    except Exception:
        pass



def task_transcribe(job_id: str, stt_engine: str = "faster_whisper", langue: str = "fr"):
    """
    Tâche Celery — transcription d'un job.
    """
    from apps.studio.models import Job, Segment, VoiceProfile

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.EXTRACTING)
    ws_send(job_id, "status", message="Extraction audio en cours…", level="info")

    try:
        video_path = str(job.video_file.path)
        work_dir   = job.output_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        wav_path   = str(job.wav_path)

        # ── 1. Extraire audio ─────────────────────────────────────────────
        if not extraire_audio_wav(video_path, wav_path):
            raise RuntimeError(
                "L'extraction audio a échoué. "
                "Vérifiez que ffmpeg est installé et que la vidéo n'est pas corrompue."
            )
        ws_send(job_id, "status", message="Audio extrait.", level="ok")

        # ── 2. Waveform ───────────────────────────────────────────────────
        waveform = extraire_waveform(wav_path)
        job.waveform_data = waveform
        job.save(update_fields=["waveform_data"])
        ws_send(job_id, "waveform", data=waveform)

        # ── 3. Durée + dimensions vidéo ───────────────────────────────────
        video_duration_s = get_video_duration(video_path)
        video_w, video_h = get_video_dimensions(video_path)

        if video_duration_s > 0:
            job.video_duration_ms = int(video_duration_s * 1000)
            job.save(update_fields=["video_duration_ms"])

        # ── 4. Transcription ──────────────────────────────────────────────
        job.set_status(Job.Status.TRANSCRIBING)
        ws_send(job_id, "status", message="Transcription Faster-Whisper en cours…", level="info")

        from stt_providers import STTProviderFactory
        provider = STTProviderFactory.create("faster_whisper")

        if not provider.est_disponible():
            raise RuntimeError(
                "Faster-Whisper n'est pas disponible. "
                "Exécutez : pip install faster-whisper"
            )

        segments_raw = provider.transcrire(wav_path, langue=langue)

        if not segments_raw:
            raise RuntimeError(
                "Aucun segment transcrit. "
                "La vidéo est peut-être silencieuse ou la langue ne correspond pas."
            )

        # ── 5. Récupérer WPM de la voix choisie ──────────────────────────
        wpm = VoiceProfile.get_wpm(
            voice_id   = job.tts_voice,
            tts_engine = job.tts_engine,
        )

        # ── 6. Créer les segments en base avec silences détectés ──────────
        Segment.objects.filter(job=job).delete()

        miniatures_dir = Path(settings.MEDIA_ROOT) / "jobs" / str(job.pk) / "miniatures"
        miniatures_dir.mkdir(parents=True, exist_ok=True)

        # Construire la liste complète : paroles + silences intercalés
        SILENCE_MIN_MS = 500  # gap minimum pour créer un segment silence
        all_segs = []
        global_idx = 0

        # Intro silence (avant le premier segment)
        if segments_raw:
            first_start = int(segments_raw[0].get("start_ms", segments_raw[0].get("start", 0)))
            if first_start > SILENCE_MIN_MS:
                all_segs.append({
                    "text": "", "start_ms": 0, "end_ms": first_start,
                    "is_silence": True,
                })

        for i, seg in enumerate(segments_raw):
            text     = (seg.get("text") or "").strip()
            start_ms = int(seg.get("start_ms", seg.get("start", 0)))
            end_ms   = int(seg.get("end_ms",   seg.get("end",   0)))
            dur_ms   = end_ms - start_ms

            if dur_ms < MIN_SEGMENT_S * 1000:
                continue

            all_segs.append({
                "text": text, "start_ms": start_ms, "end_ms": end_ms,
                "is_silence": False,
            })

            # Silence entre ce segment et le suivant
            if i + 1 < len(segments_raw):
                next_start = int(segments_raw[i+1].get("start_ms", segments_raw[i+1].get("start", 0)))
                gap_ms     = next_start - end_ms
                if gap_ms > SILENCE_MIN_MS:
                    all_segs.append({
                        "text": "", "start_ms": end_ms, "end_ms": next_start,
                        "is_silence": True,
                    })

        # Outro silence (après le dernier segment)
        if segments_raw and video_duration_s > 0:
            last_end  = int(segments_raw[-1].get("end_ms", segments_raw[-1].get("end", 0)))
            video_end = int(video_duration_s * 1000)
            if video_end - last_end > SILENCE_MIN_MS:
                all_segs.append({
                    "text": "", "start_ms": last_end, "end_ms": video_end,
                    "is_silence": True,
                })

        segments_data = []
        objs          = []

        for i, seg in enumerate(all_segs):
            text     = seg["text"]
            start_ms = seg["start_ms"]
            end_ms   = seg["end_ms"]
            dur_ms   = end_ms - start_ms
            speed    = calculer_speed_factor(text, dur_ms, wpm)

            # Miniature au milieu du segment
            mid_s      = (start_ms + dur_ms / 2) / 1000.0
            thumb_fn   = f"seg_{i:04d}.jpg"
            thumb_path = str(miniatures_dir / thumb_fn)
            thumb_url  = ""

            if extraire_miniature(video_path, mid_s, thumb_path):
                thumb_url = f"/media/jobs/{job.pk}/miniatures/{thumb_fn}"

            objs.append(Segment(
                job          = job,
                index        = i,
                start_ms     = start_ms,
                end_ms       = end_ms,
                text         = text,
                speed_factor = speed,
                speed_forced = False,
            ))

            segments_data.append({
                "id":           None,
                "index":        i,
                "start_ms":     start_ms,
                "end_ms":       end_ms,
                "text":         text,
                "speed_factor": speed,
                "speed_forced": False,
                "is_silence":   seg["is_silence"],
                "thumb_url":    thumb_url,
                "duration_ms":  dur_ms,
            })

        Segment.objects.bulk_create(objs, batch_size=500)

        # Récupérer les IDs créés
        created = Segment.objects.filter(job=job).order_by("index")
        for seg_data, seg_obj in zip(segments_data, created):
            seg_data["id"] = str(seg_obj.pk)

        # ── 7. Finaliser ──────────────────────────────────────────────────
        job.set_status(Job.Status.TRANSCRIBED)
        ws_send(job_id, "segments", data=segments_data)
        ws_send(job_id, "status",
                message=f"{len(objs)} segments transcrits.", level="ok")

        logger.info(f"Transcription OK : job={job_id} segments={len(objs)}")
        return {
            "status":      "success",
            "nb_segments": len(objs),
        }

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur transcription {job_id} : {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        return {"status": "error", "message": msg}