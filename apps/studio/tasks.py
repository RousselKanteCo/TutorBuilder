"""
apps/studio/tasks.py — Tâches Celery pour TutoBuilder Vision.

v5 — Sous-titres ASS natifs ffmpeg (qualité pro), export sans blanc,
     synchronisation image/voix/texte parfaite.
"""

import os
import re
import json
import math
import logging
from pathlib import Path
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
#  HELPERS WEBSOCKET
# ─────────────────────────────────────────

def ws_send(job_id: str, message_type: str, **kwargs):
    try:
        channel_layer = get_channel_layer()
        if channel_layer is None:
            return
        async_to_sync(channel_layer.group_send)(
            f"job_{job_id}",
            {"type": f"job.{message_type}", **kwargs},
        )
    except Exception:
        pass


def ws_status(job_id: str, message: str):
    ws_send(job_id, "status", message=message)
    logger.info(f"[{job_id}] {message}")


def ws_progress(job_id: str, step: int, total: int, label: str):
    percent = int((step / total) * 100)
    ws_send(job_id, "progress", step=step, total=total, label=label, percent=percent)


# ═══════════════════════════════════════════════════════════════════════════════
#  TIMING INTELLIGENCE
# ═══════════════════════════════════════════════════════════════════════════════

_SPEECH_RATE: dict = {
    "fr": 13.5, "en": 15.5, "es": 15.0, "de": 12.0,
    "it": 14.5, "pt": 14.5, "nl": 13.5, "ja": 8.0,
    "zh": 7.5,  "ar": 12.0, "ru": 13.0, "default": 14.0,
}

MAX_VIDEO_RATIO  = 3.2
MIN_VIDEO_RATIO  = 0.5
SUBTITLE_GAP_MS  = 80
SUBTITLE_MAX_LINES = 3
SUBTITLE_MAX_CHARS = 44
MIN_GAP_MS       = 80.0


def _speech_rate(lang: str) -> float:
    return _SPEECH_RATE.get((lang or "fr").lower()[:2], _SPEECH_RATE["default"])


def _estimate_tts_ms(text: str, lang: str = "fr") -> float:
    if not text:
        return 400.0
    clean     = re.sub(r"\s+", " ", text.strip())
    effective = len(re.sub(r"[^\w\s]", "", clean))
    rate      = _speech_rate(lang)
    base_ms   = (effective / rate) * 1000.0
    pauses    = (
        clean.count(".") * 260 + clean.count("?") * 260 +
        clean.count("!") * 260 + clean.count(",") * 110 +
        clean.count(";") * 180 + clean.count(":") * 140
    )
    return max(380.0, base_ms + pauses)


def _chunk_for_subtitle(text: str) -> str:
    words, lines, current = text.split(), [], ""
    for word in words:
        test = (current + " " + word).strip() if current else word
        if len(test) <= SUBTITLE_MAX_CHARS:
            current = test
        else:
            if current:
                lines.append(current)
                if len(lines) >= SUBTITLE_MAX_LINES:
                    return "\n".join(lines)
            current = word
    if current and len(lines) < SUBTITLE_MAX_LINES:
        lines.append(current)
    return "\n".join(lines)


def _redistribute_timecodes(segments_data: list, lang: str = "fr") -> list:
    if not segments_data:
        return []

    segs = sorted(
        [{
            "index":    int(s.get("index", i)),
            "start_ms": float(s.get("start_ms", s.get("start", 0))),
            "end_ms":   float(s.get("end_ms", s.get("end", 0))),
            "text":     (s.get("text") or "").strip(),
        } for i, s in enumerate(segments_data)],
        key=lambda x: x["start_ms"],
    )

    gaps = []
    for i in range(len(segs) - 1):
        gap = segs[i + 1]["start_ms"] - segs[i]["end_ms"]
        gaps.append(max(MIN_GAP_MS, gap))

    estimates = [_estimate_tts_ms(s["text"], lang) for s in segs]

    results = []
    cursor  = segs[0]["start_ms"]

    for i, (seg, est_ms) in enumerate(zip(segs, estimates)):
        new_start = cursor
        new_end   = cursor + est_ms

        results.append({
            "index":         seg["index"],
            "new_start_ms":  int(round(new_start)),
            "new_end_ms":    int(round(new_end)),
            "est_tts_ms":    round(est_ms, 1),
            "text":          seg["text"],
            "subtitle_text": _chunk_for_subtitle(seg["text"]),
        })

        cursor = new_end + (gaps[i] if i < len(gaps) else 0)

    return results


