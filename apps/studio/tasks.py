"""
apps/studio/tasks.py — TutoBuilder Vision v13
══════════════════════════════════════════════════════════════════════════════

RÈGLE ABSOLUE : LA VOIX N'EST JAMAIS MODIFIÉE
──────────────────────────────────────────────
La voix TTS est toujours à x1.0. Jamais d'atempo. Jamais d'accélération.
Si le TTS est plus long que le segment parole → on gèle la dernière frame
de la vidéo le temps nécessaire (max 5s). La voix finit naturellement.

DÉCOUPE VIDÉO
─────────────
  Clip parole  : source[seg.start → seg.end]         x1.0 toujours
  Clip silence : source[seg.end → next_seg.start]    vitesse selon durée
  Clip freeze  : dernière frame gelée si TTS déborde  max 5s
  Clip intro   : source[0 → first_seg.start]         si > 0
  Clip outro   : source[last_seg.end → video_end]    si > 0

VITESSE DES SILENCES
────────────────────
  < 2s   → x1.0  (respiration naturelle)
  2-5s   → x2.0
  5-15s  → x4.0
  > 15s  → x8.0

GESTION DU DÉBORDEMENT TTS
────────────────────────────
  tts_ms ≤ parole_ms         → copie directe + padding silence audio
  tts_ms > parole_ms         → freeze de la dernière frame
  overflow ≤ FREEZE_MAX_MS   → gel ok, voix naturelle
  overflow > FREEZE_MAX_MS   → bloqué côté frontend à la sauvegarde
                               (le backend applique quand même le freeze
                                plafonné comme sécurité résiduelle)

CORRECTIONS WHISPER (avant export)
───────────────────────────────────
  C1 : Overlap → forcer seg[i].start = seg[i-1].end
  C2 : Intro silencieuse → clip muet [0 → seg[0].start]
  C3 : Outro silencieux → clip muet [last.end → video_end]
  C4 : Durée nulle/négative → ignorer le segment
  C5 : Texte vide/ponctuation → ignorer, traiter en silence

GARANTIES FFMPEG
────────────────
  - fps forcé sur CHAQUE clip (jamais -c copy) → élimine les frames dupliquées
  - durée min 0.1s avant tout appel ffmpeg
  - -shortest sur l'encodage final
  - vérification existence + taille de chaque fichier produit
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

# ══════════════════════════════════════════════════════════════════════════════
#  CONSTANTES
# ══════════════════════════════════════════════════════════════════════════════

OUTPUT_FPS          = 25
OUTPUT_FPS_STR      = "25"
SAMPLE_RATE         = 22050
# La voix n'est JAMAIS acceleree — x1.0 toujours
# Si TTS deborde → freeze de la derniere frame (max 5s)
FREEZE_MAX_MS       = 5000.0
MIN_CLIP_DURATION   = 0.10
MIN_SEGMENT_CHARS   = 3
SILENCE_X1_MAX      = 2.0
SILENCE_X2_MAX      = 5.0
SILENCE_X4_MAX      = 15.0
MIN_TTS_BYTES       = 2000
VIDEO_PRESET        = "fast"
VIDEO_CRF           = "20"
SUBTITLE_MAX_CHARS  = 44
GAP_BETWEEN_TTS_MS  = 80


# ══════════════════════════════════════════════════════════════════════════════
#  WS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def ws_send(job_id, msg_type, **kwargs):
    try:
        cl = get_channel_layer()
        if cl:
            async_to_sync(cl.group_send)(
                f"job_{job_id}", {"type": f"job.{msg_type}", **kwargs}
            )
    except Exception:
        pass


def ws_status(job_id, msg: str, level: str = "info"):
    ws_send(job_id, "status", message=msg, level=level)
    fn = logger.warning if level == "warn" else (
         logger.error   if level == "err"  else logger.info)
    fn(f"[{job_id}] {msg}")


def ws_progress(job_id, step: int, total: int, label: str):
    ws_send(job_id, "progress", step=step, total=total, label=label,
            percent=int((step / max(total, 1)) * 100))


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES AUDIO
# ══════════════════════════════════════════════════════════════════════════════

def get_wav_duration_ms(wav_path: str) -> float:
    """Lit la durée réelle d'un WAV. Corrige le bug Cartesia (INT32_MAX)."""
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
            return max(400.0, (os.path.getsize(wav_path) - 44) / (SAMPLE_RATE * 2) * 1000.0)
        except Exception:
            return 3000.0


def validate_tts_file(path: str) -> tuple[bool, str]:
    if not path:
        return False, "chemin vide"
    if not os.path.exists(path):
        return False, f"fichier introuvable : {path}"
    size = os.path.getsize(path)
    if size < MIN_TTS_BYTES:
        return False, f"fichier trop petit ({size} o)"
    try:
        dur = get_wav_duration_ms(path)
        if dur < 100:
            return False, f"durée invalide ({dur:.0f}ms)"
        return True, ""
    except Exception as e:
        return False, f"erreur lecture WAV : {e}"


def build_silence_wav(duration_ms: float, output_path: str) -> bool:
    """Génère un fichier WAV de silence pur."""
    n = max(1, int(duration_ms * SAMPLE_RATE / 1000))
    try:
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(b'\x00' * n * 2)
        return True
    except Exception as e:
        logger.error(f"build_silence_wav : {e}")
        return False


def copy_tts_exact(tts_path: str, output_path: str) -> float:
    """
    Copie le TTS sans aucune modification de vitesse.
    La voix est TOUJOURS a x1.0 — c'est une regle absolue.
    Retourne la duree reelle du fichier (ms).
    """
    r = subprocess.run([
        "ffmpeg", "-y", "-i", tts_path,
        "-ar", str(SAMPLE_RATE), "-ac", "1", "-sample_fmt", "s16",
        output_path,
    ], capture_output=True, text=True)
    if r.returncode == 0 and os.path.exists(output_path):
        return get_wav_duration_ms(output_path)
    shutil.copy2(tts_path, output_path)
    return get_wav_duration_ms(tts_path)


