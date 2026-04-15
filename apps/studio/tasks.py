"""
apps/studio/tasks.py — TutoBuilder Vision v10
══════════════════════════════════════════════════════════════════════════════
PRINCIPE : "Vidéo maître du temps — TTS s'adapte"
──────────────────────────────────────────────────
La vidéo source N'EST JAMAIS étirée ni compressée temporellement.
C'est l'audio TTS qui est ajusté pour coller à la vidéo.

LOGIQUE PAR SEGMENT :
  budget_ms = seg[i+1].start_ms − seg[i].start_ms
              (ou fin de vidéo pour le dernier segment)

  Cas A — TTS ≤ budget × 0.95  (texte court, segment vidéo long)
    → Vidéo découpée normalement [start → next_start]
    → Audio TTS placé au début, silence naturel en fin de segment
    → Résultat : voix nette + respiration naturelle

  Cas B — TTS ≈ budget (±5%)
    → Vidéo découpée normalement
    → Audio TTS posé directement, synchronisation quasi-parfaite

  Cas C — TTS > budget  (texte long, segment vidéo court)
    → La portion parole [start → end] est loopée proprement
      (rebouclage sur la même séquence naturelle)
      jusqu'à couvrir la durée TTS complète
    → Pas de setpts, pas d'étirement : loop d'une action d'interface
    → Pause freeze 400ms entre segments (sauf dernier)

RÈGLES ABSOLUES :
  - Jamais de setpts (ni accélération ni ralentissement vidéo)
  - Jamais de modification du TTS audio (atempo distord la voix)
  - Un segment = un fichier TTS = un clip vidéo dans la timeline
  - Les timecodes Whisper sont SACRÉS — seul le texte est modifiable
"""

import os
import re
import json
import wave
import logging
import subprocess
import shutil
import tempfile
from pathlib import Path

import numpy as np
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from celery import shared_task
from django.conf import settings

from apps.studio.notifications import send_job_notification

logger = logging.getLogger(__name__)

OUTPUT_FPS   = 25
PAUSE_MS     = 400          # freeze entre segments (ms)
MIN_TTS_MS   = 300          # durée TTS minimale acceptable
MAX_LOOPS    = 6            # boucles max pour cas C (sécurité)

SUBTITLE_MAX_CHARS = 44
SUBTITLE_MAX_LINES = 3


# ══════════════════════════════════════════════════════════════════════════════
#  CORRECTION BUG CARTESIA : header WAV corrompu (INT32_MAX)
# ══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_path: str) -> float:
    """Lit la vraie durée WAV, contourne le bug INT32_MAX de Cartesia."""
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
            return max(MIN_TTS_MS, (real_frames / sr) * 1000.0)
        return max(MIN_TTS_MS, (nframes / sr) * 1000.0)
    except Exception:
        try:
            return max(MIN_TTS_MS, (os.path.getsize(wav_path) - 44) / (22050 * 2) * 1000.0)
        except Exception:
            return 3000.0


def fix_wav_header(wav_path: str) -> str:
    """Réécrit le header WAV via ffmpeg si corrompu."""
    fixed = wav_path + "_fixed.wav"
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", wav_path,
         "-ar", "22050", "-ac", "1", "-sample_fmt", "s16", fixed],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and os.path.exists(fixed):
        os.replace(fixed, wav_path)
    return wav_path


# ══════════════════════════════════════════════════════════════════════════════
#  WEBSOCKET
# ══════════════════════════════════════════════════════════════════════════════

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


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES VIDÉO
# ══════════════════════════════════════════════════════════════════════════════

def get_video_duration(video_path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", video_path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def get_video_dimensions(video_path: str) -> tuple:
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


def get_clip_duration_ms(path: str) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", path],
        capture_output=True, text=True,
    )
    try:
        return float(r.stdout.strip()) * 1000
    except Exception:
        return 0.0


