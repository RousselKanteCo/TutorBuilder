"""
apps/studio/tasks.py — TutoBuilder Vision v8 Final
───────────────────────────────────────────────────
PRINCIPE : La voix TTS est sacrée. La vidéo s'adapte à elle.
  video_speed = scene_dur / tts_dur
  Correction bug Cartesia : WAV header INT32_MAX → calcul depuis taille fichier
"""

import os
import re
import json
import wave
import logging
import subprocess
import shutil
from pathlib import Path

import numpy as np
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from celery import shared_task
from django.conf import settings

from apps.studio.notifications import send_job_notification


logger = logging.getLogger(__name__)

OUTPUT_FPS            = 25
MAX_SPEED             = 2.0
MIN_SPEED             = 0.4
PAUSE_MS              = 600
SCENE_CHANGE_PAUSE_MS = 900
MAX_FREEZE_MS         = 6000
SCENE_THRESHOLD       = 0.18
SUBTITLE_MAX_CHARS    = 44
SUBTITLE_MAX_LINES    = 3

_SPEECH_RATE = {
    "fr": 13.5, "en": 15.5, "es": 15.0, "de": 12.0,
    "it": 14.5, "pt": 14.5, "default": 14.0,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  CORRECTION BUG CARTESIA : header WAV corrompu (INT32_MAX)
# ═══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_path: str) -> float:
    """
    Lit la vraie durée d'un WAV même si le header est corrompu.
    Cartesia retourne getnframes() = 2147483647 (INT32_MAX).
    Dans ce cas on calcule depuis la taille réelle du fichier.
    """
    try:
        file_size = os.path.getsize(wav_path)
        with wave.open(wav_path, "rb") as wf:
            sr        = wf.getframerate()
            channels  = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            nframes   = wf.getnframes()

        # Header corrompu détecté
        if nframes >= 2_000_000_000:
            data_bytes  = max(0, file_size - 44)
            real_frames = data_bytes // max(1, sampwidth * channels)
            dur = (real_frames / sr) * 1000.0
            logger.debug(f"WAV header corrompu corrigé : {file_size} bytes → {dur:.0f}ms")
            return max(400.0, dur)

        return max(400.0, (nframes / sr) * 1000.0)

    except Exception:
        try:
            # Dernier recours : taille fichier / (22050 * 2)
            file_size = os.path.getsize(wav_path)
            return max(400.0, (file_size - 44) / (22050 * 2) * 1000.0)
        except Exception:
            return 3000.0


def fix_wav_header(wav_path: str) -> str:
    """Corrige un WAV avec header corrompu via ffmpeg."""
    fixed = wav_path + "_fixed.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", fixed],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and os.path.exists(fixed):
        os.replace(fixed, wav_path)
        logger.info(f"WAV header corrigé : {wav_path}")
    return wav_path


# ═══════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET
# ═══════════════════════════════════════════════════════════════════════════════

def ws_send(job_id, message_type, **kwargs):
    try:
        cl = get_channel_layer()
        if cl:
            async_to_sync(cl.group_send)(
                f"job_{job_id}", {"type": f"job.{message_type}", **kwargs}
            )
    except Exception:
        pass

def ws_status(job_id, msg):
    ws_send(job_id, "status", message=msg)
    logger.info(f"[{job_id}] {msg}")

def ws_progress(job_id, step, total, label):
    ws_send(job_id, "progress", step=step, total=total, label=label,
            percent=int((step / max(total, 1)) * 100))


# ═══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES TEXTE
# ═══════════════════════════════════════════════════════════════════════════════

def _speech_rate(lang):
    return _SPEECH_RATE.get((lang or "fr").lower()[:2], _SPEECH_RATE["default"])

def _estimate_tts_ms(text, lang="fr"):
    clean     = re.sub(r"\s+", " ", (text or "").strip())
    effective = len(re.sub(r"[^\w\s]", "", clean))
    base_ms   = (effective / _speech_rate(lang)) * 1000.0
    pauses    = (
        clean.count(".") * 260 + clean.count("?") * 260 +
        clean.count("!") * 260 + clean.count(",") * 110 +
        clean.count(";") * 180 + clean.count(":") * 140
    )
    return max(400.0, base_ms + pauses)

def _chunk_for_subtitle(text):
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