def make_freeze_frame(source_clip: str, duration_ms: float,
                      output_path: str, video_w: int, video_h: int,
                      source_video: str = None, source_time_s: float = 0.0) -> bool:
    if duration_ms < 50:
        return False
    dur_s      = duration_ms / 1000.0
    frame_path = output_path + "_last.jpg"

    r1 = subprocess.run([
        "ffmpeg", "-y", "-i", source_clip,
        "-frames:v", "1", "-q:v", "2", frame_path,
    ], capture_output=True, text=True)

    logger.info(f"make_freeze_frame tentative1 returncode={r1.returncode} frame_exists={os.path.exists(frame_path)} source_video={source_video}")

    if (r1.returncode != 0 or not os.path.exists(frame_path)) and source_video:
        seek_s = max(0.0, source_time_s - 0.1)
        logger.info(f"make_freeze_frame fallback seek={seek_s:.3f}s source={source_video}")
        r1 = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{seek_s:.6f}",
            "-i", source_video,
            "-frames:v", "1", "-q:v", "2", frame_path,
        ], capture_output=True, text=True)
        logger.info(f"make_freeze_frame fallback returncode={r1.returncode} stderr={r1.stderr[-200:]}")

    if r1.returncode != 0 or not os.path.exists(frame_path):
        logger.error(f"make_freeze_frame : impossible d'extraire une frame")
        return False

    r2 = subprocess.run([
        "ffmpeg", "-y",
        "-loop", "1", "-i", frame_path,
        "-t", f"{dur_s:.4f}",
        "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h}:flags=lanczos",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", VIDEO_CRF,
        "-an", output_path,
    ], capture_output=True, text=True)

    try:
        os.remove(frame_path)
    except Exception:
        pass

    if r2.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"make_freeze_frame clip : {r2.stderr[-200:]}")
        return False
    return True

# ══════════════════════════════════════════════════════════════════════════════
#  UTILITAIRES VIDÉO
# ══════════════════════════════════════════════════════════════════════════════

def get_video_duration(video_path: str) -> float:
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def get_video_dimensions(video_path: str) -> tuple[int, int]:
    r = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "csv=p=0", video_path,
    ], capture_output=True, text=True)
    try:
        w, h = r.stdout.strip().split(",")
        return int(w), int(h)
    except Exception:
        return 1920, 1080


def extract_clip(video_path: str, start_s: float, end_s: float,
                 output_path: str, video_w: int, video_h: int,
                 speed: float = 1.0) -> bool:
    """
    Extrait un clip vidéo [start_s → end_s] à la vitesse `speed`.
    fps forcé sur chaque clip — garantit zéro frame dupliquée au concat.

    speed > 1.0 → setpts=1/speed*PTS (accélération)
    speed = 1.0 → découpe pure sans modification
    """
    src_dur = end_s - start_s
    if src_dur < MIN_CLIP_DURATION:
        logger.warning(f"extract_clip ignoré : durée {src_dur:.3f}s < min")
        return False

    # setpts pour la vitesse
    pts = f"setpts={1.0/speed:.6f}*PTS," if speed != 1.0 else ""
    vf  = f"{pts}fps={OUTPUT_FPS},scale={video_w}:{video_h}:flags=lanczos"

    r = subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{start_s:.6f}",
        "-t",  f"{src_dur:.6f}",
        "-i",  video_path,
        "-vf", vf,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", VIDEO_CRF,
        "-an",
        output_path,
    ], capture_output=True, text=True)

    if r.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"extract_clip [{start_s:.2f}→{end_s:.2f}s x{speed}] : {r.stderr[-300:]}")
        return False
    return True


def silence_speed(duration_s: float) -> float:
    """Retourne la vitesse d'accélération pour un silence de durée donnée."""
    if duration_s < SILENCE_X1_MAX:
        return 1.0
    if duration_s < SILENCE_X2_MAX:
        return 2.0
    if duration_s < SILENCE_X4_MAX:
        return 4.0
    return 8.0


def concat_video_clips(clip_paths: list[str], output_path: str,
                       tmp_dir: Path) -> bool:
    """Concatène des clips via le demuxer concat de ffmpeg."""
    if not clip_paths:
        return False
    concat_f = str(tmp_dir / "_concat.txt")
    with open(concat_f, "w", encoding="utf-8") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    r = subprocess.run([
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0", "-i", concat_f,
        "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", VIDEO_CRF,
        "-an",
        output_path,
    ], capture_output=True, text=True)
    try:
        os.remove(concat_f)
    except Exception:
        pass
    if r.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"concat_video_clips échoué : {r.stderr[-300:]}")
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  CORRECTIONS WHISPER
# ══════════════════════════════════════════════════════════════════════════════

def is_empty_text(text: str) -> bool:
    """
    Cas C5 : texte vide, ponctuation seule, hallucination Whisper.
    Retourne True si le texte ne contient pas de contenu réel à synthétiser.
    """
    t = (text or "").strip()
    if len(t) < MIN_SEGMENT_CHARS:
        return True
    # Pas de lettre ni chiffre → ponctuation/symboles seuls
    if not re.search(r'[a-zA-Z0-9\u00C0-\u024F\u0400-\u04FF]', t):
        return True
    return False