def run_ffmpeg(args: list, label: str = "") -> bool:
    """Lance ffmpeg -y + args, retourne True si succès."""
    r = subprocess.run(["ffmpeg", "-y"] + args, capture_output=True, text=True)
    if r.returncode != 0:
        logger.warning(f"ffmpeg '{label}' failed:\n{r.stderr[-400:]}")
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  CALCUL DES BUDGETS-TEMPS
# ══════════════════════════════════════════════════════════════════════════════

def compute_budgets(plan_items: list, video_dur_s: float) -> list:
    """
    Pour chaque item, calcule :
      seg_start_s   — début parole (secondes)
      seg_end_s     — fin parole (secondes)
      next_start_s  — début du segment suivant (ou fin vidéo)
      budget_ms     — temps total disponible = next_start - seg_start
      tts_ms        — durée réelle du TTS
      ratio         — tts_ms / budget_ms
      case          — "short" | "match" | "long"
    """
    result = []
    n = len(plan_items)

    for i, item in enumerate(plan_items):
        seg_start_s = item["start_ms"] / 1000.0
        seg_end_s   = item["end_ms"]   / 1000.0

        next_start_s = (
            plan_items[i + 1]["start_ms"] / 1000.0
            if i + 1 < n else video_dur_s
        )

        budget_ms = max(500.0, (next_start_s - seg_start_s) * 1000.0)
        tts_ms    = item["actual_tts_ms"]
        ratio     = tts_ms / budget_ms

        if ratio < 0.95:
            case = "short"
        elif ratio <= 1.05:
            case = "match"
        else:
            case = "long"

        result.append({
            **item,
            "seg_start_s":  seg_start_s,
            "seg_end_s":    max(seg_end_s, seg_start_s + 0.1),
            "next_start_s": next_start_s,
            "budget_ms":    budget_ms,
            "tts_ms":       tts_ms,
            "ratio":        round(ratio, 3),
            "case":         case,
        })

    return result


# ══════════════════════════════════════════════════════════════════════════════
#  FREEZE PAUSE
# ══════════════════════════════════════════════════════════════════════════════

def _append_freeze(clip_path: str, pause_ms: float,
                   w: int, h: int, fps: int, tmp_dir: str, stem: str):
    """Extrait la dernière frame du clip et soude un freeze à sa fin."""
    last_frame = os.path.join(tmp_dir, f"{stem}_lf.jpg")
    pause_path = os.path.join(tmp_dir, f"{stem}_pause.mp4")

    subprocess.run([
        "ffmpeg", "-y", "-sseof", "-0.15", "-i", clip_path,
        "-vframes", "1", "-q:v", "2", last_frame,
    ], capture_output=True)

    if not os.path.exists(last_frame):
        return

    ok = run_ffmpeg([
        "-loop", "1", "-i", last_frame,
        "-t", f"{pause_ms / 1000.0:.3f}",
        "-vf", f"scale={w}:{h},fps={fps}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-an", pause_path,
    ], f"freeze {stem}")

    try:
        os.remove(last_frame)
    except Exception:
        pass

    if not ok or not os.path.exists(pause_path):
        return

    combined = clip_path + "_weld.mp4"
    cat_f = os.path.join(tmp_dir, f"{stem}_wcat.txt")
    with open(cat_f, "w") as f:
        f.write(f"file '{clip_path}'\n")
        f.write(f"file '{pause_path}'\n")

    ok2 = run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", cat_f,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-an", combined,
    ], f"weld pause {stem}")

    for p in [cat_f, pause_path]:
        try:
            os.remove(p)
        except Exception:
            pass

    if ok2 and os.path.exists(combined):
        os.replace(combined, clip_path)


# ══════════════════════════════════════════════════════════════════════════════
#  RENDU D'UN CLIP VIDÉO
# ══════════════════════════════════════════════════════════════════════════════