def _redistribute_timecodes(segments_data, lang="fr"):
    if not segments_data:
        return []
    segs = sorted([{
        "index":    int(s.get("index", i)),
        "start_ms": float(s.get("start_ms", s.get("start", 0))),
        "end_ms":   float(s.get("end_ms", s.get("end", 0))),
        "text":     (s.get("text") or "").strip(),
    } for i, s in enumerate(segments_data)], key=lambda x: x["start_ms"])

    gaps = [max(80.0, segs[i+1]["start_ms"] - segs[i]["end_ms"])
            for i in range(len(segs)-1)]
    estimates = [_estimate_tts_ms(s["text"], lang) for s in segs]
    results, cursor = [], segs[0]["start_ms"]

    for i, (seg, est_ms) in enumerate(zip(segs, estimates)):
        results.append({
            "index":         seg["index"],
            "new_start_ms":  int(round(cursor)),
            "new_end_ms":    int(round(cursor + est_ms)),
            "est_tts_ms":    round(est_ms, 1),
            "text":          seg["text"],
            "subtitle_text": _chunk_for_subtitle(seg["text"]),
        })
        cursor += est_ms + (gaps[i] if i < len(gaps) else 0)
    return results


# ═══════════════════════════════════════════════════════════════════════════════
#  DÉTECTION VIDÉO
# ═══════════════════════════════════════════════════════════════════════════════

def get_video_duration(video_path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0

def get_video_dimensions(video_path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", video_path],
        capture_output=True, text=True,
    )
    try:
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1920, 1080

def get_clip_duration_ms(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip()) * 1000
    except Exception:
        return 0

def detect_scene_changes(video_path, threshold=SCENE_THRESHOLD):
    cmd = [
        "ffmpeg", "-i", video_path,
        "-filter:v", f"select='gt(scene,{threshold})',showinfo",
        "-f", "null", "-",
    ]
    result      = subprocess.run(cmd, capture_output=True, text=True)
    scene_times = [0.0]
    for line in result.stderr.split("\n"):
        if "pts_time:" in line:
            try:
                t = float(line.split("pts_time:")[1].split()[0])
                scene_times.append(t)
            except (ValueError, IndexError):
                pass
    scene_times = sorted(set(scene_times))
    merged = [scene_times[0]]
    for t in scene_times[1:]:
        if t - merged[-1] >= 0.5:
            merged.append(t)
    return merged


# ═══════════════════════════════════════════════════════════════════════════════
#  RENDU D'UN CLIP
# ═══════════════════════════════════════════════════════════════════════════════

def render_clip(video_path, start_s, scene_dur_s, tts_ms,
                pause_ms, output_path, video_w, video_h):
    parts_dir   = Path(output_path).parent
    stem        = Path(output_path).stem
    tts_s       = tts_ms / 1000.0
    # Limiter la scène à max 2x la durée TTS → supprime les blancs résiduels
    scene_dur_s = min(scene_dur_s, tts_s * 2.0)
    scene_dur_s = max(scene_dur_s, 0.3)
    ideal_speed = scene_dur_s / tts_s
    clips       = []

    if MIN_SPEED <= ideal_speed <= MAX_SPEED:
        vf = (f"fps={OUTPUT_FPS},scale={video_w}:{video_h}"
              if abs(ideal_speed - 1.0) < 0.05
              else f"setpts={1.0/ideal_speed:.4f}*PTS,fps={OUTPUT_FPS},scale={video_w}:{video_h}")

        clip_path = str(parts_dir / f"{stem}_main.mp4")
        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{start_s:.3f}", "-t", f"{scene_dur_s:.3f}",
            "-i", video_path, "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", clip_path,
        ], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(clip_path):
            clips.append(clip_path)

    else:
        actual_speed = MAX_SPEED if ideal_speed > MAX_SPEED else MIN_SPEED
        vf           = f"setpts={1.0/actual_speed:.4f}*PTS,fps={OUTPUT_FPS},scale={video_w}:{video_h}"
        clip_path    = str(parts_dir / f"{stem}_main.mp4")

        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{start_s:.3f}", "-t", f"{scene_dur_s:.3f}",
            "-i", video_path, "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", clip_path,
        ], capture_output=True, text=True)

        if r.returncode == 0 and os.path.exists(clip_path):
            clips.append(clip_path)
            clip_dur_s = get_clip_duration_ms(clip_path) / 1000.0
            missing_s  = min(max(0, tts_s - clip_dur_s), MAX_FREEZE_MS / 1000.0)

            if missing_s > 0.2:
                last_frame  = str(parts_dir / f"{stem}_last.jpg")
                freeze_path = str(parts_dir / f"{stem}_freeze.mp4")
                subprocess.run([
                    "ffmpeg", "-y", "-sseof", "-0.1", "-i", clip_path,
                    "-vframes", "1", "-q:v", "2", last_frame,
                ], capture_output=True)
                if os.path.exists(last_frame):
                    r2 = subprocess.run([
                        "ffmpeg", "-y", "-loop", "1", "-i", last_frame,
                        "-t", f"{missing_s:.3f}",
                        "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h}",
                        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                        "-an", freeze_path,
                    ], capture_output=True, text=True)
                    if r2.returncode == 0 and os.path.exists(freeze_path):
                        clips.append(freeze_path)
                    try: os.remove(last_frame)
                    except Exception: pass

    # Pause pédagogique
    if pause_ms > 100 and clips:
        last_frame = str(parts_dir / f"{stem}_pf.jpg")
        pause_path = str(parts_dir / f"{stem}_pause.mp4")
        subprocess.run([
            "ffmpeg", "-y", "-sseof", "-0.1", "-i", clips[-1],
            "-vframes", "1", "-q:v", "2", last_frame,
        ], capture_output=True)
        if os.path.exists(last_frame):
            r3 = subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", last_frame,
                "-t", f"{pause_ms/1000.0:.3f}",
                "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h},hue=s=0.5",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                "-an", pause_path,
            ], capture_output=True, text=True)
            if r3.returncode == 0 and os.path.exists(pause_path):
                clips.append(pause_path)
            try: os.remove(last_frame)
            except Exception: pass

    if not clips:
        return 0

    if len(clips) == 1:
        os.rename(clips[0], output_path)
    else:
        concat_f = str(parts_dir / f"{stem}_c.txt")
        with open(concat_f, "w") as f:
            for p in clips: f.write(f"file '{p}'\n")
        r4 = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_f,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", output_path,
        ], capture_output=True, text=True)
        for p in clips:
            try: os.remove(p)
            except Exception: pass
        try: os.remove(concat_f)
        except Exception: pass
        if r4.returncode != 0:
            return 0

    return get_clip_duration_ms(output_path)