def sanitize_segments(segments: list[dict], video_duration_s: float) -> list[dict]:
    """
    Corrige toutes les anomalies Whisper avant d'aller plus loin.

    C1 : Chevauchements → forcer start[i] = end[i-1]
    C4 : Durée nulle ou négative → supprimer
    C5 : Texte vide/ponctuation → marquer empty=True (pas de TTS)

    Retourne une liste propre triée par start_ms.
    """
    video_end_ms = int(video_duration_s * 1000)

    # Tri par start_ms
    segs = sorted(segments, key=lambda s: s["start_ms"])

    clean = []
    prev_end_ms = 0

    for seg in segs:
        start_ms = int(seg["start_ms"])
        end_ms   = int(seg["end_ms"])

        # C4 : durée nulle ou négative
        if end_ms <= start_ms:
            logger.warning(f"Segment idx={seg.get('index','?')} ignoré : durée nulle/négative ({start_ms}→{end_ms})")
            continue

        # C1 : chevauchement avec le segment précédent
        if start_ms < prev_end_ms:
            logger.warning(
                f"Segment idx={seg.get('index','?')} : overlap corrigé "
                f"{start_ms}ms → {prev_end_ms}ms"
            )
            start_ms = prev_end_ms
            if end_ms <= start_ms:
                continue  # réduit à rien après correction

        # Clamp à la durée vidéo
        end_ms = min(end_ms, video_end_ms)
        if end_ms <= start_ms:
            continue

        # C5 : texte vide
        empty = is_empty_text(seg.get("text", ""))

        clean.append({
            **seg,
            "start_ms": start_ms,
            "end_ms":   end_ms,
            "empty":    empty,
        })
        prev_end_ms = end_ms

    return clean


# ══════════════════════════════════════════════════════════════════════════════
#  SOUS-TITRES ASS
# ══════════════════════════════════════════════════════════════════════════════

def _text_to_lines(text: str, max_chars: int = SUBTITLE_MAX_CHARS) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip() if cur else w
        if len(t) <= max_chars:
            cur = t
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def _split_subtitle_events(text: str, start_ms: int, end_ms: int) -> list[dict]:
    MAX_LINES = 3
    all_lines = _text_to_lines(text)
    if not all_lines:
        return []
    screens = [all_lines[i:i+MAX_LINES] for i in range(0, len(all_lines), MAX_LINES)]
    if len(screens) == 1:
        return [{"start_ms": start_ms, "end_ms": end_ms,
                 "sub_text": "\n".join(screens[0])}]
    total_words = sum(len(" ".join(s).split()) for s in screens) or 1
    total_ms    = max(end_ms - start_ms, 100)
    events, cur = [], start_ms
    for i, screen in enumerate(screens):
        txt  = "\n".join(screen)
        nb   = len(txt.split())
        last = (i == len(screens) - 1)
        dur  = int(total_ms * nb / total_words)
        fin  = end_ms if last else cur + dur
        events.append({"start_ms": int(cur), "end_ms": int(fin), "sub_text": txt})
        cur = fin
    return events


def _hex_to_ass(h: str) -> str:
    h = h.lstrip("#").upper()
    return (h[4:6] + h[2:4] + h[0:2]) if len(h) == 6 else "FFFFFF"


def _ms_to_ass(ms: int) -> str:
    ms = int(max(0, ms))
    h  = ms // 3_600_000; ms %= 3_600_000
    m  = ms // 60_000;    ms %= 60_000
    s  = ms // 1_000;     ms %= 1_000
    return f"{h}:{m:02d}:{s:02d}.{ms//10:02d}"


