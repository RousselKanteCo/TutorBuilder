"""
apps/studio/tasks.py — TutoBuilder Vision v9
─────────────────────────────────────────────
PRINCIPE :
  1. Chaque segment Whisper a un timecode fixe (parole)
  2. Le silence qui suit appartient au segment (rattaché)
  3. Au montage :
     - Partie PAROLE  → adaptée à la durée TTS (ralentir/accélérer)
     - Partie SILENCE → accélérée dynamiquement selon sa durée
       < 2s  → x1 (naturel)
       2-5s  → x2
       5-15s → x4
       > 15s → x8
  4. Pause 0.5s freeze entre chaque segment
  5. L'user ne change QUE le texte — timecodes intouchables
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

OUTPUT_FPS   = 25
MAX_SPEED    = 2.0   # vitesse max partie parole
MIN_SPEED    = 0.5   # vitesse min partie parole
# Pause dynamique selon longueur texte — voir _pause_ms()

SUBTITLE_MAX_CHARS = 44
SUBTITLE_MAX_LINES = 3

_SPEECH_RATE = {
    "fr": 13.5, "en": 15.5, "es": 15.0, "de": 12.0,
    "it": 14.5, "pt": 14.5, "default": 14.0,
}


# ═══════════════════════════════════════════════════════════════════════════════
#  CORRECTION BUG CARTESIA : header WAV corrompu (INT32_MAX)
# ═══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_path: str) -> float:
    try:
        file_size = os.path.getsize(wav_path)
        with wave.open(wav_path, "rb") as wf:
            sr        = wf.getframerate()
            channels  = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            nframes   = wf.getnframes()
        if nframes >= 2_000_000_000:
            data_bytes  = max(0, file_size - 44)
            real_frames = data_bytes // max(1, sampwidth * channels)
            return max(400.0, (real_frames / sr) * 1000.0)
        return max(400.0, (nframes / sr) * 1000.0)
    except Exception:
        try:
            file_size = os.path.getsize(wav_path)
            return max(400.0, (file_size - 44) / (22050 * 2) * 1000.0)
        except Exception:
            return 3000.0


def fix_wav_header(wav_path: str) -> str:
    fixed = wav_path + "_fixed.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", fixed],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and os.path.exists(fixed):
        os.replace(fixed, wav_path)
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
#  UTILITAIRES
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

def _text_to_screen(text):
    """
    Découpe un texte en lignes de 44 chars max.
    Retourne une liste de lignes — SANS limite de nombre.
    """
    words, lines, current = text.split(), [], ""
    for word in words:
        test = (current + " " + word).strip() if current else word
        if len(test) <= SUBTITLE_MAX_CHARS:
            current = test
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _chunk_for_subtitle(text):
    """
    Retourne les 3 premières lignes du texte pour un aperçu.
    Utilisé uniquement pour la prévisualisation dans le plan.
    Le vrai découpage en événements est fait par _split_subtitle_events.
    """
    lines = _text_to_screen(text)
    return "\n".join(lines[:3])


def _split_subtitle_events(text, start_ms, end_ms):
    """
    Découpe un texte en écrans de 3 lignes max (44 chars par ligne).
    AUCUN texte n est perdu — si ca depasse 3 lignes, on cree un nouvel ecran.
    Timing proportionnel au nombre de mots.

    Exemple (texte long) :
      Ecran 1 : lignes 1-3  → [start_ms → milieu]
      Ecran 2 : lignes 4-6  → [milieu → end_ms]
    """
    MAX_LINES_PER_SCREEN = 3

    # 1. Découper tout le texte en lignes de 44 chars
    all_lines = _text_to_screen(text)

    if not all_lines:
        return [{"start_ms": start_ms, "end_ms": end_ms,
                 "text": text, "sub_text": ""}]

    # 2. Regrouper les lignes par écrans de MAX_LINES_PER_SCREEN
    screens = []
    for i in range(0, len(all_lines), MAX_LINES_PER_SCREEN):
        screens.append(all_lines[i:i + MAX_LINES_PER_SCREEN])

    if len(screens) == 1:
        return [{"start_ms": start_ms, "end_ms": end_ms,
                 "text": text, "sub_text": "\n".join(screens[0])}]

    # 3. Répartir le temps proportionnellement au nombre de mots par écran
    screen_texts  = ["\n".join(s) for s in screens]
    screen_words  = [len(t.split()) for t in screen_texts]
    total_words   = sum(screen_words) or 1
    total_ms      = max(end_ms - start_ms, 100)
    events        = []
    cursor        = start_ms

    for i, (screen_text, nb_words) in enumerate(zip(screen_texts, screen_words)):
        is_last = (i == len(screens) - 1)
        duree   = int(total_ms * nb_words / total_words)
        fin     = end_ms if is_last else cursor + duree

        events.append({
            "start_ms": int(cursor),
            "end_ms":   int(fin),
            "text":     screen_text,
            "sub_text": screen_text,
        })
        cursor = fin

    return events


def _silence_speed(silence_dur_s: float) -> float:
    """Vitesse d'accélération dynamique selon la durée du silence."""
    if silence_dur_s < 2.0:
        return 1.0   # naturel
    elif silence_dur_s < 5.0:
        return 2.0   # léger
    elif silence_dur_s < 15.0:
        return 4.0   # modéré
    else:
        return 8.0   # rapide