def render_video_clip(video_path: str, item: dict, output_path: str,
                      video_w: int, video_h: int,
                      is_last: bool, tmp_dir: str) -> float:
    """
    Rend le clip vidéo (sans audio) pour un segment.
    Retourne la durée réelle du clip en ms (0 si échec).

    CAS A/B — Découpe simple [seg_start → next_start]
    CAS C   — Loop de la portion parole jusqu'à couvrir le TTS
    """
    case        = item["case"]
    seg_start_s = item["seg_start_s"]
    seg_end_s   = item["seg_end_s"]
    next_start_s= item["next_start_s"]
    tts_ms      = item["tts_ms"]
    stem        = Path(output_path).stem
    vf_scale    = f"scale={video_w}:{video_h},fps={OUTPUT_FPS}"

    # ── CAS A / B — découpe directe ───────────────────────────────────────
    if case in ("short", "match"):
        clip_dur_s = max(0.1, next_start_s - seg_start_s)
        ok = run_ffmpeg([
            "-ss", f"{seg_start_s:.3f}",
            "-t",  f"{clip_dur_s:.3f}",
            "-i",  video_path,
            "-vf", vf_scale,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", output_path,
        ], f"clip simple {stem}")

        if not ok:
            return 0.0

    # ── CAS C — loop de la portion parole ─────────────────────────────────
    else:
        speech_dur_s = max(0.2, seg_end_s - seg_start_s)
        tts_s        = tts_ms / 1000.0
        n_loops      = min(MAX_LOOPS, int(tts_s / speech_dur_s) + 1)

        loop_clips = []
        for k in range(n_loops):
            lp = os.path.join(tmp_dir, f"{stem}_lp{k}.mp4")
            ok = run_ffmpeg([
                "-ss", f"{seg_start_s:.3f}",
                "-t",  f"{speech_dur_s:.3f}",
                "-i",  video_path,
                "-vf", vf_scale,
                "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                "-an", lp,
            ], f"loop {k} {stem}")
            if ok and os.path.exists(lp):
                loop_clips.append(lp)

        # Silence vidéo restant après les loops (si budget > total loops)
        loop_total_s = speech_dur_s * len(loop_clips)
        remaining_s  = max(0.0, (next_start_s - seg_start_s) - loop_total_s)
        if remaining_s > 0.1 and seg_end_s < next_start_s:
            sil_start_s = seg_end_s
            avail_s     = min(remaining_s, next_start_s - sil_start_s)
            if avail_s > 0.05:
                sil_p = os.path.join(tmp_dir, f"{stem}_sil.mp4")
                ok = run_ffmpeg([
                    "-ss", f"{sil_start_s:.3f}",
                    "-t",  f"{avail_s:.3f}",
                    "-i",  video_path,
                    "-vf", vf_scale,
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
                    "-an", sil_p,
                ], f"sil {stem}")
                if ok and os.path.exists(sil_p):
                    loop_clips.append(sil_p)

        if not loop_clips:
            return 0.0

        concat_f = os.path.join(tmp_dir, f"{stem}_lcat.txt")
        with open(concat_f, "w") as f:
            for p in loop_clips:
                f.write(f"file '{p}'\n")

        ok = run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", concat_f,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", output_path,
        ], f"concat loops {stem}")

        for p in loop_clips:
            try:
                os.remove(p)
            except Exception:
                pass
        try:
            os.remove(concat_f)
        except Exception:
            pass

        if not ok:
            return 0.0

    # Pause freeze en fin de clip (sauf dernier segment)
    if not is_last:
        _append_freeze(output_path, PAUSE_MS, video_w, video_h,
                       OUTPUT_FPS, tmp_dir, stem)

    return get_clip_duration_ms(output_path)


# ══════════════════════════════════════════════════════════════════════════════
#  SOUS-TITRES ASS
# ══════════════════════════════════════════════════════════════════════════════

def _text_to_lines(text: str) -> list:
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