def _antioverlap_pass(items: list) -> None:
    items_sorted = sorted(
        [it for it in items if "new_start_ms" in it],
        key=lambda x: x["new_start_ms"],
    )
    for i, item in enumerate(items_sorted):
        tts_ms = item.get("actual_tts_ms", 0)
        if i + 1 < len(items_sorted):
            available = items_sorted[i + 1]["new_start_ms"] - item["new_start_ms"] - SUBTITLE_GAP_MS
            sub_dur   = max(300.0, min(tts_ms, available))
        else:
            sub_dur = tts_ms
        item["sub_end_ms"] = item["new_start_ms"] + sub_dur


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS SOUS-TITRES ASS
# ═══════════════════════════════════════════════════════════════════════════════

def _hex_to_ass(hex_color: str) -> str:
    """Convertit RRGGBB → BBGGRR (format couleur ASS/SSA inversé)."""
    h = hex_color.lstrip("#").upper()
    if len(h) == 6:
        return h[4:6] + h[2:4] + h[0:2]
    return "FFFFFF"


def _ms_to_ass(ms: float) -> str:
    """Convertit millisecondes → H:MM:SS.CC (format timestamp ASS)."""
    ms = int(max(0, ms))
    h  = ms // 3_600_000; ms %= 3_600_000
    m  = ms // 60_000;    ms %= 60_000
    s  = ms // 1_000;     ms %= 1_000
    cs = ms // 10
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _generate_ass(items_timeline: list, video_w: int, video_h: int,
                  style: dict, output_path: str) -> bool:
    """
    Génère un fichier ASS complet depuis la timeline de segments.
    Qualité pro : rendu natif ffmpeg, zéro overlap, styles riches.
    """
    try:
        # ── Paramètres de style ───────────────────────────────────────────
        font_map = {
            "calibri": "Calibri", "arial": "Arial", "segoe": "Segoe UI",
            "georgia": "Georgia", "impact": "Impact", "courier": "Courier New",
        }
        font_name   = font_map.get(style.get("font_family", "calibri"), "Arial")
        font_size   = int(style.get("font_size", 48))
        primary_clr = _hex_to_ass(style.get("text_color", "FFFFFF"))
        outline_clr = _hex_to_ass(style.get("outline_color", "000000"))
        bg_clr      = _hex_to_ass(style.get("bg_color", "000000"))
        outline_w   = int(style.get("outline_width", 2))
        shadow_on   = bool(style.get("shadow", True))
        shadow_d    = 2 if shadow_on else 0
        bg_on       = bool(style.get("bg_enabled", True))
        bg_opacity  = int(style.get("bg_opacity", 75))
        # ASS alpha : 00=opaque, FF=transparent (inversé)
        bg_alpha    = hex(max(0, 255 - int(bg_opacity * 2.55)))[2:].upper().zfill(2)
        position    = style.get("position", "bottom")
        margin_v    = int(style.get("margin", 60))

        # Alignment ASS : 2=bas-centre, 8=haut-centre, 5=milieu-centre
        alignment    = {"bottom": 2, "top": 8, "center": 5}.get(position, 2)
        # BorderStyle : 1=contour+ombre, 3=boîte de fond opaque
        border_style = 3 if bg_on else 1
        back_color   = f"&H{bg_alpha}{bg_clr}&"
        # Outline visible seulement si pas de fond
        ow_actual    = outline_w if not bg_on else 0

        header = (
            f"[Script Info]\n"
            f"ScriptType: v4.00+\n"
            f"PlayResX: {video_w}\n"
            f"PlayResY: {video_h}\n"
            f"ScaledBorderAndShadow: yes\n"
            f"WrapStyle: 0\n\n"
            f"[V4+ Styles]\n"
            f"Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            f"OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            f"ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            f"Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,{font_name},{font_size},"
            f"&H00{primary_clr}&,&H000000FF&,"
            f"&H00{outline_clr}&,{back_color},"
            f"1,0,0,0,100,100,0,0,"
            f"{border_style},{ow_actual},{shadow_d},"
            f"{alignment},10,10,{margin_v},1\n\n"
            f"[Events]\n"
            f"Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )

        events = []
        sorted_items = sorted(
            [it for it in items_timeline if "new_start_ms" in it],
            key=lambda x: x["new_start_ms"],
        )

        for item in sorted_items:
            sub_text = item.get("sub_text", "").strip()
            if not sub_text:
                continue
            start_t  = _ms_to_ass(item["new_start_ms"])
            end_t    = _ms_to_ass(item.get("sub_end_ms", item["new_start_ms"] + item.get("actual_tts", 2000)))
            # \N = saut de ligne ASS
            text_ass = sub_text.replace("\n", "\\N")
            events.append(f"Dialogue: 0,{start_t},{end_t},Default,,0,0,0,,{text_ass}")

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(events))

        logger.info(f"ASS généré : {len(events)} dialogues → {output_path}")
        return True

    except Exception as e:
        logger.error(f"Erreur génération ASS : {e}", exc_info=True)
        return False