def generate_ass(events: list[dict], video_w: int, video_h: int,
                 style: dict, output_path: str) -> bool:
    try:
        font_map  = {"calibri":"Calibri","arial":"Arial","segoe":"Segoe UI",
                     "georgia":"Georgia","impact":"Impact"}
        font_name = font_map.get(style.get("font_family", "calibri"), "Arial")
        font_size = int(style.get("font_size", 48))
        prim_clr  = _hex_to_ass(style.get("text_color",    "FFFFFF"))
        out_clr   = _hex_to_ass(style.get("outline_color", "000000"))
        bg_clr    = _hex_to_ass(style.get("bg_color",      "000000"))
        outline_w = int(style.get("outline_width", 2))
        shadow_d  = 2 if style.get("shadow", True) else 0
        bg_on     = bool(style.get("bg_enabled", True))
        bg_opacity= int(style.get("bg_opacity", 75))
        bg_alpha  = hex(max(0, 255 - int(bg_opacity * 2.55)))[2:].upper().zfill(2)
        position  = style.get("position", "bottom")
        margin_v  = int(style.get("margin", 60))
        alignment = {"bottom": 2, "top": 8, "center": 5}.get(position, 2)
        bstyle    = 3 if bg_on else 1
        back_color= f"&H{bg_alpha}{bg_clr}&"
        ow_actual = outline_w if not bg_on else 0

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
            f"&H00{prim_clr}&,&H000000FF&,&H00{out_clr}&,{back_color},"
            f"1,0,0,0,100,100,0,0,{bstyle},{ow_actual},{shadow_d},"
            f"{alignment},10,10,{margin_v},1\n\n"
            f"[Events]\n"
            f"Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text\n"
        )

        sorted_ev = sorted(events, key=lambda x: x["start_ms"])
        # Éviter les chevauchements entre événements sous-titres
        clean_ev = []
        for i, ev in enumerate(sorted_ev):
            s = ev["start_ms"]
            e = ev["end_ms"]
            if i + 1 < len(sorted_ev):
                e = min(e, sorted_ev[i+1]["start_ms"] - 50)
            if e > s:
                clean_ev.append({**ev, "start_ms": s, "end_ms": e})

        lines = []
        for ev in clean_ev:
            txt = (ev.get("sub_text") or "").strip()
            if txt:
                lines.append(
                    f"Dialogue: 0,{_ms_to_ass(ev['start_ms'])},"
                    f"{_ms_to_ass(ev['end_ms'])},Default,,0,0,0,,"
                    f"{txt.replace(chr(10), chr(92)+'N')}"
                )

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(header + "\n".join(lines))
        logger.info(f"ASS généré : {len(lines)} événements")
        return True
    except Exception as e:
        logger.error(f"generate_ass : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS PRIVÉS (transcription)
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
            round(float(np.max(np.abs(samples[i*block:min((i+1)*block, len(samples))])) / 32767), 4)
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
                "L'extraction audio a échoué. "
                "Vérifiez que ffmpeg est installé et que la vidéo n'est pas corrompue."
            )
        ws_status(job_id, "Audio extrait.", "ok")

        ws_progress(job_id, 2, 4, "Waveform")
        waveform = _extraire_waveform(wav_path)
        job.waveform_data = waveform
        job.save(update_fields=["waveform_data"])
        ws_send(job_id, "waveform", data=waveform)

        ws_progress(job_id, 3, 4, "Vignettes")
        thumbs = _extraire_vignettes(video_path, job)
        if thumbs:
            job.thumbnail_paths = thumbs
            job.save(update_fields=["thumbnail_paths"])

        ws_status(job_id, "Transcription Faster-Whisper en cours...")
        ws_progress(job_id, 4, 4, "Transcription")
        job.set_status(Job.Status.TRANSCRIBING)

        provider = STTProviderFactory.create("faster_whisper")
        if not provider.est_disponible():
            raise RuntimeError(
                "Faster-Whisper n'est pas disponible. "
                "Exécutez : pip install faster-whisper"
            )

        segments_raw = provider.transcrire(wav_path, langue=langue)
        if not segments_raw:
            raise RuntimeError(
                "Aucun segment transcrit. La vidéo est peut-être silencieuse, "
                "ou la langue sélectionnée ne correspond pas à l'audio."
            )

        nb = Segment.bulk_create_from_stt(job, segments_raw)
        job.set_status(Job.Status.TRANSCRIBED)
        send_job_notification(job, "transcribed")
        ws_send(job_id, "segments", data=segments_raw)
        ws_status(job_id, f"{nb} segments transcrits.", "ok")
        return {"status": "success", "nb_segments": nb}

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur transcription {job_id}: {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        send_job_notification(job, "error")
        return {"status": "error", "message": msg}


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
        # Vérification clé API
        if tts_engine == "elevenlabs":
            api_key = getattr(settings, "ELEVENLABS_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError(
                    "Clé API ElevenLabs manquante. "
                    "Ajoutez ELEVENLABS_API_KEY dans votre .env et redémarrez."
                )
        elif tts_engine == "cartesia":
            api_key = getattr(settings, "CARTESIA_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError(
                    "Clé API Cartesia manquante. "
                    "Ajoutez CARTESIA_API_KEY dans votre .env et redémarrez."
                )
        else:
            api_key = ""

        provider = TTSProviderFactory.create(tts_engine, api_key=api_key)
        if not provider.est_disponible():
            raise RuntimeError(
                f"Le moteur vocal '{tts_engine}' n'est pas disponible. "
                "Vérifiez la clé API et votre connexion internet."
            )

        output_dir = str(job.output_dir / "tts")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # Toujours utiliser les timecodes en base (SACRÉS)
        db_segments = list(
            Segment.objects.filter(job=job).order_by("index")
            .values("index", "start_ms", "end_ms", "text")
        )
        if not db_segments:
            raise RuntimeError(
                "Aucun segment en base. "
                "Lancez la transcription et sauvegardez avant de synthétiser."
            )

        total  = len(db_segments)
        plan   = []
        echecs = []
        longs  = []

        ws_status(job_id, f"Synthèse vocale ({tts_engine}) — {total} segments...")

        for i, seg in enumerate(db_segments):
            idx   = seg["index"]
            texte = (seg["text"] or "").strip()

            ws_progress(job_id, i + 1, total, f"Segment {i+1}/{total}")
            ws_send(job_id, "tts_progress", current=i+1, total=total)

            # C5 : Filtrer les segments vides / ponctuation
            if is_empty_text(texte):
                ws_status(job_id, f"  Segment {idx} vide/ponctuation — ignoré", "warn")
                plan.append({
                    "index":    idx,
                    "start_ms": seg["start_ms"],
                    "end_ms":   seg["end_ms"],
                    "text":     texte,
                    "tts_path": None,
                    "tts_ms":   0,
                    "valid":    False,
                    "empty":    True,
                })
                continue

            # Estimation préventive
            dur_parole_ms = float(seg["end_ms"] - seg["start_ms"])
            est_tts_ms    = len(texte) / 14.0 * 1000
            if est_tts_ms > dur_parole_ms * 1.5:
                longs.append(idx)

            filename = f"seg_{idx:04d}.wav"
            filepath = None
            last_err = ""
            RETRY_DELAYS = [0, 3, 7]

            for attempt in range(3):
                from tts_providers import (TTSErrorCleAPI, TTSErrorReseau,
                                           TTSErrorAPI, TTSErrorConversion)
                wait = RETRY_DELAYS[attempt]
                if wait > 0:
                    import time
                    ws_status(job_id,
                        f"  Seg {idx} — attente {wait}s avant tentative {attempt+1}/3...",
                        "warn")
                    time.sleep(wait)

                try:
                    chemin = provider.generer(
                        texte=texte, voix=voix, output_dir=output_dir,
                        filename=filename, langue=langue,
                    )
                    valide, raison = validate_tts_file(chemin)
                    if valide:
                        filepath = chemin
                        break
                    else:
                        last_err = raison
                        ws_status(job_id,
                            f"  Seg {idx} tentative {attempt+1}/3 invalide : {raison}", "warn")

                except TTSErrorCleAPI as e:
                    last_err = str(e)
                    ws_send(job_id, "error", message=last_err)
                    echecs.append({"index": idx, "raison": last_err})
                    for seg_rest in db_segments[i+1:]:
                        echecs.append({"index": seg_rest["index"],
                                       "raison": "arrêté — erreur clé API"})
                        plan.append({
                            "index": seg_rest["index"],
                            "start_ms": seg_rest["start_ms"],
                            "end_ms":   seg_rest["end_ms"],
                            "text":     (seg_rest["text"] or "").strip(),
                            "tts_path": None, "tts_ms": 0,
                            "valid": False, "empty": False,
                        })
                    db_segments = db_segments[:i+1]
                    break

                except (TTSErrorReseau, TTSErrorAPI) as e:
                    last_err = str(e)
                    ws_status(job_id,
                        f"  Seg {idx} tentative {attempt+1}/3 : {last_err}", "warn")
                    if attempt == 2:
                        ws_send(job_id, "tts_segment_warn", index=idx, message=last_err)

                except TTSErrorConversion as e:
                    last_err = str(e)
                    ws_status(job_id, f"  Seg {idx} : {last_err}", "err")
                    ws_send(job_id, "tts_segment_warn", index=idx, message=last_err)
                    echecs.append({"index": idx, "raison": last_err})
                    break

                except Exception as e:
                    last_err = str(e)
                    ws_status(job_id,
                        f"  Seg {idx} tentative {attempt+1}/3 inattendu : {last_err}", "warn")

            if filepath:
                dur_ms = get_wav_duration_ms(filepath)
                Segment.objects.filter(job=job, index=idx).update(audio_file=filepath)
                plan.append({
                    "index":    idx,
                    "start_ms": seg["start_ms"],
                    "end_ms":   seg["end_ms"],
                    "text":     texte,
                    "tts_path": filepath,
                    "tts_ms":   round(dur_ms, 1),
                    "valid":    True,
                    "empty":    False,
                })
                ws_status(job_id, f"  Seg {idx} OK — {dur_ms:.0f}ms", "ok")
            else:
                if not any(e["index"] == idx for e in echecs):
                    echecs.append({"index": idx,
                                   "raison": last_err or "échec 3 tentatives"})
                plan.append({
                    "index":    idx,
                    "start_ms": seg["start_ms"],
                    "end_ms":   seg["end_ms"],
                    "text":     texte,
                    "tts_path": None,
                    "tts_ms":   0,
                    "valid":    False,
                    "empty":    False,
                })

        nb_valides = sum(1 for p in plan if p["valid"])
        nb_echecs  = len(echecs)

        plan_data = {
            "job_id":     str(job_id),
            "langue":     langue,
            "tts_engine": tts_engine,
            "tts_valid":  nb_valides > 0 and nb_echecs == 0,
            "nb_valides": nb_valides,
            "nb_total":   total,
            "echecs":     echecs,
            "plan":       plan,
        }
        plan_path = str(job.output_dir / "synthesis_plan.json")
        with open(plan_path, "w", encoding="utf-8") as f:
            json.dump(plan_data, f, ensure_ascii=False, indent=2)

        if nb_valides == 0:
            derniere = echecs[-1]["raison"] if echecs else "inconnue"
            raise RuntimeError(
                f"Aucun segment vocal généré ({nb_echecs} échec(s)). "
                f"Dernière erreur : {derniere}"
            )

        if echecs:
            ws_status(job_id,
                f"Avertissement : {nb_echecs} segment(s) échoué(s) — "
                f"{[e['index'] for e in echecs]}. "
                "Ces segments seront silencieux dans la vidéo finale.", "warn")

        if longs:
            ws_status(job_id,
                f"Avertissement : {len(longs)} segment(s) probablement trop longs "
                f"pour leur durée — {longs}. Raccourcissez leurs textes.", "warn")

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done",
                nb_ok=nb_valides, nb_total=total, nb_echecs=nb_echecs,
                echecs=[e["index"] for e in echecs])
        ws_status(job_id,
            f"Synthèse terminée : {nb_valides}/{total} segments.",
            "ok" if nb_echecs == 0 else "warn")
        send_job_notification(job, "tts_done")
        return {
            "status":     "success" if nb_echecs == 0 else "partial",
            "nb_valides": nb_valides,
            "nb_total":   total,
            "nb_echecs":  nb_echecs,
        }

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur synthèse {job_id}: {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        send_job_notification(job, "error")
        return {"status": "error", "message": msg}


# ══════════════════════════════════════════════════════════════════════════════
#  TÂCHE 3 : EXPORT v12
# ══════════════════════════════════════════════════════════════════════════════

@shared_task(bind=True, name="studio.export")
def task_export(self, job_id, subtitle_style=None):
    """
    Export v12 — logique déterministe, zéro répétition garantie.

    PIPELINE :
    ┌─────────────────────────────────────────────────────────┐
    │ 1. Charger le plan + corrections Whisper                │
    │ 2. Construire la timeline complète                      │
    │    (intro + [parole + silence]×N + outro)               │
    │ 3. Pour chaque entrée de la timeline :                  │
    │    a. Extraire le clip vidéo (x1.0 ou accéléré)         │
    │    b. Préparer l'audio (TTS ajusté ou silence)          │
    │ 4. Concaténer tous les clips vidéo                      │
    │ 5. Mixage audio composite                               │
    │ 6. Sous-titres ASS                                      │
    │ 7. Encodage final                                       │
    └─────────────────────────────────────────────────────────┘
    """
    from apps.studio.models import Job

    style             = subtitle_style or {}
    subtitles_enabled = style.get("enabled", True)

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.EXTRACTING)
    ws_status(job_id, "Démarrage de l'export v12...")

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
        ws_status(job_id, f"Source : {video_w}×{video_h} — {video_dur:.1f}s")

        # ── 1. Chargement du plan ─────────────────────────────────────────
        ws_progress(job_id, 1, 7, "Chargement du plan")
        plan_path = str(work_dir / "synthesis_plan.json")

        if not os.path.exists(plan_path):
            raise RuntimeError(
                "Plan de synthèse introuvable. "
                "Effectuez la synthèse vocale (étape 3) avant d'exporter."
            )

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)

        if not plan_data.get("tts_valid", True):
            nb_val = plan_data.get("nb_valides", 0)
            nb_tot = plan_data.get("nb_total",   0)
            raise RuntimeError(
                f"Synthèse incomplète ({nb_val}/{nb_tot} segments valides). "
                "Relancez la synthèse vocale avant d'exporter."
            )

        langue = plan_data.get("langue", "fr")

        # Valider les fichiers TTS présents
        raw_items = []
        for item in plan_data.get("plan", []):
            if item.get("empty", False):
                raw_items.append(item)
                continue
            if not item.get("valid", False):
                ws_status(job_id, f"  Seg {item.get('index','?')} ignoré (invalide)", "warn")
                raw_items.append({**item, "tts_path": None, "valid": False})
                continue
            ok, raison = validate_tts_file(item.get("tts_path", ""))
            if ok:
                raw_items.append(item)
            else:
                ws_status(job_id,
                    f"  Seg {item.get('index','?')} TTS introuvable : {raison}", "warn")
                raw_items.append({**item, "tts_path": None, "valid": False})

        if not any(it.get("valid") for it in raw_items):
            raise RuntimeError(
                "Aucun fichier audio valide. "
                "Relancez la synthèse — les fichiers précédents sont manquants."
            )

        # ── 2. Corrections Whisper + construction de la timeline ──────────
        ws_progress(job_id, 2, 7, "Construction de la timeline")

        # Sanitize les timecodes (C1, C4)
        segs_clean = sanitize_segments(raw_items, video_dur)

        # Construction de la timeline complète :
        # [intro?] [parole_0][silence_0] [parole_1][silence_1] … [outro?]
        #
        # Chaque entrée : {
        #   type       : "speech" | "silence"
        #   start_s    : float
        #   end_s      : float
        #   speed      : float (1.0 pour parole, calculé pour silence)
        #   tts_path   : str | None
        #   tts_ms     : float
        #   text       : str (pour sous-titres)
        #   seg_index  : int
        # }

        timeline = []

        # C2 : intro silencieuse
        first_start_s = segs_clean[0]["start_ms"] / 1000.0 if segs_clean else 0.0
        if first_start_s > MIN_CLIP_DURATION:
            spd = silence_speed(first_start_s)
            timeline.append({
                "type":      "silence",
                "start_s":   0.0,
                "end_s":     first_start_s,
                "speed":     spd,
                "tts_path":  None,
                "tts_ms":    0.0,
                "text":      "",
                "seg_index": -1,
            })
            ws_status(job_id,
                f"  Intro silencieuse {first_start_s:.1f}s détectée → x{spd:.0f}")

        TTS_WPM = 130  # mots/minute — même valeur que le frontend

        for i, seg in enumerate(segs_clean):
            start_s    = seg["start_ms"] / 1000.0
            end_s      = seg["end_ms"]   / 1000.0
            seg_dur_ms = float(seg["end_ms"] - seg["start_ms"])

            # Calculer la portion parlée réelle via le TTS
            tts_ms = float(seg.get("tts_ms", 0))
            if tts_ms < 100:
                # Pas de TTS disponible → estimer depuis le texte
                words  = len((seg.get("text") or "").split())
                tts_ms = (words / TTS_WPM) * 60.0 * 1000.0

            # La portion parole = du début jusqu'à la fin du TTS (max end_s)
            parole_end_s = min(start_s + tts_ms / 1000.0, end_s)

            # Entrée parole — juste la durée du TTS
            timeline.append({
                "type":      "speech",
                "start_s":   start_s,
                "end_s":     parole_end_s,
                "speed":     1.0,
                "tts_path":  seg.get("tts_path") if seg.get("valid") else None,
                "tts_ms":    tts_ms,
                "text":      seg.get("text", ""),
                "seg_index": seg.get("index", i),
                "empty":     seg.get("empty", False),
            })

            # Silence interne — reste du segment après la parole
            internal_sil_s = end_s - parole_end_s
            if internal_sil_s >= MIN_CLIP_DURATION:
                spd = silence_speed(internal_sil_s)
                timeline.append({
                    "type":      "silence",
                    "start_s":   parole_end_s,
                    "end_s":     end_s,
                    "speed":     spd,
                    "tts_path":  None,
                    "tts_ms":    0.0,
                    "text":      "",
                    "seg_index": -1,
                })

            # Silence entre segments (gap réel si Whisper a laissé un trou)
            if i + 1 < len(segs_clean):
                next_start_s = segs_clean[i+1]["start_ms"] / 1000.0
                gap_s        = next_start_s - end_s
                if gap_s >= MIN_CLIP_DURATION:
                    spd = silence_speed(gap_s)
                    timeline.append({
                        "type":      "silence",
                        "start_s":   end_s,
                        "end_s":     next_start_s,
                        "speed":     spd,
                        "tts_path":  None,
                        "tts_ms":    0.0,
                        "text":      "",
                        "seg_index": -1,
                    })

        # C3 : outro silencieux
        last_end_s = segs_clean[-1]["end_ms"] / 1000.0 if segs_clean else 0.0
        outro_dur  = video_dur - last_end_s
        if outro_dur > MIN_CLIP_DURATION:
            spd = silence_speed(outro_dur)
            timeline.append({
                "type":      "silence",
                "start_s":   last_end_s,
                "end_s":     video_dur,
                "speed":     spd,
                "tts_path":  None,
                "tts_ms":    0.0,
                "text":      "",
                "seg_index": -1,
            })
            ws_status(job_id,
                f"  Outro {outro_dur:.1f}s détecté → x{spd:.0f}")

        ws_status(job_id,
            f"Timeline : {len(timeline)} entrées "
            f"({sum(1 for t in timeline if t['type']=='speech')} parole + "
            f"{sum(1 for t in timeline if t['type']=='silence')} silence)")
    
        for entry in timeline:
            if entry["type"] == "silence":
                sil_dur = entry["end_s"] - entry["start_s"]
                ws_status(job_id,
                    f"  Silence [{entry['start_s']:.1f}s→{entry['end_s']:.1f}s] "
                    f"= {sil_dur:.1f}s → x{entry['speed']:.0f}")

        # ── 3. Extraction clips + préparation audio ───────────────────────
        ws_progress(job_id, 3, 7, "Extraction des clips vidéo")

        video_clips     = []   # chemins des clips vidéo dans l'ordre
        audio_segments  = []   # {path, start_ms, dur_ms}
        subtitle_events = []   # événements ASS
        audio_cursor_ms = 0.0  # position courante dans la timeline audio finale
        warnings        = []   # avertissements à afficher à l'user

        nb_total = len(timeline)

        for ti, entry in enumerate(timeline):
            ws_progress(job_id, ti + 1, nb_total,
                        f"Clip {ti+1}/{nb_total}")

            clip_path = str(parts_dir / f"clip_{ti:04d}.mp4")
            src_dur   = entry["end_s"] - entry["start_s"]

            # ── Clip vidéo ────────────────────────────────────────────────
            clip_ok = extract_clip(
                video_path = video_path,
                start_s    = entry["start_s"],
                end_s      = entry["end_s"],
                output_path= clip_path,
                video_w    = video_w,
                video_h    = video_h,
                speed      = entry["speed"],
            )
            if not clip_ok:
                ws_status(job_id,
                    f"  Clip {ti+1} ignoré (extraction échouée)", "warn")
                # On ne casse pas le pipeline : on passe juste à l'entrée suivante
                # L'audio cursor n'avance pas → les sous-titres restent calés
                continue

            clip_dur_ms = (src_dur / entry["speed"]) * 1000.0
            video_clips.append(clip_path)

            # ── Audio + gestion debordement TTS ──────────────────────────
            if entry["type"] == "speech" and entry["tts_path"]:
                tts_copy_path = str(parts_dir / f"tts_{ti:04d}.wav")
                parole_ms     = src_dur * 1000.0
                tts_ms        = float(entry["tts_ms"])

                # Copie exacte — voix JAMAIS modifiee
                actual_ms = copy_tts_exact(entry["tts_path"], tts_copy_path)

                # Debordement : TTS plus long que le clip parole
                overflow_ms = max(0.0, actual_ms - parole_ms)
                if overflow_ms >= 50:
                    freeze_ms  = min(overflow_ms, FREEZE_MAX_MS)
                    freeze_path = str(parts_dir / f"freeze_{ti:04d}.mp4")
                    # Chercher le clip suivant comme fallback si le clip parole est trop court
                    next_clip = str(parts_dir / f"clip_{ti+1:04d}.mp4") if ti + 1 < nb_total else None
                    freeze_ok = make_freeze_frame(
                        source_clip  = clip_path,
                        duration_ms  = freeze_ms,
                        output_path  = freeze_path,
                        video_w      = video_w,
                        video_h      = video_h,
                        source_video = video_path,        # ← nouveau
                        source_time_s= entry["end_s"],    # ← nouveau
                    )
                    if freeze_ok:
                        video_clips.append(freeze_path)
                        ws_status(job_id,
                            f"  Seg {entry['seg_index']} : voix deborde de "
                            f"{overflow_ms:.0f}ms → gel image {freeze_ms:.0f}ms", "info")
                    else:
                        ws_status(job_id,
                            f"  Seg {entry['seg_index']} : freeze echoue, debordement ignore", "warn")

                audio_segments.append({
                    "path":     tts_copy_path,
                    "start_ms": audio_cursor_ms,
                    "dur_ms":   actual_ms,
                })

                # Sous-titres cales sur le curseur audio
                sub_end_ms = int(audio_cursor_ms + actual_ms)
                if entry["text"] and not entry.get("empty"):
                    for ev in _split_subtitle_events(
                        entry["text"],
                        int(audio_cursor_ms),
                        sub_end_ms,
                    ):
                        subtitle_events.append(ev)

                # Curseur audio avance de la duree reelle de la voix
                audio_cursor_ms += actual_ms
                freeze_ms_used = freeze_ms if overflow_ms >= 50 else 0.0
                total_video_ms = clip_dur_ms + freeze_ms_used
                pad_ms = max(0.0, total_video_ms - actual_ms)
                audio_cursor_ms += pad_ms

            else:
                # Silence — audio muet, on avance le curseur
                audio_cursor_ms += clip_dur_ms

            # Mini gap entre deux voix consecutives
            if (entry["type"] == "speech"
                    and entry["tts_path"]
                    and ti + 1 < nb_total
                    and timeline[ti + 1]["type"] == "speech"
                    and timeline[ti + 1].get("tts_path")):
                audio_cursor_ms += GAP_BETWEEN_TTS_MS

        if not video_clips:
            raise RuntimeError(
                "Aucun clip vidéo généré. "
                "La vidéo source est peut-être corrompue ou trop courte."
            )

        # ── 4. Assemblage vidéo ───────────────────────────────────────────
        ws_progress(job_id, 4, 7, "Assemblage vidéo")
        assembled = str(work_dir / "assembled.mp4")
        ws_status(job_id, f"Assemblage de {len(video_clips)} clips...")

        if not concat_video_clips(video_clips, assembled, parts_dir):
            raise RuntimeError(
                "L'assemblage des clips vidéo a échoué. "
                "Vérifiez que ffmpeg est installé correctement."
            )
        ws_status(job_id, "Assemblage vidéo OK.", "ok")

        # ── 5. Mixage audio composite ─────────────────────────────────────
        ws_progress(job_id, 5, 7, "Mixage audio")

        total_samples = int((audio_cursor_ms / 1000.0 + 2.0) * SAMPLE_RATE)
        buf           = np.zeros(total_samples, dtype=np.int16)

        for seg_a in audio_segments:
            try:
                r_a = subprocess.run([
                    "ffmpeg", "-y", "-i", seg_a["path"],
                    "-ar", str(SAMPLE_RATE), "-ac", "1",
                    "-f", "s16le", "pipe:1",
                ], capture_output=True)
                if r_a.returncode == 0 and r_a.stdout:
                    arr = np.frombuffer(r_a.stdout, dtype=np.int16).copy()
                    ss  = int(seg_a["start_ms"] * SAMPLE_RATE / 1000)
                    es  = ss + len(arr)
                    if es > len(buf):
                        buf = np.concatenate([
                            buf,
                            np.zeros(es - len(buf) + SAMPLE_RATE, dtype=np.int16),
                        ])
                    buf[ss:es] = np.clip(
                        buf[ss:es].astype(np.int32) + arr.astype(np.int32),
                        np.iinfo(np.int16).min,
                        np.iinfo(np.int16).max,
                    ).astype(np.int16)
            except Exception as ex:
                ws_status(job_id, f"  Audio seg ignoré au mixage : {ex}", "warn")

        composite_wav = str(work_dir / "composite.wav")
        with wave.open(composite_wav, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(SAMPLE_RATE)
            wf.writeframes(buf.tobytes())
        ws_status(job_id, "Mixage audio OK.", "ok")

        # ── 6. Sous-titres ASS ────────────────────────────────────────────
        ass_path = None
        if subtitles_enabled and subtitle_events:
            ws_progress(job_id, 6, 7, "Génération sous-titres")
            ass_path = str(work_dir / "subtitles.ass")
            if not generate_ass(subtitle_events, video_w, video_h, style, ass_path):
                ass_path = None
                ws_status(job_id, "Sous-titres non générés — export sans.", "warn")
            else:
                ws_status(job_id,
                    f"Sous-titres OK : {len(subtitle_events)} événements.", "ok")

        # ── 7. Encodage final ─────────────────────────────────────────────
        ws_progress(job_id, 7, 7, "Encodage final")

        def _encode(ass=None):
            vf = None
            if ass and os.path.exists(ass):
                # Échappement du chemin pour le filtre ASS (Windows & Linux)
                ass_esc = ass.replace("\\", "/").replace(":", "\\:")
                vf = f"ass='{ass_esc}'"
            cmd = ["ffmpeg", "-y", "-i", assembled, "-i", composite_wav]
            if vf:
                cmd += ["-filter:v", vf]
            cmd += [
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", VIDEO_PRESET, "-crf", "17",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-shortest",
                final_path,
            ]
            return subprocess.run(cmd, capture_output=True, text=True)

        r = _encode(ass_path)
        if r.returncode != 0:
            ws_status(job_id, "Encodage avec sous-titres échoué → essai sans.", "warn")
            r = _encode(None)

        # Nettoyage
        for tmp in [composite_wav, assembled]:
            try:
                os.remove(tmp)
            except Exception:
                pass
        try:
            shutil.rmtree(str(parts_dir))
        except Exception:
            pass

        if not os.path.exists(final_path) or os.path.getsize(final_path) < 10_000:
            raise RuntimeError(
                "Le fichier final est invalide ou absent. "
                f"Erreur ffmpeg : {r.stderr[-400:] if r else 'inconnue'}"
            )

        size_mb      = os.path.getsize(final_path) / (1024 * 1024)
        total_dur_s  = audio_cursor_ms / 1000.0
        download_url = f"{settings.MEDIA_URL.rstrip('/')}/exports/{job.pk}/final.mp4"

        # Résumé des warnings
        if warnings:
            w_err  = [w for w in warnings if w["level"] == "err"]
            w_warn = [w for w in warnings if w["level"] == "warn"]
            if w_err:
                ws_status(job_id,
                    f"{len(w_err)} segment(s) avec texte trop long : "
                    f"{[w['index'] for w in w_err]}. "
                    "Raccourcissez ces textes pour une meilleure qualité.", "err")
            if w_warn:
                ws_status(job_id,
                    f"{len(w_warn)} segment(s) légèrement accélérés : "
                    f"{[w['index'] for w in w_warn]}.", "warn")

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "export_done",
                download_url=download_url,
                file_size_mb=round(size_mb, 1),
                warnings=warnings)
        ws_status(job_id,
            f"Export v12 terminé : {size_mb:.1f} Mo · {total_dur_s:.1f}s · "
            f"{len(subtitle_events)} sous-titres · {len(video_clips)} clips", "ok")
        send_job_notification(job, "export_done", download_url=download_url)
        return {"status": "success", "download_url": download_url, "warnings": warnings}

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur export {job_id}: {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        send_job_notification(job, "error")
        return {"status": "error", "message": msg}