# ═══════════════════════════════════════════════════════════════════════════════
#  SOUS-TITRES ASS
# ═══════════════════════════════════════════════════════════════════════════════

def _hex_to_ass(hex_color):
    h = hex_color.lstrip("#").upper()
    return (h[4:6] + h[2:4] + h[0:2]) if len(h) == 6 else "FFFFFF"

def _ms_to_ass(ms):
    ms = int(max(0, ms))
    h  = ms // 3_600_000; ms %= 3_600_000
    m  = ms // 60_000;    ms %= 60_000
    s  = ms // 1_000;     ms %= 1_000
    return f"{h}:{m:02d}:{s:02d}.{ms//10:02d}"

def _generate_ass(events, video_w, video_h, style, output_path):
    try:
        font_map  = {"calibri": "Calibri", "arial": "Arial", "segoe": "Segoe UI",
                     "georgia": "Georgia", "impact": "Impact"}
        font_name    = font_map.get(style.get("font_family", "calibri"), "Arial")
        font_size    = int(style.get("font_size", 48))
        primary_clr  = _hex_to_ass(style.get("text_color", "FFFFFF"))
        outline_clr  = _hex_to_ass(style.get("outline_color", "000000"))
        bg_clr       = _hex_to_ass(style.get("bg_color", "000000"))
        outline_w    = int(style.get("outline_width", 2))
        shadow_d     = 2 if style.get("shadow", True) else 0
        bg_on        = bool(style.get("bg_enabled", True))
        bg_opacity   = int(style.get("bg_opacity", 75))
        bg_alpha     = hex(max(0, 255 - int(bg_opacity * 2.55)))[2:].upper().zfill(2)
        position     = style.get("position", "bottom")
        margin_v     = int(style.get("margin", 60))
        alignment    = {"bottom": 2, "top": 8, "center": 5}.get(position, 2)
        border_style = 3 if bg_on else 1
        back_color   = f"&H{bg_alpha}{bg_clr}&"
        ow_actual    = outline_w if not bg_on else 0

        header = (
            f"[Script Info]\nScriptType: v4.00+\n"
            f"PlayResX: {video_w}\nPlayResY: {video_h}\n"
            f"ScaledBorderAndShadow: yes\nWrapStyle: 0\n\n"
            f"[V4+ Styles]\n"
            f"Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,"
            f"OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,"
            f"ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,"
            f"Alignment,MarginL,MarginR,MarginV,Encoding\n"
            f"Style: Default,{font_name},{font_size},"
            f"&H00{primary_clr}&,&H000000FF&,&H00{outline_clr}&,{back_color},"
            f"1,0,0,0,100,100,0,0,{border_style},{ow_actual},{shadow_d},"
            f"{alignment},10,10,{margin_v},1\n\n"
            f"[Events]\n"
            f"Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )

        sorted_events = sorted(events, key=lambda x: x["start_ms"])
        clean_events  = []
        for i, ev in enumerate(sorted_events):
            start = ev["start_ms"]
            end   = ev["end_ms"]
            if i + 1 < len(sorted_events):
                end = min(end, sorted_events[i+1]["start_ms"] - 50)
            if end > start:
                clean_events.append({**ev, "start_ms": start, "end_ms": end})

        lines = []
        for ev in clean_events:
            txt = (ev.get("sub_text") or ev.get("text", "")).strip()
            if txt:
                lines.append(
                    f"Dialogue: 0,{_ms_to_ass(ev['start_ms'])},"
                    f"{_ms_to_ass(ev['end_ms'])},Default,,0,0,0,,"
                    f"{txt.replace(chr(10), chr(92)+'N')}"
                )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(lines))
        logger.info(f"ASS : {len(lines)} sous-titres")
        return True
    except Exception as e:
        logger.error(f"Erreur ASS : {e}")
        return False