# ─────────────────────────────────────────
#  TÂCHE 1 : TRANSCRIPTION
# ─────────────────────────────────────────

@shared_task(bind=True, name="studio.transcribe")
def task_transcribe(self, job_id: str, video_path: str,
                    stt_engine: str = "faster_whisper", langue: str = "fr"):
    from apps.studio.models import Job, Segment
    from stt_providers import STTProviderFactory, extraire_audio_wav

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        ws_status(job_id, "🔊 Extraction audio en cours...")
        ws_progress(job_id, 1, 4, "Extraction audio")
        job.set_status(Job.Status.EXTRACTING)

        wav_path = str(job.wav_path)
        if extraire_audio_wav(video_path, output_wav=wav_path) is None:
            raise RuntimeError("ffmpeg introuvable ou échec extraction.")
        ws_status(job_id, "✅ Audio extrait.")

        ws_status(job_id, "📊 Génération de la waveform...")
        ws_progress(job_id, 2, 4, "Waveform")
        waveform = _extraire_waveform(wav_path)
        job.waveform_data = waveform
        job.save(update_fields=["waveform_data"])
        ws_send(job_id, "waveform", data=waveform)

        ws_status(job_id, "🖼️ Extraction des vignettes...")
        ws_progress(job_id, 3, 4, "Vignettes")
        thumbs = _extraire_vignettes(video_path, job)
        if thumbs:
            job.thumbnail_paths = thumbs
            job.save(update_fields=["thumbnail_paths"])

        ws_status(job_id, f"🧠 Transcription via {stt_engine}...")
        ws_progress(job_id, 4, 4, f"Transcription ({stt_engine})")
        job.set_status(Job.Status.TRANSCRIBING)

        provider_kwargs = {}
        if stt_engine == "vosk":
            provider_kwargs["model_lang"] = langue
            provider_kwargs["model_path"] = str(
                settings.MODELS_ROOT / f"vosk-model-small-{langue}-0.22"
            )

        provider = STTProviderFactory.create(stt_engine, **provider_kwargs)
        if provider is None or not provider.est_disponible():
            raise RuntimeError(f"Provider STT '{stt_engine}' non disponible.")

        segments_raw = provider.transcrire(wav_path, langue=langue)
        if not segments_raw:
            raise RuntimeError("Aucun segment transcrit.")

        nb = Segment.bulk_create_from_stt(job, segments_raw)
        job.set_status(Job.Status.TRANSCRIBED)
        ws_send(job_id, "segments", data=segments_raw)
        ws_status(job_id, f"✅ Transcription terminée — {nb} segments via {provider.nom}.")
        return {"status": "success", "job_id": job_id, "engine": stt_engine, "nb_segments": nb}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur transcription job {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ Erreur : {error_msg}")
        return {"status": "error", "message": error_msg}


# ─────────────────────────────────────────
#  TÂCHE 2 : SYNTHÈSE VOCALE
# ─────────────────────────────────────────

@shared_task(bind=True, name="studio.synthesize")
def task_synthesize(self, job_id: str, segments_data: list,
                    tts_engine: str = "elevenlabs", voix: str = "narrateur_pro",
                    langue: str = "fr"):
    import wave
    from apps.studio.models import Job, Segment
    from tts_providers import TTSProviderFactory

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.SYNTHESIZING)
    job.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        provider_kwargs = {}
        if tts_engine == "elevenlabs":
            provider_kwargs["api_key"] = getattr(settings, "ELEVENLABS_API_KEY", "")

        provider = TTSProviderFactory.create(tts_engine, **provider_kwargs)
        if provider is None or not provider.est_disponible():
            ws_status(job_id, f"⚠️ Provider '{tts_engine}' indisponible — fallback pyttsx3...")
            provider = TTSProviderFactory.create("pyttsx3")
            if provider and provider.est_disponible():
                tts_engine = "pyttsx3"
                ws_status(job_id, "✅ Fallback pyttsx3 activé.")
            else:
                raise RuntimeError("Aucun provider TTS disponible.")

        output_dir = str(job.output_dir / "tts")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        db_segments = list(
            Segment.objects.filter(job=job).order_by("index").values(
                "index", "start_ms", "end_ms", "text"
            )
        )
        if not db_segments:
            raise RuntimeError("Aucun segment en base.")

        total = len(db_segments)
        ws_status(job_id, f"🎙️ Synthèse via {provider.nom} — {total} segments...")

        plan_items = []
        chemins    = []

        for i, seg in enumerate(db_segments):
            texte = (seg["text"] or "").strip()
            if not texte:
                chemins.append(None)
                continue

            ws_send(job_id, "tts_progress", current=i + 1, total=total,
                    message=f"Segment {i + 1}/{total}…")

            filename = f"seg_{seg['index']:04d}.wav"
            try:
                chemin = provider.generer(
                    texte=texte, voix=voix, output_dir=output_dir,
                    filename=filename, langue=langue,
                )
                if chemin and os.path.exists(chemin):
                    with wave.open(chemin, "rb") as wf:
                        actual_ms = wf.getnframes() / wf.getframerate() * 1000.0

                    Segment.objects.filter(job=job, index=seg["index"]).update(audio_file=chemin)

                    plan_items.append({
                        "index":         seg["index"],
                        "db_start_ms":   seg["start_ms"],
                        "db_end_ms":     seg["end_ms"],
                        "actual_tts_ms": round(actual_ms, 1),
                        "tts_path":      chemin,
                        "subtitle_text": _chunk_for_subtitle(texte),
                    })
                    chemins.append(chemin)
                else:
                    chemins.append(None)
            except Exception as e:
                logger.warning(f"TTS segment {i} échoué : {e}")
                chemins.append(None)

        plan_path = str(job.output_dir / "synthesis_plan.json")
        try:
            with open(plan_path, "w", encoding="utf-8") as f:
                json.dump({"job_id": str(job_id), "langue": langue, "plan": plan_items},
                          f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"Plan JSON non sauvegardé : {e}")

        nb_ok = sum(1 for c in chemins if c is not None)
        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done", files=chemins, nb_ok=nb_ok, nb_total=total)
        ws_status(job_id, f"✅ Synthèse terminée — {nb_ok}/{total} segments générés.")
        return {"status": "success", "job_id": job_id, "engine": tts_engine,
                "nb_generated": nb_ok, "nb_total": total}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur synthèse job {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ Erreur : {error_msg}")
        return {"status": "error", "message": error_msg}


# ─────────────────────────────────────────
#  TÂCHE 3 : EXPORT FINAL v5
# ─────────────────────────────────────────

@shared_task(bind=True, name="studio.export")
def task_export(self, job_id: str, subtitle_style: dict = None):
    """
    Export v5 — Synchronisation parfaite image/voix/texte.

    - Chaque clip vidéo est étiré/compressé pour durer exactement actual_tts_ms
    - Clips concaténés sans gap → zéro blanc
    - Audio TTS positionné sur la timeline de sortie réelle
    - Sous-titres ASS natifs ffmpeg (qualité pro, zéro overlap)
    - Option désactivation sous-titres via style["enabled"]
    """
    import subprocess
    import wave
    import shutil
    import numpy as np
    from django.conf import settings as djsettings
    from apps.studio.models import Job, Segment

    style = subtitle_style or {}
    subtitles_enabled = style.get("enabled", True)

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.EXTRACTING)
    ws_status(job_id, "🎬 Démarrage export v5...")

    try:
        video_path  = str(job.video_file.path)
        work_dir    = job.output_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        parts_dir   = work_dir / "parts"; parts_dir.mkdir(parents=True, exist_ok=True)
        exports_dir = Path(djsettings.MEDIA_ROOT) / "exports" / str(job.pk)
        exports_dir.mkdir(parents=True, exist_ok=True)
        final_path  = str(exports_dir / "final.mp4")

        # ── 1. Dimensions vidéo ───────────────────────────────────────────
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
            capture_output=True, text=True,
        )
        video_w, video_h = 1920, 1080
        if probe.returncode == 0 and probe.stdout.strip():
            wh = probe.stdout.strip().split(",")
            try: video_w, video_h = int(wh[0]), int(wh[1])
            except ValueError: pass
        ws_status(job_id, f"📹 Résolution : {video_w}×{video_h}")

        # ── 2. Charger plan JSON ──────────────────────────────────────────
        plan_path = str(job.output_dir / "synthesis_plan.json")
        plan_map  = {}
        if os.path.exists(plan_path):
            try:
                with open(plan_path, "r", encoding="utf-8") as f:
                    plan_data = json.load(f)
                for item in plan_data.get("plan", []):
                    tts_path = item.get("tts_path", "")
                    if tts_path and os.path.exists(tts_path):
                        with wave.open(tts_path, "rb") as wf:
                            actual_ms = wf.getnframes() / wf.getframerate() * 1000.0
                        plan_map[item["index"]] = {
                            "actual_tts_ms": actual_ms,
                            "tts_path":      tts_path,
                            "subtitle_text": item.get("subtitle_text", ""),
                        }
            except Exception as e:
                logger.warning(f"Plan JSON invalide : {e}")

        # ── 3. Segments DB ────────────────────────────────────────────────
        segments_db = list(
            Segment.objects.filter(job=job).exclude(audio_file="").order_by("index")
        )
        if not segments_db:
            raise RuntimeError("Aucun segment audio. Lancez d'abord la synthèse.")

        items = []
        for seg in segments_db:
            if seg.index in plan_map:
                tts_path  = plan_map[seg.index]["tts_path"]
                actual_ms = plan_map[seg.index]["actual_tts_ms"]
                sub_text  = plan_map[seg.index]["subtitle_text"] or _chunk_for_subtitle(seg.text)
            elif seg.audio_file and os.path.exists(seg.audio_file):
                tts_path = seg.audio_file
                try:
                    with wave.open(tts_path, "rb") as wf:
                        actual_ms = wf.getnframes() / wf.getframerate() * 1000.0
                except Exception:
                    continue
                sub_text = _chunk_for_subtitle(seg.text)
            else:
                continue

            if actual_ms < 80:
                continue

            slot_ms = float(seg.end_ms) - float(seg.start_ms)
            if slot_ms <= 0:
                slot_ms = actual_ms

            items.append({
                "index":      seg.index,
                "db_start":   float(seg.start_ms),
                "db_end":     float(seg.end_ms),
                "slot_ms":    slot_ms,
                "actual_tts": actual_ms,
                "tts_path":   tts_path,
                "sub_text":   sub_text,
            })

        if not items:
            raise RuntimeError("Aucun item valide.")

        items.sort(key=lambda x: x["db_start"])
        ws_status(job_id, f"✅ {len(items)} segments chargés.")

        # ── 4. Extraire + étirer chaque clip (image = voix) ───────────────
        MAX_RATIO = 3.0
        MIN_RATIO = 0.4

        part_files     = []
        timeline_ms    = 0.0
        items_timeline = []

        total = len(items)
        ws_status(job_id, f"✂️ Synchronisation de {total} segments...")

        for i, item in enumerate(items):
            ws_progress(job_id, i + 1, total, f"Segment {i+1}/{total}")

            start_s  = item["db_start"] / 1000.0
            slot_s   = item["slot_ms"]  / 1000.0
            tts_ms   = item["actual_tts"]

            # ratio : combien étirer la vidéo pour qu'elle dure tts_ms
            ratio = tts_ms / item["slot_ms"]
            ratio = max(MIN_RATIO, min(MAX_RATIO, ratio))

            part_path = str(parts_dir / f"part_{i:04d}.mp4")

            if abs(ratio - 1.0) < 0.03:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{start_s:.3f}", "-t", f"{slot_s:.3f}",
                    "-i", video_path,
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-an", part_path,
                ]
            else:
                cmd = [
                    "ffmpeg", "-y",
                    "-ss", f"{start_s:.3f}", "-t", f"{slot_s:.3f}",
                    "-i", video_path,
                    "-filter:v", f"setpts={ratio:.6f}*PTS",
                    "-c:v", "libx264", "-preset", "fast", "-crf", "18",
                    "-an", part_path,
                ]

            result = subprocess.run(cmd, capture_output=True, text=True)
            if result.returncode != 0 or not os.path.exists(part_path):
                logger.warning(f"Clip {i} échoué : {result.stderr[-200:]}")
                continue

            # Mesurer durée réelle du clip
            probe_dur = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration",
                 "-of", "default=noprint_wrappers=1:nokey=1", part_path],
                capture_output=True, text=True,
            )
            clip_real_ms = (
                float(probe_dur.stdout.strip()) * 1000
                if probe_dur.returncode == 0 and probe_dur.stdout.strip()
                else slot_s * ratio * 1000
            )

            item["new_start_ms"] = timeline_ms
            item["new_end_ms"]   = timeline_ms + tts_ms
            item["clip_dur_ms"]  = clip_real_ms
            item["sub_end_ms"]   = timeline_ms + tts_ms - 50  # légère marge avant le suivant
            item["actual_tts_ms"] = tts_ms  # pour _antioverlap_pass

            part_files.append(part_path)
            items_timeline.append(item)
            timeline_ms += clip_real_ms

        if not part_files:
            raise RuntimeError("Aucune partie vidéo extraite.")

        ws_status(job_id, f"✅ {len(part_files)} clips — durée totale : {timeline_ms/1000:.1f}s")

        # ── 5. Anti-overlap sous-titres ───────────────────────────────────
        _antioverlap_pass(items_timeline)

        # ── 6. Concaténation vidéo sans gap ──────────────────────────────
        ws_status(job_id, "🔗 Assemblage vidéo...")
        concat_list = str(parts_dir / "concat.txt")
        with open(concat_list, "w", encoding="utf-8") as f:
            for p in part_files:
                f.write(f"file '{p}'\n")

        assembled_path = str(work_dir / "assembled.mp4")
        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0",
             "-i", concat_list,
             "-c:v", "libx264", "-preset", "fast", "-crf", "18",
             assembled_path],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not os.path.exists(assembled_path):
            raise RuntimeError(f"Concat échoué : {result.stderr[-300:]}")
        ws_status(job_id, "✅ Vidéo assemblée — zéro blanc.")

        # ── 7. Audio composite ────────────────────────────────────────────
        ws_status(job_id, "🎵 Mixage audio...")

        first_tts = items_timeline[0]["tts_path"]
        with wave.open(first_tts, "rb") as wf:
            sample_rate  = wf.getframerate()
            n_channels   = wf.getnchannels()
            sample_width = wf.getsampwidth()

        total_audio_ms = timeline_ms + 1000
        total_samples  = int(total_audio_ms * sample_rate / 1000) * n_channels
        dtype  = np.int16 if sample_width == 2 else np.int32
        buffer = np.zeros(total_samples, dtype=dtype)

        mixed = 0
        for item in items_timeline:
            try:
                with wave.open(item["tts_path"], "rb") as wf:
                    if wf.getframerate() != sample_rate:
                        continue
                    raw       = wf.readframes(wf.getnframes())
                    seg_array = np.frombuffer(raw, dtype=dtype).copy()

                if not len(seg_array):
                    continue

                ss = int(item["new_start_ms"] * sample_rate / 1000) * n_channels
                es = ss + len(seg_array)
                if ss >= len(buffer):
                    continue
                if es > len(buffer):
                    seg_array = seg_array[:len(buffer) - ss]
                    es = len(buffer)

                buffer[ss:es] = np.clip(
                    buffer[ss:es].astype(np.int32) + seg_array.astype(np.int32),
                    np.iinfo(dtype).min, np.iinfo(dtype).max,
                ).astype(dtype)
                mixed += 1
            except Exception as e:
                logger.warning(f"Audio ignoré seg {item['index']} : {e}")

        if int(np.max(np.abs(buffer))) == 0:
            raise RuntimeError("Buffer audio silencieux.")

        composite_path = str(work_dir / "composite.wav")
        with wave.open(composite_path, "wb") as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(buffer.tobytes())
        ws_status(job_id, f"✅ {mixed} segments audio mixés.")

        # ── 8. Sous-titres ASS (méthode pro) ─────────────────────────────
        ass_path = None
        if subtitles_enabled:
            ws_status(job_id, "🎨 Génération sous-titres ASS...")
            ass_path = str(work_dir / "subtitles.ass")
            ok = _generate_ass(items_timeline, video_w, video_h, style, ass_path)
            if ok:
                ws_status(job_id, f"✅ Sous-titres ASS générés.")
            else:
                ass_path = None
                ws_status(job_id, "⚠️ Sous-titres ignorés (erreur génération ASS).")
        else:
            ws_status(job_id, "ℹ️ Sous-titres désactivés par l'utilisateur.")

        # ── 9. Fusion finale ──────────────────────────────────────────────
        ws_status(job_id, "⚙️ Encodage final HD...")

        if ass_path and os.path.exists(ass_path):
            # Escape du chemin pour le filtre ffmpeg (Linux + Windows)
            ass_escaped = ass_path.replace("\\", "/").replace(":", "\\:")
            cmd = [
                "ffmpeg", "-y",
                "-i", assembled_path,
                "-i", composite_path,
                "-filter_complex", f"[0:v]ass='{ass_escaped}'[vout]",
                "-map", "[vout]",
                "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", final_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y",
                "-i", assembled_path,
                "-i", composite_path,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k",
                "-shortest", final_path,
            ]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not os.path.exists(final_path):
            ws_status(job_id, "⚠️ Fallback sans sous-titres...")
            result = subprocess.run([
                "ffmpeg", "-y",
                "-i", assembled_path, "-i", composite_path,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", final_path,
            ], capture_output=True, text=True)
            if result.returncode != 0 or not os.path.exists(final_path):
                raise RuntimeError(f"Fusion finale échouée : {result.stderr[-300:]}")

        # ── Nettoyage ─────────────────────────────────────────────────────
        for tmp in [composite_path, assembled_path]:
            try: os.remove(tmp)
            except Exception: pass
        try: shutil.rmtree(str(parts_dir))
        except Exception: pass

        file_size = os.path.getsize(final_path) if os.path.exists(final_path) else 0
        if file_size < 10_000:
            raise RuntimeError(f"Fichier final trop petit ({file_size} bytes).")

        file_size_mb = file_size / (1024 * 1024)
        media_url    = djsettings.MEDIA_URL.rstrip("/")
        download_url = f"{media_url}/exports/{job.pk}/final.mp4"

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "export_done", download_url=download_url,
                file_size_mb=round(file_size_mb, 1))
        ws_status(job_id,
                  f"✅ Export terminé — {file_size_mb:.1f} Mo"
                  + (f" — sous-titres ASS incrustés" if ass_path else " — sans sous-titres"))

        return {
            "status": "success", "job_id": job_id,
            "final_path": final_path, "download_url": download_url,
            "file_size_mb": round(file_size_mb, 1),
        }

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur export job {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ Erreur montage : {error_msg}")
        return {"status": "error", "message": error_msg}


# ─────────────────────────────────────────
#  HELPERS PRIVÉS
# ─────────────────────────────────────────

def _extraire_waveform(wav_path: str, nb_points: int = 500) -> list:
    import wave
    import numpy as np
    try:
        with wave.open(wav_path, "rb") as wf:
            n_frames = wf.getnframes()
            if n_frames == 0:
                return [0.0] * nb_points
            samples = np.frombuffer(wf.readframes(n_frames), dtype=np.int16).astype(np.float32)
        block_size = max(1, len(samples) // nb_points)
        result     = []
        for i in range(nb_points):
            start = i * block_size
            end   = min(start + block_size, len(samples))
            result.append(0.0 if start >= len(samples) else
                          round(float(np.max(np.abs(samples[start:end])) / 32767.0), 4))
        return result
    except Exception as e:
        logger.warning(f"Waveform extraction failed: {e}")
        return [0.0] * nb_points


def _extraire_vignettes(video_path: str, job, nb_max: int = 10) -> list:
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        step         = max(1, total_frames // nb_max)
        thumbs_dir   = job.output_dir / "thumbs"
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(0, total_frames, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if ret:
                thumb    = cv2.resize(frame, (160, 90))
                filename = f"thumb_{len(paths):02d}.jpg"
                cv2.imwrite(str(thumbs_dir / filename), thumb)
                paths.append(f"/media/jobs/{job.pk}/thumbs/{filename}")
            if len(paths) >= nb_max:
                break
        cap.release()
        return paths
    except ImportError:
        logger.warning("OpenCV non installé.")
        return []
    except Exception as e:
        logger.warning(f"Vignettes failed: {e}")
        return []