def _split_subtitle_events(text: str, start_ms: int, end_ms: int) -> list:
    MAX_PER_SCREEN = 3
    all_lines = _text_to_lines(text)
    if not all_lines:
        return [{"start_ms": start_ms, "end_ms": end_ms, "sub_text": ""}]

    screens = [all_lines[i:i + MAX_PER_SCREEN]
               for i in range(0, len(all_lines), MAX_PER_SCREEN)]

    if len(screens) == 1:
        return [{"start_ms": start_ms, "end_ms": end_ms,
                 "sub_text": "\n".join(screens[0])}]

    texts     = ["\n".join(s) for s in screens]
    wcs       = [len(t.split()) for t in texts]
    total_w   = sum(wcs) or 1
    total_ms  = max(end_ms - start_ms, 100)
    events, cursor = [], start_ms

    for i, (txt, wc) in enumerate(zip(texts, wcs)):
        is_last = (i == len(screens) - 1)
        fin = end_ms if is_last else cursor + int(total_ms * wc / total_w)
        events.append({"start_ms": int(cursor), "end_ms": int(fin), "sub_text": txt})
        cursor = fin

    return events


def _hex_to_ass(hex_color: str) -> str:
    h = hex_color.lstrip("#").upper()
    return (h[4:6] + h[2:4] + h[0:2]) if len(h) == 6 else "FFFFFF"


def _ms_to_ass(ms: int) -> str:
    ms = int(max(0, ms))
    h  = ms // 3_600_000; ms %= 3_600_000
    m  = ms // 60_000;    ms %= 60_000
    s  = ms // 1_000;     ms %= 1_000
    return f"{h}:{m:02d}:{s:02d}.{ms // 10:02d}"