# ═══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 1 : TRANSCRIPTION
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, name="studio.transcribe")
def task_transcribe(self, job_id, video_path,
                    stt_engine="faster_whisper", langue="fr"):
    from apps.studio.models import Job, Segment
    from stt_providers import STTProviderFactory, extraire_audio_wav

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.output_dir.mkdir(parents=True, exist_ok=True)

    try:
        ws_status(job_id, "🔊 Extraction audio...")
        ws_progress(job_id, 1, 4, "Extraction audio")
        job.set_status(Job.Status.EXTRACTING)

        wav_path = str(job.wav_path)
        if extraire_audio_wav(video_path, output_wav=wav_path) is None:
            raise RuntimeError("Extraction audio échouée.")
        ws_status(job_id, "✅ Audio extrait.")

        ws_status(job_id, "📊 Waveform...")
        ws_progress(job_id, 2, 4, "Waveform")
        waveform = _extraire_waveform(wav_path)
        job.waveform_data = waveform
        job.save(update_fields=["waveform_data"])
        ws_send(job_id, "waveform", data=waveform)

        ws_status(job_id, "🖼️ Vignettes...")
        ws_progress(job_id, 3, 4, "Vignettes")
        thumbs = _extraire_vignettes(video_path, job)
        if thumbs:
            job.thumbnail_paths = thumbs
            job.save(update_fields=["thumbnail_paths"])

        ws_status(job_id, "🧠 Transcription faster-whisper...")
        ws_progress(job_id, 4, 4, "Transcription")
        job.set_status(Job.Status.TRANSCRIBING)

        provider = STTProviderFactory.create("faster_whisper")
        if not provider.est_disponible():
            raise RuntimeError("Faster-Whisper non disponible.")

        segments_raw = provider.transcrire(wav_path, langue=langue)
        if not segments_raw:
            raise RuntimeError("Aucun segment transcrit.")

        nb = Segment.bulk_create_from_stt(job, segments_raw)
        job.set_status(Job.Status.TRANSCRIBED)
        send_job_notification(job, 'transcribed')
        ws_send(job_id, "segments", data=segments_raw)
        ws_status(job_id, f"✅ {nb} segments transcrits.")
        return {"status": "success", "nb_segments": nb}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur transcription {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ {error_msg}")
        return {"status": "error", "message": error_msg}


# ═══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 2 : SYNTHÈSE VOCALE
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, name="studio.synthesize")
def task_synthesize(self, job_id, segments_data,
                    tts_engine="elevenlabs", voix="narrateur_pro", langue="fr"):
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
        elif tts_engine == "cartesia":
            provider_kwargs["api_key"] = getattr(settings, "CARTESIA_API_KEY", "")

        provider = TTSProviderFactory.create(tts_engine, **provider_kwargs)
        if not provider.est_disponible():
            raise RuntimeError(f"Provider '{tts_engine}' non disponible.")

        output_dir = str(job.output_dir / "tts")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        db_segments = list(
            Segment.objects.filter(job=job).order_by("index")
            .values("index", "start_ms", "end_ms", "text")
        )
        if not db_segments:
            raise RuntimeError("Aucun segment en base.")

        total = len(db_segments)
        ws_status(job_id, f"🎙️ Synthèse {provider.nom} — {total} segments...")

        plan_items = []
        for i, seg in enumerate(db_segments):
            texte = (seg["text"] or "").strip()
            if not texte:
                continue

            ws_send(job_id, "tts_progress", current=i+1, total=total)
            ws_progress(job_id, i+1, total, f"Synthèse {i+1}/{total}")

            filename = f"seg_{seg['index']:04d}.wav"
            try:
                chemin = provider.generer(
                    texte=texte, voix=voix, output_dir=output_dir,
                    filename=filename, langue=langue,
                )
                if chemin and os.path.exists(chemin):
                    # ← CORRECTION : utiliser get_wav_duration_ms au lieu de wave
                    actual_ms = get_wav_duration_ms(chemin)
                    logger.info(f"Segment {i}: {actual_ms:.0f}ms — {texte[:40]}")

                    Segment.objects.filter(job=job, index=seg["index"]).update(audio_file=chemin)
                    plan_items.append({
                        "index":         seg["index"],
                        "start_ms":      seg["start_ms"],
                        "end_ms":        seg["end_ms"],
                        "actual_tts_ms": round(actual_ms, 1),
                        "tts_path":      chemin,
                        "text":          texte,
                        "subtitle_text": _chunk_for_subtitle(texte),
                    })
            except Exception as e:
                logger.warning(f"TTS segment {i} échoué : {e}")

        plan_path = str(job.output_dir / "synthesis_plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump({"job_id": str(job_id), "langue": langue, "plan": plan_items},
                      f, ensure_ascii=False, indent=2)

        nb_ok = len(plan_items)
        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done", nb_ok=nb_ok, nb_total=total)
        ws_status(job_id, f"✅ {nb_ok}/{total} segments générés.")
        send_job_notification(job, 'tts_done')
        return {"status": "success", "nb_generated": nb_ok}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur synthèse {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ {error_msg}")
        return {"status": "error", "message": error_msg}


# ═══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 3 : EXPORT FINAL
# ═══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, name="studio.export")
def task_export(self, job_id, subtitle_style=None):
    from apps.studio.models import Job

    style             = subtitle_style or {}
    subtitles_enabled = style.get("enabled", True)

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.EXTRACTING)
    ws_status(job_id, "🎬 Export — la vidéo s'adapte à la voix...")

    try:
        video_path  = str(job.video_file.path)
        work_dir    = job.output_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        parts_dir   = work_dir / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
        exports_dir.mkdir(parents=True, exist_ok=True)
        final_path  = str(exports_dir / "final.mp4")

        video_w, video_h = get_video_dimensions(video_path)
        video_dur        = get_video_duration(video_path)
        ws_status(job_id, f"📹 {video_w}×{video_h} — {video_dur:.1f}s")

        # Charger le plan
        ws_progress(job_id, 1, 5, "Chargement plan")
        plan_path = str(work_dir / "synthesis_plan.json")
        if not os.path.exists(plan_path):
            raise RuntimeError("Plan de synthèse introuvable. Lancez la synthèse.")

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)

        langue     = plan_data.get("langue", "fr")
        plan_items = []
        for item in plan_data.get("plan", []):
            tts_path = item.get("tts_path", "")
            if tts_path and os.path.exists(tts_path):
                # ← CORRECTION : utiliser get_wav_duration_ms
                ms = get_wav_duration_ms(tts_path)
                if ms > 100:
                    plan_items.append({**item, "actual_tts_ms": ms})

        if not plan_items:
            raise RuntimeError("Aucun fichier audio TTS valide.")

        ws_status(job_id, f"✅ {len(plan_items)} segments — durées corrigées.")

        # Détecter les scènes
        ws_progress(job_id, 2, 5, "Scènes")
        scene_times = detect_scene_changes(video_path)
        scene_intervals = []
        for i, t in enumerate(scene_times):
            end = scene_times[i+1] if i+1 < len(scene_times) else video_dur
            dur = end - t
            # Ignorer les scènes trop courtes (blancs/transitions)
            if dur > 0.5:
                scene_intervals.append({"start": t, "end": end, "dur": dur, "idx": i})
        if not scene_intervals:
            scene_intervals = [{"start": 0, "end": video_dur, "dur": video_dur, "idx": 0}]
        ws_status(job_id, f"✅ {len(scene_intervals)} scènes (blancs ignorés).")

        # Aligner segments
        ws_progress(job_id, 3, 5, "Alignement")
        prev_scene_idx = -1
        aligned_items  = []
        for item in plan_items:
            seg_start_s  = item["start_ms"] / 1000.0
            best_scene   = min(scene_intervals, key=lambda sc: abs(sc["start"] - seg_start_s))
            is_new_scene = (best_scene["idx"] != prev_scene_idx)
            aligned_items.append({
                **item,
                "scene_start": best_scene["start"],
                "scene_dur":   best_scene["dur"],
                "scene_idx":   best_scene["idx"],
                "pause_ms":    SCENE_CHANGE_PAUSE_MS if is_new_scene else PAUSE_MS,
            })
            prev_scene_idx = best_scene["idx"]

        # Rendu clips
        ws_progress(job_id, 4, 5, "Rendu clips")
        part_files      = []
        timeline_ms     = 0.0
        subtitle_events = []
        total           = len(aligned_items)

        for i, item in enumerate(aligned_items):
            ws_progress(job_id, i+1, total, f"Clip {i+1}/{total}")
            tts_ms    = item["actual_tts_ms"]
            scene_dur = item["scene_dur"]
            speed     = scene_dur / (tts_ms / 1000.0)

            ws_status(job_id,
                f"  [{i+1}/{total}] TTS={tts_ms:.0f}ms "
                f"scène={scene_dur*1000:.0f}ms → vitesse={speed:.2f}x"
            )

            part_path   = str(parts_dir / f"part_{i:04d}.mp4")
            clip_dur_ms = render_clip(
                video_path=video_path, start_s=item["scene_start"],
                scene_dur_s=scene_dur, tts_ms=tts_ms,
                pause_ms=item["pause_ms"], output_path=part_path,
                video_w=video_w, video_h=video_h,
            )

            if clip_dur_ms > 0 and os.path.exists(part_path):
                subtitle_events.append({
                    "start_ms": int(timeline_ms),
                    "end_ms":   int(timeline_ms + tts_ms),
                    "text":     item["text"],
                    "sub_text": item.get("subtitle_text", _chunk_for_subtitle(item["text"])),
                })
                part_files.append({
                    "path":              part_path,
                    "clip_dur_ms":       clip_dur_ms,
                    "tts_path":          item["tts_path"],
                    "timeline_start_ms": timeline_ms,
                    "tts_ms":            tts_ms,
                })
                timeline_ms += clip_dur_ms

        if not part_files:
            raise RuntimeError("Aucun clip rendu.")

        ws_status(job_id, f"✅ {len(part_files)} clips — {timeline_ms/1000:.1f}s")

        # Assemblage
        ws_progress(job_id, 5, 5, "Assemblage")
        concat_list = str(parts_dir / "concat.txt")
        with open(concat_list, "w") as f:
            for p in part_files: f.write(f"file '{p['path']}'\n")

        assembled = str(work_dir / "assembled.mp4")
        ws_status(job_id, "🔗 Assemblage...")
        r = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", assembled,
        ], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Assemblage échoué : {r.stderr[-200:]}")

        # Audio composite
        ws_status(job_id, "🎵 Mixage audio...")
        sample_rate   = 22050
        total_samples = int((timeline_ms / 1000 + 2) * sample_rate)
        buffer        = np.zeros(total_samples, dtype=np.int16)

        for p in part_files:
            try:
                # Lire l'audio avec ffmpeg pour éviter le bug du header WAV
                cmd_read = [
                    "ffmpeg", "-y", "-i", p["tts_path"],
                    "-ar", str(sample_rate), "-ac", "1",
                    "-f", "s16le", "pipe:1",
                ]
                r_audio = subprocess.run(cmd_read, capture_output=True)
                if r_audio.returncode == 0 and r_audio.stdout:
                    tts_arr = np.frombuffer(r_audio.stdout, dtype=np.int16).copy()
                    ss = int(p["timeline_start_ms"] * sample_rate / 1000)
                    es = ss + len(tts_arr)
                    if es > len(buffer):
                        buffer = np.concatenate([
                            buffer, np.zeros(es - len(buffer) + sample_rate, dtype=np.int16)
                        ])
                    buffer[ss:es] = np.clip(
                        buffer[ss:es].astype(np.int32) + tts_arr.astype(np.int32),
                        np.iinfo(np.int16).min, np.iinfo(np.int16).max
                    ).astype(np.int16)
            except Exception as e:
                logger.warning(f"Audio ignoré : {e}")

        composite_wav = str(work_dir / "composite.wav")
        with wave.open(composite_wav, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(buffer.tobytes())

        # Sous-titres
        ass_path = None
        if subtitles_enabled and subtitle_events:
            ass_path = str(work_dir / "subtitles.ass")
            if not _generate_ass(subtitle_events, video_w, video_h, style, ass_path):
                ass_path = None

        # Encodage final
        ws_status(job_id, "⚙️ Encodage final...")
        if ass_path and os.path.exists(ass_path):
            ass_esc = ass_path.replace("\\", "/").replace(":", "\\:")
            cmd = [
                "ffmpeg", "-y", "-i", assembled, "-i", composite_wav,
                "-filter:v", f"ass='{ass_esc}'",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", final_path,
            ]
        else:
            cmd = [
                "ffmpeg", "-y", "-i", assembled, "-i", composite_wav,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", final_path,
            ]

        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0 or not os.path.exists(final_path):
            subprocess.run([
                "ffmpeg", "-y", "-i", assembled, "-i", composite_wav,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", final_path,
            ], capture_output=True)

        for tmp in [composite_wav, assembled]:
            try: os.remove(tmp)
            except Exception: pass
        try: shutil.rmtree(str(parts_dir))
        except Exception: pass

        if not os.path.exists(final_path) or os.path.getsize(final_path) < 10000:
            raise RuntimeError("Fichier final invalide.")

        file_size_mb = os.path.getsize(final_path) / (1024 * 1024)
        download_url = f"{settings.MEDIA_URL.rstrip('/')}/exports/{job.pk}/final.mp4"

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "export_done", download_url=download_url,
                file_size_mb=round(file_size_mb, 1))
        ws_status(job_id,
            f"✅ Export terminé — {file_size_mb:.1f} Mo | "
            f"{timeline_ms/1000:.1f}s | {len(subtitle_events)} sous-titres"
        )
        send_job_notification(job, 'export_done', download_url=download_url)
        return {"status": "success", "download_url": download_url}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur export {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ {error_msg}")
        return {"status": "error", "message": error_msg}


# ═══════════════════════════════════════════════════════════════════════════════
#  HELPERS PRIVÉS
# ═══════════════════════════════════════════════════════════════════════════════

def _extraire_waveform(wav_path, nb_points=500):
    try:
        with wave.open(wav_path, "rb") as wf:
            n = wf.getnframes()
            if n == 0: return [0.0] * nb_points
            samples = np.frombuffer(wf.readframes(n), dtype=np.int16).astype(np.float32)
        block = max(1, len(samples) // nb_points)
        return [
            round(float(np.max(np.abs(samples[i*block:min((i+1)*block, len(samples))])) / 32767), 4)
            for i in range(nb_points)
        ]
    except Exception:
        return [0.0] * nb_points

def _extraire_vignettes(video_path, job, nb_max=10):
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened(): return []
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        step  = max(1, total // nb_max)
        thumbs_dir = job.output_dir / "thumbs"
        thumbs_dir.mkdir(parents=True, exist_ok=True)
        paths = []
        for i in range(0, total, step):
            cap.set(cv2.CAP_PROP_POS_FRAMES, i)
            ret, frame = cap.read()
            if ret:
                thumb = cv2.resize(frame, (160, 90))
                fn    = f"thumb_{len(paths):02d}.jpg"
                cv2.imwrite(str(thumbs_dir / fn), thumb)
                paths.append(f"/media/jobs/{job.pk}/thumbs/{fn}")
            if len(paths) >= nb_max: break
        cap.release()
        return paths
    except Exception:
        return []