def _pause_ms(tts_ms: float) -> float:
    """Pause dynamique selon la durée du TTS — plus c est court, moins on attend."""
    if tts_ms < 1500:
        return 300.0   # phrase très courte → 300ms
    elif tts_ms < 3000:
        return 500.0   # phrase courte → 500ms
    elif tts_ms < 6000:
        return 700.0   # phrase moyenne → 700ms
    else:
        return 900.0   # phrase longue → 900ms

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
            "index":        seg["index"],
            "new_start_ms": int(round(cursor)),
            "new_end_ms":   int(round(cursor + est_ms)),
            "est_tts_ms":   round(est_ms, 1),
            "text":         seg["text"],
            "subtitle_text":_chunk_for_subtitle(seg["text"]),
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


# ═══════════════════════════════════════════════════════════════════════════════
#  RENDU D'UN SEGMENT — LOGIQUE v9
#
#  Un segment = 3 parties :
#    1. PAROLE   [seg_start → seg_end]        → adaptée à la durée TTS
#    2. SILENCE  [seg_end   → silence_end]    → accélérée dynamiquement
#    3. PAUSE    0.5s freeze (sauf dernier)
# ═══════════════════════════════════════════════════════════════════════════════

def render_segment_v9(video_path, seg_start_s, seg_end_s, silence_end_s,
                      tts_ms, output_path, video_w, video_h, add_pause=True):
    parts_dir    = Path(output_path).parent
    stem         = Path(output_path).stem
    clips        = []
    speech_dur_s = max(0.1, seg_end_s - seg_start_s)
    silence_dur_s= max(0.0, silence_end_s - seg_end_s)
    tts_s        = tts_ms / 1000.0

    # ── 1. PARTIE PAROLE adaptée au TTS ──────────────────────────────────
    speech_speed = speech_dur_s / tts_s
    speech_speed = max(MIN_SPEED, min(MAX_SPEED, speech_speed))
    speech_path  = str(parts_dir / f"{stem}_speech.mp4")

    if abs(speech_speed - 1.0) < 0.05:
        vf_speech = f"fps={OUTPUT_FPS},scale={video_w}:{video_h}"
    else:
        vf_speech = f"setpts={1.0/speech_speed:.4f}*PTS,fps={OUTPUT_FPS},scale={video_w}:{video_h}"

    r = subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{seg_start_s:.3f}",
        "-t",  f"{speech_dur_s:.3f}",
        "-i", video_path,
        "-vf", vf_speech,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-an", speech_path,
    ], capture_output=True, text=True)

    if r.returncode == 0 and os.path.exists(speech_path):
        clips.append(speech_path)
        logger.debug(
            f"Parole : {speech_dur_s:.1f}s vidéo → {tts_s:.1f}s TTS "
            f"(vitesse x{speech_speed:.2f})"
        )

    # ── 2. PARTIE SILENCE accélérée dynamiquement ────────────────────────
    if silence_dur_s > 0.2:
        silence_speed = _silence_speed(silence_dur_s)
        silence_path  = str(parts_dir / f"{stem}_silence.mp4")

        if abs(silence_speed - 1.0) < 0.05:
            vf_silence = f"fps={OUTPUT_FPS},scale={video_w}:{video_h}"
        else:
            vf_silence = f"setpts={1.0/silence_speed:.4f}*PTS,fps={OUTPUT_FPS},scale={video_w}:{video_h}"

        r2 = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{seg_end_s:.3f}",
            "-t",  f"{silence_dur_s:.3f}",
            "-i", video_path,
            "-vf", vf_silence,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", silence_path,
        ], capture_output=True, text=True)

        if r2.returncode == 0 and os.path.exists(silence_path):
            clips.append(silence_path)
            logger.debug(
                f"Silence : {silence_dur_s:.1f}s → {silence_dur_s/silence_speed:.1f}s "
                f"(x{silence_speed:.0f})"
            )

    # ── 3. PAUSE dynamique freeze ────────────────────────────────────────
    pause_duration_ms = _pause_ms(tts_ms)
    if add_pause and clips:
        last_frame = str(parts_dir / f"{stem}_pf.jpg")
        pause_path = str(parts_dir / f"{stem}_pause.mp4")

        subprocess.run([
            "ffmpeg", "-y", "-sseof", "-0.1", "-i", clips[-1],
            "-vframes", "1", "-q:v", "2", last_frame,
        ], capture_output=True)

        if os.path.exists(last_frame):
            r3 = subprocess.run([
                "ffmpeg", "-y", "-loop", "1", "-i", last_frame,
                "-t", f"{pause_duration_ms/1000.0:.3f}",
                "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h}",
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
                "-an", pause_path,
            ], capture_output=True, text=True)

            if r3.returncode == 0 and os.path.exists(pause_path):
                clips.append(pause_path)
            try: os.remove(last_frame)
            except Exception: pass

    if not clips:
        return 0

    # ── Concat des parties ────────────────────────────────────────────────
    if len(clips) == 1:
        os.rename(clips[0], output_path)
    else:
        concat_f = str(parts_dir / f"{stem}_c.txt")
        with open(concat_f, "w") as f:
            for p in clips:
                f.write(f"file '{p}'\n")

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
        font_map     = {"calibri": "Calibri", "arial": "Arial", "segoe": "Segoe UI",
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
        send_job_notification(job, 'error')
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

        # Toujours utiliser les segments de la base (timecodes originaux)
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
                    actual_ms = get_wav_duration_ms(chemin)
                    Segment.objects.filter(job=job, index=seg["index"]).update(audio_file=chemin)
                    plan_items.append({
                        "index":         seg["index"],
                        "start_ms":      seg["start_ms"],  # timecode original Whisper
                        "end_ms":        seg["end_ms"],    # timecode original Whisper
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
        send_job_notification(job, 'error')
        return {"status": "error", "message": error_msg}


# ═══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 3 : EXPORT FINAL v9
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
    ws_status(job_id, "🎬 Export v9 — parole adaptée + silences dynamiques...")

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

        # ── Charger le plan ───────────────────────────────────────────────
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
                ms = get_wav_duration_ms(tts_path)
                if ms > 100:
                    plan_items.append({**item, "actual_tts_ms": ms})

        if not plan_items:
            raise RuntimeError("Aucun fichier audio TTS valide.")

        ws_status(job_id, f"✅ {len(plan_items)} segments chargés.")

        # ── Calculer les silences rattachés ───────────────────────────────
        #
        # Pour chaque segment i :
        #   silence_end = début du segment i+1 (ou fin de la vidéo)
        #   Le silence [end_i → start_{i+1}] appartient au segment i
        #
        ws_progress(job_id, 2, 5, "Calcul silences")
        for i, item in enumerate(plan_items):
            seg_start_s = item["start_ms"] / 1000.0
            seg_end_s   = item["end_ms"]   / 1000.0

            if i + 1 < len(plan_items):
                next_start_s = plan_items[i+1]["start_ms"] / 1000.0
            else:
                next_start_s = video_dur

            silence_dur_s = max(0.0, next_start_s - seg_end_s)

            item["seg_start_s"]    = seg_start_s
            item["seg_end_s"]      = seg_end_s
            item["silence_end_s"]  = seg_end_s + silence_dur_s
            item["silence_dur_s"]  = silence_dur_s

            spd = _silence_speed(silence_dur_s)
            ws_status(job_id,
                f"  Seg {i+1}/{len(plan_items)} : "
                f"parole {seg_end_s-seg_start_s:.1f}s + "
                f"silence {silence_dur_s:.1f}s (x{spd:.0f}) → "
                f"TTS {item['actual_tts_ms']/1000:.1f}s"
            )

        # ── Rendu clips ───────────────────────────────────────────────────
        ws_progress(job_id, 3, 5, "Rendu clips")
        part_files      = []
        timeline_ms     = 0.0   # position vidéo (clips bout à bout)
        audio_cursor_ms = 0.0   # position audio TTS (jamais chevauchement)
        subtitle_events = []
        total           = len(plan_items)

        for i, item in enumerate(plan_items):
            ws_progress(job_id, i+1, total, f"Clip {i+1}/{total}")

            part_path = str(parts_dir / f"part_{i:04d}.mp4")
            is_last   = (i == total - 1)

            clip_dur_ms = render_segment_v9(
                video_path    = video_path,
                seg_start_s   = item["seg_start_s"],
                seg_end_s     = item["seg_end_s"],
                silence_end_s = item["silence_end_s"],
                tts_ms        = item["actual_tts_ms"],
                output_path   = part_path,
                video_w       = video_w,
                video_h       = video_h,
                add_pause     = not is_last,
            )

            if clip_dur_ms > 0 and os.path.exists(part_path):
                tts_ms = item["actual_tts_ms"]

                # TTS commence au début du clip mais JAMAIS avant la fin du précédent
                tts_start_ms = max(timeline_ms, audio_cursor_ms)
                audio_end_ms = tts_start_ms + tts_ms

                # Découper le texte en événements sous-titres (max 2 lignes chacun)
                # proportionnels au nombre de mots → cohérence avec la voix
                sub_events = _split_subtitle_events(
                    item["text"], int(tts_start_ms), int(audio_end_ms)
                )
                subtitle_events.extend(sub_events)
                part_files.append({
                    "path":              part_path,
                    "clip_dur_ms":       clip_dur_ms,
                    "tts_path":          item["tts_path"],
                    "timeline_start_ms": tts_start_ms,
                    "tts_ms":            tts_ms,
                })
                timeline_ms     += clip_dur_ms
                audio_cursor_ms  = audio_end_ms

        if not part_files:
            raise RuntimeError("Aucun clip rendu.")

        ws_status(job_id, f"✅ {len(part_files)} clips — {timeline_ms/1000:.1f}s total")

        # ── Assemblage vidéo ──────────────────────────────────────────────
        ws_progress(job_id, 4, 5, "Assemblage")
        concat_list = str(parts_dir / "concat.txt")
        with open(concat_list, "w") as f:
            for p in part_files:
                f.write(f"file '{p['path']}'\n")

        assembled = str(work_dir / "assembled.mp4")
        ws_status(job_id, "🔗 Assemblage vidéo...")
        r = subprocess.run([
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18", assembled,
        ], capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(f"Assemblage échoué : {r.stderr[-200:]}")

        # ── Audio composite TTS ───────────────────────────────────────────
        ws_status(job_id, "🎵 Mixage audio...")
        sample_rate   = 22050
        total_samples = int((timeline_ms / 1000 + 2) * sample_rate)
        buffer        = np.zeros(total_samples, dtype=np.int16)

        for p in part_files:
            try:
                r_audio = subprocess.run([
                    "ffmpeg", "-y", "-i", p["tts_path"],
                    "-ar", str(sample_rate), "-ac", "1",
                    "-f", "s16le", "pipe:1",
                ], capture_output=True)
                if r_audio.returncode == 0 and r_audio.stdout:
                    tts_arr = np.frombuffer(r_audio.stdout, dtype=np.int16).copy()
                    ss = int(p["timeline_start_ms"] * sample_rate / 1000)
                    es = ss + len(tts_arr)
                    if es > len(buffer):
                        buffer = np.concatenate([
                            buffer,
                            np.zeros(es - len(buffer) + sample_rate, dtype=np.int16)
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

        # ── Sous-titres ASS ───────────────────────────────────────────────
        ass_path = None
        if subtitles_enabled and subtitle_events:
            ass_path = str(work_dir / "subtitles.ass")
            if not _generate_ass(subtitle_events, video_w, video_h, style, ass_path):
                ass_path = None

        # ── Encodage final ────────────────────────────────────────────────
        ws_progress(job_id, 5, 5, "Encodage final")
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
        send_job_notification(job, 'error')
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