def _generate_ass(events: list, video_w: int, video_h: int,
                  style: dict, output_path: str) -> bool:
    try:
        font_map   = {"calibri": "Calibri", "arial": "Arial",
                      "segoe": "Segoe UI", "georgia": "Georgia", "impact": "Impact"}
        font_name  = font_map.get(style.get("font_family", "calibri"), "Arial")
        font_size  = int(style.get("font_size", 48))
        pclr       = _hex_to_ass(style.get("text_color",    "FFFFFF"))
        oclr       = _hex_to_ass(style.get("outline_color", "000000"))
        bgclr      = _hex_to_ass(style.get("bg_color",      "000000"))
        ow         = int(style.get("outline_width", 2))
        shadow_d   = 2 if style.get("shadow", True) else 0
        bg_on      = bool(style.get("bg_enabled", True))
        bg_alpha   = hex(max(0, 255 - int(style.get("bg_opacity", 75) * 2.55)))[2:].upper().zfill(2)
        pos        = style.get("position", "bottom")
        margin_v   = int(style.get("margin", 60))
        alignment  = {"bottom": 2, "top": 8, "center": 5}.get(pos, 2)
        bstyle     = 3 if bg_on else 1
        back_color = f"&H{bg_alpha}{bgclr}&"
        ow_actual  = 0 if bg_on else ow

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
            f"&H00{pclr}&,&H000000FF&,&H00{oclr}&,{back_color},"
            f"1,0,0,0,100,100,0,0,{bstyle},{ow_actual},{shadow_d},"
            f"{alignment},10,10,{margin_v},1\n\n"
            f"[Events]\n"
            f"Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )

        sorted_ev    = sorted(events, key=lambda x: x["start_ms"])
        clean_events = []
        for i, ev in enumerate(sorted_ev):
            s = ev["start_ms"]
            e = ev["end_ms"]
            if i + 1 < len(sorted_ev):
                e = min(e, sorted_ev[i + 1]["start_ms"] - 50)
            if e > s:
                clean_events.append({**ev, "start_ms": s, "end_ms": e})

        lines = []
        for ev in clean_events:
            txt = ev.get("sub_text", "").strip()
            if txt:
                lines.append(
                    f"Dialogue: 0,{_ms_to_ass(ev['start_ms'])},"
                    f"{_ms_to_ass(ev['end_ms'])},Default,,0,0,0,,"
                    f"{txt.replace(chr(10), chr(92)+'N')}"
                )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(lines))
        logger.info(f"ASS : {len(lines)} sous-titres générés")
        return True
    except Exception as e:
        logger.error(f"Erreur ASS : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 1 : TRANSCRIPTION
# ══════════════════════════════════════════════════════════════════════════════

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
        ws_status(job_id, "Extraction audio en cours...")
        ws_progress(job_id, 1, 4, "Extraction audio")
        job.set_status(Job.Status.EXTRACTING)

        wav_path = str(job.wav_path)
        if extraire_audio_wav(video_path, output_wav=wav_path) is None:
            raise RuntimeError(
                "Extraction audio échouée. "
                "Vérifiez que ffmpeg est installé et que la vidéo n'est pas corrompue."
            )

        ws_status(job_id, "Audio extrait.")

        ws_status(job_id, "Génération waveform...")
        ws_progress(job_id, 2, 4, "Waveform")
        waveform = _extraire_waveform(wav_path)
        job.waveform_data = waveform
        job.save(update_fields=["waveform_data"])
        ws_send(job_id, "waveform", data=waveform)

        ws_status(job_id, "Extraction vignettes...")
        ws_progress(job_id, 3, 4, "Vignettes")
        thumbs = _extraire_vignettes(video_path, job)
        if thumbs:
            job.thumbnail_paths = thumbs
            job.save(update_fields=["thumbnail_paths"])

        ws_status(job_id,
            "Transcription Faster-Whisper en cours... "
            "(première utilisation : téléchargement du modèle ~1.5 Go)"
        )
        ws_progress(job_id, 4, 4, "Transcription")
        job.set_status(Job.Status.TRANSCRIBING)

        provider = STTProviderFactory.create("faster_whisper")
        if not provider.est_disponible():
            raise RuntimeError("Faster-Whisper non disponible. Vérifiez l'installation pip.")

        segments_raw = provider.transcrire(wav_path, langue=langue)
        if not segments_raw:
            raise RuntimeError(
                "Aucun segment détecté. "
                "Vérifiez que la vidéo contient de la parole audible et claire."
            )

        nb = Segment.bulk_create_from_stt(job, segments_raw)
        job.set_status(Job.Status.TRANSCRIBED)
        send_job_notification(job, "transcribed")
        ws_send(job_id, "segments", data=segments_raw)
        ws_status(job_id,
            f"{nb} segments transcrits. "
            "Relisez et corrigez le script avant de passer à la synthèse vocale."
        )
        return {"status": "success", "nb_segments": nb}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur transcription {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"Erreur : {error_msg}")
        send_job_notification(job, "error")
        return {"status": "error", "message": error_msg}


# ══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 2 : SYNTHÈSE VOCALE
# ══════════════════════════════════════════════════════════════════════════════

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
            key_name = "ELEVENLABS_API_KEY" if tts_engine == "elevenlabs" else "CARTESIA_API_KEY"
            raise RuntimeError(
                f"Clé API manquante ou invalide ({key_name}). "
                "Vérifiez votre fichier .env."
            )

        output_dir = str(job.output_dir / "tts")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Toujours lire depuis la base — timecodes Whisper originaux
        db_segments = list(
            Segment.objects.filter(job=job).order_by("index")
            .values("index", "start_ms", "end_ms", "text")
        )
        if not db_segments:
            raise RuntimeError(
                "Aucun segment trouvé en base. "
                "La transcription doit être effectuée avant la synthèse."
            )

        total  = len(db_segments)
        errors = []
        plan_items = []

        ws_status(job_id, f"Synthèse vocale ({provider.nom}) — {total} segments...")

        for i, seg in enumerate(db_segments):
            texte = (seg["text"] or "").strip()
            if not texte:
                ws_status(job_id, f"Segment {i+1} vide — ignoré.")
                continue

            ws_send(job_id, "tts_progress", current=i + 1, total=total)
            ws_progress(job_id, i + 1, total, f"Synthèse {i+1}/{total}")

            filename = f"seg_{seg['index']:04d}.wav"
            try:
                chemin = provider.generer(
                    texte=texte, voix=voix, output_dir=output_dir,
                    filename=filename, langue=langue,
                )
                if chemin and os.path.exists(chemin):
                    actual_ms = get_wav_duration_ms(chemin)
                    if actual_ms < MIN_TTS_MS:
                        logger.warning(
                            f"Segment {i+1} TTS anormalement court ({actual_ms:.0f}ms)"
                        )
                    Segment.objects.filter(job=job, index=seg["index"]).update(
                        audio_file=chemin
                    )
                    plan_items.append({
                        "index":         seg["index"],
                        "start_ms":      seg["start_ms"],
                        "end_ms":        seg["end_ms"],
                        "actual_tts_ms": round(actual_ms, 1),
                        "tts_path":      chemin,
                        "text":          texte,
                    })
                else:
                    errors.append(f"Seg {i+1}: fichier audio absent")
            except Exception as e:
                errors.append(f"Seg {i+1}: {str(e)}")
                logger.warning(f"TTS segment {i} échoué : {e}")

        if not plan_items:
            raise RuntimeError(
                "Aucun fichier audio généré. "
                "Vérifiez la clé API et la connexion internet."
            )

        plan_path = str(job.output_dir / "synthesis_plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(
                {"job_id": str(job_id), "langue": langue, "plan": plan_items},
                f, ensure_ascii=False, indent=2,
            )

        nb_ok = len(plan_items)
        if errors:
            ws_status(job_id,
                f"{nb_ok}/{total} générés. "
                + str(len(errors)) + " erreurs : " + "; ".join(errors[:3])
            )
        else:
            ws_status(job_id, f"{nb_ok}/{total} segments générés. Lancez l'export.")

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done", nb_ok=nb_ok, nb_total=total)
        send_job_notification(job, "tts_done")
        return {"status": "success", "nb_generated": nb_ok}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur synthèse {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"Erreur : {error_msg}")
        send_job_notification(job, "error")
        return {"status": "error", "message": error_msg}


# ══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 3 : EXPORT FINAL v10
# ══════════════════════════════════════════════════════════════════════════════

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
    ws_status(job_id, "Démarrage export v10 — vidéo maître du temps...")

    tmp_dir = None
    cases   = {"short": 0, "match": 0, "long": 0}

    try:
        video_path  = str(job.video_file.path)
        work_dir    = job.output_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
        exports_dir.mkdir(parents=True, exist_ok=True)
        final_path  = str(exports_dir / "final.mp4")
        tmp_dir     = tempfile.mkdtemp(dir=str(work_dir), prefix="tmp_v10_")

        video_w, video_h = get_video_dimensions(video_path)
        video_dur        = get_video_duration(video_path)
        ws_status(job_id, f"Vidéo source : {video_w}x{video_h} — {video_dur:.1f}s")

        # ── 1. Charger le plan ────────────────────────────────────────────
        ws_progress(job_id, 1, 6, "Chargement plan")
        plan_path = str(work_dir / "synthesis_plan.json")
        if not os.path.exists(plan_path):
            raise RuntimeError(
                "Plan de synthèse introuvable. "
                "Lancez la synthèse vocale avant l'export."
            )

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)

        plan_items = []
        for item in plan_data.get("plan", []):
            tp = item.get("tts_path", "")
            if not tp or not os.path.exists(tp):
                ws_status(job_id, f"TTS manquant seg {item.get('index','?')} — ignoré")
                continue
            ms = get_wav_duration_ms(tp)
            if ms < MIN_TTS_MS:
                ws_status(job_id, f"TTS trop court ({ms:.0f}ms) seg {item.get('index','?')} — ignoré")
                continue
            plan_items.append({**item, "actual_tts_ms": ms})

        if not plan_items:
            raise RuntimeError(
                "Aucun fichier TTS valide. Relancez la synthèse vocale."
            )

        ws_status(job_id, f"{len(plan_items)} segments chargés.")

        # ── 2. Calcul budgets ─────────────────────────────────────────────
        ws_progress(job_id, 2, 6, "Calcul budgets-temps")
        plan_items = compute_budgets(plan_items, video_dur)

        for item in plan_items:
            cases[item["case"]] += 1
            ws_status(job_id,
                f"  [{item['index']+1}] budget={item['budget_ms']:.0f}ms "
                f"TTS={item['actual_tts_ms']:.0f}ms "
                f"ratio={item['ratio']:.2f} => {item['case'].upper()}"
            )

        ws_status(job_id,
            f"Strategies : {cases['match']} match / "
            f"{cases['short']} courts / {cases['long']} longs (loop)"
        )

        # ── 3. Rendu clips ────────────────────────────────────────────────
        ws_progress(job_id, 3, 6, "Rendu clips")
        part_files      = []
        timeline_ms     = 0.0
        audio_cursor_ms = 0.0
        subtitle_events = []
        total           = len(plan_items)

        for i, item in enumerate(plan_items):
            ws_progress(job_id, i + 1, total, f"Clip {i+1}/{total} [{item['case']}]")
            part_path = os.path.join(tmp_dir, f"part_{i:04d}.mp4")
            is_last   = (i == total - 1)

            clip_dur_ms = render_video_clip(
                video_path=video_path, item=item, output_path=part_path,
                video_w=video_w, video_h=video_h,
                is_last=is_last, tmp_dir=tmp_dir,
            )

            if clip_dur_ms <= 0 or not os.path.exists(part_path):
                ws_status(job_id, f"Clip {i+1} echoue — ignore.")
                continue

            tts_start_ms = max(timeline_ms, audio_cursor_ms)
            tts_end_ms   = tts_start_ms + item["tts_ms"]

            sub_evs = _split_subtitle_events(
                item["text"], int(tts_start_ms), int(tts_end_ms)
            )
            subtitle_events.extend(sub_evs)

            part_files.append({
                "path":         part_path,
                "clip_dur_ms":  clip_dur_ms,
                "tts_path":     item["tts_path"],
                "tts_start_ms": tts_start_ms,
                "tts_ms":       item["tts_ms"],
            })

            timeline_ms     += clip_dur_ms
            audio_cursor_ms  = tts_end_ms

        if not part_files:
            raise RuntimeError(
                "Aucun clip vidéo généré. "
                "Vérifiez que ffmpeg est installé et la vidéo source accessible."
            )

        ws_status(job_id,
            f"{len(part_files)} clips — duree totale {timeline_ms/1000:.1f}s"
        )

        # ── 4. Assemblage ─────────────────────────────────────────────────
        ws_progress(job_id, 4, 6, "Assemblage")
        concat_list = os.path.join(tmp_dir, "concat.txt")
        with open(concat_list, "w") as f:
            for p in part_files:
                f.write(f"file '{p['path']}'\n")

        assembled = str(work_dir / "assembled.mp4")
        ok = run_ffmpeg([
            "-f", "concat", "-safe", "0", "-i", concat_list,
            "-c:v", "libx264", "-preset", "fast", "-crf", "18",
            assembled,
        ], "assemblage")
        if not ok:
            raise RuntimeError(
                "Assemblage vidéo échoué. "
                "Vérifiez l'espace disque disponible."
            )

        # ── 5. Audio composite ────────────────────────────────────────────
        ws_progress(job_id, 5, 6, "Mixage audio")
        ws_status(job_id, "Mixage piste vocale...")

        sample_rate   = 22050
        total_samples = int((timeline_ms / 1000 + 3) * sample_rate)
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
                    ss = int(p["tts_start_ms"] * sample_rate / 1000)
                    es = ss + len(tts_arr)
                    if es > len(buffer):
                        buffer = np.concatenate([
                            buffer,
                            np.zeros(es - len(buffer) + sample_rate, dtype=np.int16),
                        ])
                    buffer[ss:es] = np.clip(
                        buffer[ss:es].astype(np.int32) + tts_arr.astype(np.int32),
                        np.iinfo(np.int16).min, np.iinfo(np.int16).max,
                    ).astype(np.int16)
            except Exception as e:
                logger.warning(f"Mixage audio ignore : {e}")

        composite_wav = str(work_dir / "composite.wav")
        with wave.open(composite_wav, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(buffer.tobytes())

        # ── Sous-titres ───────────────────────────────────────────────────
        ass_path = None
        if subtitles_enabled and subtitle_events:
            ass_path = str(work_dir / "subtitles.ass")
            if not _generate_ass(subtitle_events, video_w, video_h, style, ass_path):
                ws_status(job_id, "Sous-titres non générés — export sans sous-titres.")
                ass_path = None

        # ── 6. Encodage final ─────────────────────────────────────────────
        ws_progress(job_id, 6, 6, "Encodage final")
        ws_status(job_id, "Encodage final en cours...")

        if ass_path and os.path.exists(ass_path):
            ass_esc = ass_path.replace("\\", "/").replace(":", "\\:")
            cmd = [
                "-i", assembled, "-i", composite_wav,
                "-filter:v", f"ass='{ass_esc}'",
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "17",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", final_path,
            ]
        else:
            cmd = [
                "-i", assembled, "-i", composite_wav,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", final_path,
            ]

        if not run_ffmpeg(cmd, "encodage final") or not os.path.exists(final_path):
            run_ffmpeg([
                "-i", assembled, "-i", composite_wav,
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
                "-shortest", final_path,
            ], "encodage secours")

        # Nettoyage
        for tmp in [composite_wav, assembled]:
            try: os.remove(tmp)
            except Exception: pass
        try: shutil.rmtree(tmp_dir)
        except Exception: pass
        tmp_dir = None

        if not os.path.exists(final_path) or os.path.getsize(final_path) < 10_000:
            raise RuntimeError(
                "Le fichier final est absent ou corrompu. "
                "Vérifiez les logs ffmpeg pour diagnostiquer."
            )

        size_mb      = os.path.getsize(final_path) / (1024 * 1024)
        download_url = f"{settings.MEDIA_URL.rstrip('/')}/exports/{job.pk}/final.mp4"

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "export_done",
                download_url=download_url,
                file_size_mb=round(size_mb, 1))

        ws_status(job_id,
            f"Export termine — {size_mb:.1f} Mo | "
            f"{timeline_ms/1000:.1f}s | "
            f"{len(subtitle_events)} sous-titres | "
            f"match:{cases['match']} court:{cases['short']} long:{cases['long']}"
        )
        send_job_notification(job, "export_done", download_url=download_url)
        return {"status": "success", "download_url": download_url}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur export {job_id}: {error_msg}", exc_info=True)
        if tmp_dir:
            try: shutil.rmtree(tmp_dir)
            except Exception: pass
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"Erreur export : {error_msg}")
        send_job_notification(job, "error")
        return {"status": "error", "message": error_msg}


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS PRIVÉS
# ══════════════════════════════════════════════════════════════════════════════

def _extraire_waveform(wav_path: str, nb_points: int = 500) -> list:
    try:
        with wave.open(wav_path, "rb") as wf:
            n = wf.getnframes()
            if n == 0:
                return [0.0] * nb_points
            samples = np.frombuffer(wf.readframes(n), dtype=np.int16).astype(np.float32)
        block = max(1, len(samples) // nb_points)
        return [
            round(float(np.max(np.abs(
                samples[i * block:min((i + 1) * block, len(samples))]
            ))) / 32767, 4)
            for i in range(nb_points)
        ]
    except Exception:
        return [0.0] * nb_points


def _extraire_vignettes(video_path: str, job, nb_max: int = 10) -> list:
    try:
        import cv2
        cap = cv2.VideoCapture(video_path)
        if not cap.isOpened():
            return []
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
            if len(paths) >= nb_max:
                break
        cap.release()
        return paths
    except Exception:
        return []