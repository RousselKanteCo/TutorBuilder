"""
apps/studio/tasks.py — TutoBuilder Vision v11
══════════════════════════════════════════════════════════════════════════════
PRINCIPE — "Vidéo maître du temps, silences compressés"
────────────────────────────────────────────────────────
  La vidéo originale n'est JAMAIS etirée ni compressée (pas de setpts).
  Le TTS s'adapte par atempo audio uniquement (max x1.30, inaudible).
  Les silences longs entre segments sont compressés à MAX_SILENCE_MS.

TRAITEMENT DES SILENCES INTER-SEGMENTS
───────────────────────────────────────
  silence_naturel = next_seg_start - seg_end    (ms entre deux prises de parole)
  Si silence > MAX_SILENCE_MS (1200ms par défaut) :
    → on découpe la vidéo jusqu'à seg_end + MAX_SILENCE_MS seulement
    → le surplus de blanc est supprimé
  Sinon :
    → on inclut le silence naturel complet (respiration pédagogique)
  Résultat : fluidité sans stretcher le TTS, sans silences morts de 3-4s.

BUDGET AJUSTÉ PAR SEGMENT
──────────────────────────
  budget_ms = (seg_end - seg_start) + min(silence_naturel, MAX_SILENCE_MS)
  clip_dur  = budget_ms / 1000.0  (durée réelle du clip vidéo extrait)
  tts_ms    ≤ budget_ms → atempo si nécessaire, silence de padding sinon

VALIDATION TTS
──────────────
  Après chaque segment : vérification existence fichier + taille + durée.
  Résultat global stocké dans synthesis_plan.json avec champ "tts_valid".
  task_export vérifie "tts_valid" avant de démarrer — refuse proprement si KO.

ERREURS
───────
  Chaque étape log précisément la cause. Les erreurs TTS sont comptabilisées
  et signalées segment par segment. Un export avec 0 TTS valide est refusé
  avec un message clair indiquant quoi faire.
"""

import os
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

OUTPUT_FPS          = 25
ATEMPO_MAX          = 1.30       # accélération TTS max (inaudible à l'oreille)
PAUSE_FREEZE_MS     = 300        # freeze inter-segment (ms)
MAX_SILENCE_MS      = 800.0      # silence naturel max conservé entre segments
SUBTITLE_MAX_CHARS  = 44
VIDEO_ENCODE_PRESET = "fast"


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
    """Envoie un message de log vers le frontend. level = info|warn|ok|err"""
    ws_send(job_id, "status", message=msg, level=level)
    log_fn = logger.warning if level == "warn" else (
             logger.error   if level == "err"  else logger.info)
    log_fn(f"[{job_id}] {msg}")


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
            return max(400.0, (os.path.getsize(wav_path) - 44) / (22050 * 2) * 1000.0)
        except Exception:
            return 3000.0


def validate_tts_file(path: str) -> tuple[bool, str]:
    """
    Valide qu'un fichier TTS est exploitable.
    Retourne (valide, raison_si_invalide).
    """
    if not path:
        return False, "chemin vide"
    if not os.path.exists(path):
        return False, f"fichier introuvable : {path}"
    size = os.path.getsize(path)
    if size < 2000:
        return False, f"fichier trop petit ({size} octets) — probablement corrompu"
    try:
        dur = get_wav_duration_ms(path)
        if dur < 100:
            return False, f"durée audio invalide ({dur:.0f}ms)"
        return True, ""
    except Exception as e:
        return False, f"erreur lecture WAV : {e}"


def adjust_tts_to_budget(tts_path: str, budget_ms: float,
                          output_path: str, sample_rate: int = 22050) -> float:
    """
    Ajuste la durée TTS pour tenir dans budget_ms via atempo audio.
    Ne modifie JAMAIS la vidéo.
    Retourne la durée réelle du fichier de sortie (ms).
    """
    tts_ms = get_wav_duration_ms(tts_path)
    ratio  = tts_ms / max(budget_ms, 100.0)

    if ratio <= 1.05:
        # TTS dans le budget → copie directe
        r = subprocess.run(
            ["ffmpeg", "-y", "-i", tts_path,
             "-ar", str(sample_rate), "-ac", "1", "-sample_fmt", "s16",
             output_path],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and os.path.exists(output_path):
            return get_wav_duration_ms(output_path)
        shutil.copy2(tts_path, output_path)
        return tts_ms

    # TTS trop long → atempo (limité à ATEMPO_MAX)
    tempo = min(ratio, ATEMPO_MAX)
    if ratio > ATEMPO_MAX:
        logger.warning(
            f"TTS ({tts_ms:.0f}ms) > budget ({budget_ms:.0f}ms) ratio {ratio:.2f} > max {ATEMPO_MAX}. "
            f"Accélération plafonnée à x{ATEMPO_MAX}. "
            "Conseil utilisateur : raccourcir le texte de ce segment."
        )

    r = subprocess.run(
        ["ffmpeg", "-y", "-i", tts_path,
         "-filter:a", f"atempo={tempo:.4f}",
         "-ar", str(sample_rate), "-ac", "1", "-sample_fmt", "s16",
         output_path],
        capture_output=True, text=True,
    )
    if r.returncode == 0 and os.path.exists(output_path):
        out_ms = get_wav_duration_ms(output_path)
        logger.info(f"atempo x{tempo:.2f} : {tts_ms:.0f}ms → {out_ms:.0f}ms "
                    f"(budget {budget_ms:.0f}ms)")
        return out_ms

    logger.error(f"atempo échoué : {r.stderr[-300:]}")
    shutil.copy2(tts_path, output_path)
    return tts_ms


def build_silence_wav(duration_ms: float, output_path: str,
                      sample_rate: int = 22050) -> bool:
    n_samples = int(duration_ms * sample_rate / 1000)
    try:
        with wave.open(output_path, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(sample_rate)
            wf.writeframes(b'\x00' * n_samples * 2)
        return True
    except Exception as e:
        logger.error(f"Erreur création silence : {e}")
        return False


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


def get_video_dimensions(video_path: str) -> tuple[int, int]:
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


def detect_freeze_zones(video_path: str, start_s: float, end_s: float,
                        freeze_threshold: float = 0.001,
                        min_freeze_dur: float = 0.5) -> list:
    """
    Détecte les zones figées (image statique) dans un segment vidéo.
    Retourne une liste de (freeze_start, freeze_end) en secondes absolues.
    """
    r = subprocess.run([
        "ffmpeg", "-ss", f"{start_s:.3f}", "-t", f"{end_s - start_s:.3f}",
        "-i", video_path,
        "-vf", f"freezedetect=n={freeze_threshold}:d={min_freeze_dur}",
        "-f", "null", "-",
    ], capture_output=True, text=True)

    zones = []
    freeze_start = None
    for line in r.stderr.splitlines():
        if "freeze_start" in line:
            try:
                t = float(line.split("freeze_start:")[-1].strip())
                freeze_start = start_s + t
            except Exception:
                pass
        elif "freeze_end" in line and freeze_start is not None:
            try:
                t = float(line.split("freeze_end:")[-1].strip())
                freeze_end = start_s + t
                if freeze_end > freeze_start:
                    zones.append((freeze_start, freeze_end))
                freeze_start = None
            except Exception:
                pass
    return zones


def extract_video_clip_adaptive(video_path: str, start_s: float, end_s: float,
                                 output_path: str, video_w: int, video_h: int,
                                 target_dur_s: float) -> bool:
    """
    Extrait un clip vidéo avec accélération ADAPTATIVE selon le contenu :
    - Zones figées (image statique) : accélérées jusqu'à x4.0
    - Zones actives (mouvement)     : accélération douce max x1.5
    
    L'accélération est calculée pour que la durée du clip = target_dur_s.
    Utilise setpts pour la vidéo (smooth, pas d'artefacts).
    Aucune modification audio (la piste audio est supprimée, sera remplacée par TTS).
    """
    src_dur = end_s - start_s
    if src_dur < 0.05 or target_dur_s < 0.05:
        return False

    ratio_global = src_dur / target_dur_s  # > 1 = on accélère

    if ratio_global <= 1.05:
        # Pas d'accélération nécessaire — découpe simple
        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{start_s:.4f}", "-t", f"{src_dur:.4f}",
            "-i", video_path,
            "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h}",
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", output_path,
        ], capture_output=True, text=True)
        return r.returncode == 0 and os.path.exists(output_path)

    # Détecter les zones figées dans ce segment
    freeze_zones = detect_freeze_zones(video_path, start_s, end_s)

    # Calculer les proportions freeze vs active
    step = 0.05
    t = start_s
    dur_freeze = 0.0
    dur_active = 0.0
    while t < end_s:
        t_next = min(end_s, t + step)
        dt = t_next - t
        in_freeze = any(fz[0] <= t < fz[1] for fz in freeze_zones)
        if in_freeze:
            dur_freeze += dt
        else:
            dur_active += dt
        t = t_next

    MAX_SPEED_FREEZE = 4.0
    MAX_SPEED_ACTIVE = 1.5

    # Calculer les vitesses pour atteindre target_dur_s
    # Équation : dur_freeze/speed_freeze + dur_active/speed_active = target_dur_s
    speed_active = 1.0
    if dur_freeze > 0:
        remaining_for_freeze = target_dur_s - dur_active / speed_active
        if remaining_for_freeze <= 0:
            speed_active = min(MAX_SPEED_ACTIVE, dur_active / max(0.1, target_dur_s))
            remaining_for_freeze = target_dur_s - dur_active / speed_active
        speed_freeze = min(MAX_SPEED_FREEZE,
                          dur_freeze / max(0.05, remaining_for_freeze))
    else:
        # Pas de zones figées → accélérer uniformément
        speed_freeze = 1.0
        speed_active = min(MAX_SPEED_ACTIVE, ratio_global)

    logger.info(
        f"Clip adaptatif [{start_s:.1f}s-{end_s:.1f}s] → {target_dur_s:.1f}s | "
        f"freeze {dur_freeze:.1f}s x{speed_freeze:.1f} | "
        f"active {dur_active:.1f}s x{speed_active:.1f} | "
        f"{len(freeze_zones)} zones"
    )

    if len(freeze_zones) == 0 or abs(speed_freeze - speed_active) < 0.05:
        # Accélération uniforme — setpts simple
        pts_factor = 1.0 / max(speed_active, 0.1)
        vf = (f"setpts={pts_factor:.4f}*PTS,"
              f"fps={OUTPUT_FPS},scale={video_w}:{video_h}")
        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{start_s:.4f}", "-t", f"{src_dur:.4f}",
            "-i", video_path,
            "-vf", vf,
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", output_path,
        ], capture_output=True, text=True)
        return r.returncode == 0 and os.path.exists(output_path)

    # Accélération différenciée par zone — construction via select+setpts
    # Stratégie : on décompose le segment en sous-clips puis on concatène
    import tempfile
    tmp_dir = Path(output_path).parent / f"_adapt_{Path(output_path).stem}"
    tmp_dir.mkdir(exist_ok=True)

    sub_clips = []
    zones_timeline = []  # (t_start, t_end, is_freeze)

    # Construire la timeline des zones
    t = start_s
    current_freeze = None
    for fz in sorted(freeze_zones, key=lambda x: x[0]):
        if fz[0] > t:
            zones_timeline.append((t, fz[0], False))
        zones_timeline.append((fz[0], fz[1], True))
        t = fz[1]
    if t < end_s:
        zones_timeline.append((t, end_s, False))

    # Extraire et accélérer chaque zone
    for zi, (z_start, z_end, is_freeze) in enumerate(zones_timeline):
        z_dur = z_end - z_start
        if z_dur < 0.05:
            continue
        speed = speed_freeze if is_freeze else speed_active
        pts   = 1.0 / max(speed, 0.1)
        sub_p = str(tmp_dir / f"sub_{zi:03d}.mp4")
        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{z_start:.4f}", "-t", f"{z_dur:.4f}",
            "-i", video_path,
            "-vf", (f"setpts={pts:.4f}*PTS,"
                   f"fps={OUTPUT_FPS},scale={video_w}:{video_h}"),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", sub_p,
        ], capture_output=True, text=True)
        if r.returncode == 0 and os.path.exists(sub_p):
            sub_clips.append(sub_p)

    if not sub_clips:
        # Fallback : accélération uniforme
        pts_factor = 1.0 / max(ratio_global, 0.1)
        r = subprocess.run([
            "ffmpeg", "-y",
            "-ss", f"{start_s:.4f}", "-t", f"{src_dur:.4f}",
            "-i", video_path,
            "-vf", (f"setpts={pts_factor:.4f}*PTS,"
                   f"fps={OUTPUT_FPS},scale={video_w}:{video_h}"),
            "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
            "-an", output_path,
        ], capture_output=True, text=True)
        return r.returncode == 0 and os.path.exists(output_path)

    # Concaténer les sous-clips
    concat_f = str(tmp_dir / "concat.txt")
    with open(concat_f, "w") as f:
        for p in sub_clips:
            f.write("file '" + p + "'\n")
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_f,
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-an", output_path,
    ], capture_output=True, text=True)

    try:
        shutil.rmtree(str(tmp_dir))
    except Exception:
        pass

    if r.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"extract_video_clip_adaptive échoué : {r.stderr[-200:]}")
        return False
    return True


def extract_video_clip(video_path: str, start_s: float, duration_s: float,
                       output_path: str, video_w: int, video_h: int) -> bool:
    """Découpe un clip vidéo à x1.0 — fallback simple sans adaptation."""
    if duration_s < 0.05:
        return False
    r = subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{start_s:.4f}", "-t", f"{duration_s:.4f}",
        "-i", video_path,
        "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "20",
        "-an", output_path,
    ], capture_output=True, text=True)
    if r.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"extract_video_clip échoué ({start_s:.2f}s) : {r.stderr[-200:]}")
        return False
    return True


def make_freeze_frame(source_video: str, output_path: str,
                      duration_ms: float, video_w: int, video_h: int) -> bool:
    frame_path = output_path + "_last.jpg"
    r1 = subprocess.run([
        "ffmpeg", "-y", "-sseof", "-0.1", "-i", source_video,
        "-vframes", "1", "-q:v", "2", frame_path,
    ], capture_output=True, text=True)
    if r1.returncode != 0 or not os.path.exists(frame_path):
        return False
    r2 = subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", frame_path,
        "-t", f"{duration_ms/1000.0:.4f}",
        "-vf", f"fps={OUTPUT_FPS},scale={video_w}:{video_h}",
        "-c:v", "libx264", "-preset", "ultrafast", "-crf", "22",
        "-an", output_path,
    ], capture_output=True, text=True)
    try: os.remove(frame_path)
    except Exception: pass
    return r2.returncode == 0 and os.path.exists(output_path)


def concat_clips(clip_paths: list, output_path: str, tmp_dir: Path) -> bool:
    concat_f = str(tmp_dir / "concat.txt")
    with open(concat_f, "w") as f:
        for p in clip_paths:
            f.write(f"file '{p}'\n")
    r = subprocess.run([
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_f,
        "-c:v", "libx264", "-preset", VIDEO_ENCODE_PRESET, "-crf", "20",
        "-an", output_path,
    ], capture_output=True, text=True)
    try: os.remove(concat_f)
    except Exception: pass
    if r.returncode != 0 or not os.path.exists(output_path):
        logger.error(f"concat échoué : {r.stderr[-200:]}")
        return False
    return True


# ══════════════════════════════════════════════════════════════════════════════
#  SOUS-TITRES ASS
# ══════════════════════════════════════════════════════════════════════════════

def _text_to_lines(text: str, max_chars: int = SUBTITLE_MAX_CHARS) -> list:
    words, lines, cur = text.split(), [], ""
    for w in words:
        t = (cur + " " + w).strip() if cur else w
        if len(t) <= max_chars:
            cur = t
        else:
            if cur: lines.append(cur)
            cur = w
    if cur: lines.append(cur)
    return lines


def _split_subtitle_events(text: str, start_ms: int, end_ms: int) -> list:
    MAX_LINES = 3
    all_lines = _text_to_lines(text)
    if not all_lines:
        return [{"start_ms": start_ms, "end_ms": end_ms,
                 "text": text, "sub_text": ""}]
    screens = [all_lines[i:i+MAX_LINES] for i in range(0, len(all_lines), MAX_LINES)]
    if len(screens) == 1:
        return [{"start_ms": start_ms, "end_ms": end_ms,
                 "text": text, "sub_text": "\n".join(screens[0])}]
    total_words = sum(len(" ".join(s).split()) for s in screens) or 1
    total_ms    = max(end_ms - start_ms, 100)
    events, cur = [], start_ms
    for i, screen in enumerate(screens):
        txt  = "\n".join(screen)
        nb   = len(txt.split())
        last = (i == len(screens) - 1)
        dur  = int(total_ms * nb / total_words)
        fin  = end_ms if last else cur + dur
        events.append({"start_ms": int(cur), "end_ms": int(fin),
                       "text": txt, "sub_text": txt})
        cur = fin
    return events


def _hex_to_ass(h: str) -> str:
    h = h.lstrip("#").upper()
    return (h[4:6] + h[2:4] + h[0:2]) if len(h) == 6 else "FFFFFF"


def _ms_to_ass(ms: int) -> str:
    ms = int(max(0, ms))
    h = ms // 3_600_000; ms %= 3_600_000
    m = ms // 60_000;    ms %= 60_000
    s = ms // 1_000;     ms %= 1_000
    return f"{h}:{m:02d}:{s:02d}.{ms//10:02d}"


def _generate_ass(events: list, video_w: int, video_h: int,
                  style: dict, output_path: str) -> bool:
    try:
        font_map    = {"calibri":"Calibri","arial":"Arial","segoe":"Segoe UI",
                       "georgia":"Georgia","impact":"Impact"}
        font_name   = font_map.get(style.get("font_family","calibri"), "Arial")
        font_size   = int(style.get("font_size", 48))
        prim_clr    = _hex_to_ass(style.get("text_color","FFFFFF"))
        out_clr     = _hex_to_ass(style.get("outline_color","000000"))
        bg_clr      = _hex_to_ass(style.get("bg_color","000000"))
        outline_w   = int(style.get("outline_width", 2))
        shadow_d    = 2 if style.get("shadow", True) else 0
        bg_on       = bool(style.get("bg_enabled", True))
        bg_opacity  = int(style.get("bg_opacity", 75))
        bg_alpha    = hex(max(0, 255 - int(bg_opacity * 2.55)))[2:].upper().zfill(2)
        position    = style.get("position", "bottom")
        margin_v    = int(style.get("margin", 60))
        alignment   = {"bottom":2,"top":8,"center":5}.get(position, 2)
        bstyle      = 3 if bg_on else 1
        back_color  = f"&H{bg_alpha}{bg_clr}&"
        ow_actual   = outline_w if not bg_on else 0

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
        clean_ev  = []
        for i, ev in enumerate(sorted_ev):
            s = ev["start_ms"]; e = ev["end_ms"]
            if i+1 < len(sorted_ev):
                e = min(e, sorted_ev[i+1]["start_ms"] - 50)
            if e > s:
                clean_ev.append({**ev, "start_ms": s, "end_ms": e})

        lines = []
        for ev in clean_ev:
            txt = (ev.get("sub_text") or ev.get("text","")).strip()
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
        logger.error(f"Erreur génération ASS : {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS PRIVÉS
# ══════════════════════════════════════════════════════════════════════════════

def _extraire_waveform(wav_path: str, nb_points: int = 500) -> list:
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


def _extraire_vignettes(video_path: str, job, nb_max: int = 10) -> list:
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
#  TÂCHE 2 : SYNTHÈSE VOCALE — avec validation rigoureuse
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
        # ── Vérification clé API ─────────────────────────────────────────
        if tts_engine == "elevenlabs":
            api_key = getattr(settings, "ELEVENLABS_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError(
                    "Clé API ElevenLabs manquante. "
                    "Ajoutez ELEVENLABS_API_KEY dans votre fichier .env et redémarrez le serveur."
                )
        elif tts_engine == "cartesia":
            api_key = getattr(settings, "CARTESIA_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError(
                    "Clé API Cartesia manquante. "
                    "Ajoutez CARTESIA_API_KEY dans votre fichier .env et redémarrez le serveur."
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

        # ── Toujours utiliser les timecodes en base (SACRÉS) ────────────
        db_segments = list(
            Segment.objects.filter(job=job).order_by("index")
            .values("index", "start_ms", "end_ms", "text")
        )
        if not db_segments:
            raise RuntimeError(
                "Aucun segment en base. "
                "Lancez la transcription et sauvegardez le script avant de générer la voix."
            )

        total   = len(db_segments)
        plan    = []
        echecs  = []
        longs   = []   # segments dont le TTS dépasse probablement le budget

        ws_status(job_id, f"Synthèse vocale ({tts_engine}) — {total} segments à traiter...")

        for i, seg in enumerate(db_segments):
            idx   = seg["index"]
            texte = (seg["text"] or "").strip()

            ws_progress(job_id, i + 1, total, f"Segment {i+1}/{total}")
            ws_send(job_id, "tts_progress", current=i+1, total=total)

            if not texte:
                ws_status(job_id, f"  Segment {idx} vide — ignoré", "warn")
                continue

            # Estimation préventive (≈14 chars/s parlé)
            dur_parole_ms = float(seg["end_ms"] - seg["start_ms"])
            est_tts_ms    = len(texte) / 14.0 * 1000
            if est_tts_ms > dur_parole_ms * ATEMPO_MAX * 1.5:
                longs.append(idx)

            filename = f"seg_{idx:04d}.wav"
            filepath = None
            last_err = ""

            # 3 tentatives avec délai croissant (instabilité réseau / API)
            # Délais : 0s → 3s → 7s
            RETRY_DELAYS = [0, 3, 7]

            for attempt in range(3):
                from tts_providers import TTSErrorCleAPI, TTSErrorReseau, TTSErrorAPI, TTSErrorConversion

                wait = RETRY_DELAYS[attempt]
                if wait > 0:
                    import time
                    ws_status(job_id,
                        f"  Segment {idx} — attente {wait}s avant tentative {attempt+1}/3...",
                        "warn")
                    time.sleep(wait)

                try:
                    chemin = provider.generer(
                        texte=texte, voix=voix, output_dir=output_dir,
                        filename=filename, langue=langue,
                    )
                    # Vérification fichier (double sécurité)
                    valide, raison = validate_tts_file(chemin)
                    if valide:
                        filepath = chemin
                        break
                    else:
                        last_err = raison
                        ws_status(job_id,
                            f"  Segment {idx} tentative {attempt+1}/3 : fichier invalide ({raison})",
                            "warn")

                except TTSErrorCleAPI as e:
                    # Clé invalide/absente → inutile de retenter, on arrête tout
                    last_err = str(e)
                    ws_send(job_id, "error", message=last_err)
                    ws_status(job_id, f"  Erreur clé API : {last_err}", "err")
                    echecs.append({"index": idx, "raison": last_err})
                    # Marquer tous les segments restants comme échoués sans appel API
                    for seg_rest in db_segments[i+1:]:
                        echecs.append({
                            "index":  seg_rest["index"],
                            "raison": "arrêté — erreur clé API",
                        })
                        plan.append({
                            "index": seg_rest["index"],
                            "start_ms": seg_rest["start_ms"],
                            "end_ms":   seg_rest["end_ms"],
                            "text":     (seg_rest["text"] or "").strip(),
                            "tts_path": None, "tts_ms": 0, "valid": False,
                        })
                    # Sortir de la boucle principale
                    db_segments = db_segments[:i+1]
                    break

                except TTSErrorReseau as e:
                    # Timeout / connexion → on retente (réseau instable)
                    last_err = str(e)
                    ws_status(job_id,
                        f"  Segment {idx} tentative {attempt+1}/3 : {last_err}",
                        "warn")
                    if attempt == 2:
                        ws_send(job_id, "tts_segment_warn",
                                index=idx, message=last_err)

                except TTSErrorAPI as e:
                    # Erreur serveur (500, 429) → on retente
                    last_err = str(e)
                    ws_status(job_id,
                        f"  Segment {idx} tentative {attempt+1}/3 : {last_err}",
                        "warn")
                    if attempt == 2:
                        ws_send(job_id, "tts_segment_warn",
                                index=idx, message=last_err)

                except TTSErrorConversion as e:
                    # Conversion ffmpeg échouée → retenter n'aidera pas
                    last_err = str(e)
                    ws_status(job_id, f"  Segment {idx} : {last_err}", "err")
                    ws_send(job_id, "tts_segment_warn", index=idx, message=last_err)
                    echecs.append({"index": idx, "raison": last_err})
                    break  # pas la peine de retenter

                except Exception as e:
                    last_err = str(e)
                    ws_status(job_id,
                        f"  Segment {idx} tentative {attempt+1}/3 erreur inattendue : {last_err}",
                        "warn")

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
                })
                ws_status(job_id, f"  Segment {idx} OK — {dur_ms:.0f}ms", "ok")
            else:
                # Ajouter dans echecs si pas déjà enregistré (ex: TTSErrorCleAPI)
                if not any(e["index"] == idx for e in echecs):
                    echecs.append({"index": idx, "raison": last_err or "échec après 3 tentatives"})
                plan.append({
                    "index":    idx,
                    "start_ms": seg["start_ms"],
                    "end_ms":   seg["end_ms"],
                    "text":     texte,
                    "tts_path": None,
                    "tts_ms":   0,
                    "valid":    False,
                })

        nb_valides = sum(1 for p in plan if p["valid"])
        nb_echecs  = len(echecs)

        # ── Stockage du plan avec statut de validité ─────────────────────
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

        # ── Résumé et avertissements ─────────────────────────────────────
        if nb_valides == 0:
            derniere = echecs[-1]["raison"] if echecs else "inconnue"
            raise RuntimeError(
                f"Aucun segment vocal généré ({nb_echecs} échec(s) après 3 tentatives chacun). "
                f"Dernière erreur : {derniere}\n"
                "Causes possibles : instabilité réseau, API ElevenLabs/Cartesia temporairement "
                "indisponible, ou clé API invalide. Relancez dans quelques instants."
            )

        if echecs:
            ws_status(job_id,
                f"Avertissement : {nb_echecs} segment(s) échoué(s) sur {total} après 3 tentatives — "
                f"segments {[e['index'] for e in echecs]}. "
                "Ces segments seront silencieux dans la vidéo finale. "
                "Relancez la synthèse pour retenter uniquement les segments manquants.", "warn")

        if longs:
            ws_status(job_id,
                f"Avertissement : {len(longs)} segment(s) avec texte probablement "
                f"trop long pour leur durée vidéo — segments {longs}. "
                "La voix sera légèrement accélérée. "
                "Pour une meilleure qualité, raccourcissez ces textes.", "warn")

        # ── Succès ───────────────────────────────────────────────────────
        tts_status = "ok" if nb_echecs == 0 else "warn"
        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done",
                nb_ok=nb_valides, nb_total=total, nb_echecs=nb_echecs,
                echecs=[e["index"] for e in echecs])
        ws_status(job_id,
            f"Synthèse terminée : {nb_valides}/{total} segments valides.", tts_status)
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
#  TÂCHE 3 : EXPORT v11 — silences compressés + validation pré-export
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
    ws_status(job_id, "Démarrage de l'export v11...")

    try:
        video_path  = str(job.video_file.path)
        work_dir    = job.output_dir
        work_dir.mkdir(parents=True, exist_ok=True)
        parts_dir   = work_dir / "parts"
        parts_dir.mkdir(parents=True, exist_ok=True)
        exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
        exports_dir.mkdir(parents=True, exist_ok=True)
        final_path  = str(exports_dir / "final.mp4")
        sample_rate = 22050

        video_w, video_h = get_video_dimensions(video_path)
        video_dur        = get_video_duration(video_path)
        ws_status(job_id, f"Source : {video_w}×{video_h} — {video_dur:.1f}s")

        # ── Chargement et validation du plan ─────────────────────────────
        ws_progress(job_id, 1, 6, "Chargement du plan de synthèse")
        plan_path = str(work_dir / "synthesis_plan.json")

        if not os.path.exists(plan_path):
            raise RuntimeError(
                "Plan de synthèse introuvable. "
                "La synthèse vocale n'a pas encore été effectuée pour ce job. "
                "Retournez à l'étape 3 et générez la voix off avant d'exporter."
            )

        with open(plan_path, "r", encoding="utf-8") as f:
            plan_data = json.load(f)

        # Refuser si tts_valid = False (aucun segment valide)
        if not plan_data.get("tts_valid", True):
            nb_val = plan_data.get("nb_valides", 0)
            nb_tot = plan_data.get("nb_total", 0)
            raise RuntimeError(
                f"La synthèse vocale précédente a échoué ({nb_val}/{nb_tot} segments valides). "
                "Relancez la synthèse vocale à l'étape 3 avant d'exporter."
            )

        langue = plan_data.get("langue", "fr")

        # Filtrer les items valides avec fichier existant
        plan_items = []
        for item in plan_data.get("plan", []):
            if not item.get("valid", False):
                ws_status(job_id,
                    f"  Segment {item.get('index','?')} ignoré (marqué invalide)", "warn")
                continue
            tts_path = item.get("tts_path", "")
            valide, raison = validate_tts_file(tts_path)
            if valide:
                plan_items.append(item)
            else:
                ws_status(job_id,
                    f"  Segment {item.get('index','?')} ignoré : {raison}", "warn")

        if not plan_items:
            raise RuntimeError(
                "Aucun fichier audio valide trouvé. "
                "Relancez la synthèse vocale — les fichiers précédents sont manquants ou corrompus."
            )

        ws_status(job_id, f"{len(plan_items)} segments audio valides.", "ok")

        # ── Calcul des budgets avec compression des silences ─────────────
        #
        # PRINCIPE :
        #   budget_ms = durée_parole + min(silence_naturel, MAX_SILENCE_MS)
        #   clip_dur  = budget_ms / 1000.0  ← le clip vidéo s'arrête là
        #
        # Ainsi les silences de 3-4s entre paragraphes sont réduits à 1.2s max.
        # Le TTS n'est JAMAIS stretché — seulement légèrement accéléré si trop long.
        #
        ws_progress(job_id, 2, 6, "Calcul des budgets-temps")

        for i, item in enumerate(plan_items):
            seg_start_ms  = float(item["start_ms"])
            seg_end_ms    = float(item["end_ms"])
            dur_parole_ms = seg_end_ms - seg_start_ms
            tts_ms        = float(item["tts_ms"])

            # ══ STRATÉGIE ADAPTATIVE ══════════════════════════════════
            #
            # PRINCIPE : on adapte la VITESSE de la vidéo source pour que
            # la durée du clip = tts_ms + silence_transition.
            #
            # Deux zones traitées différemment :
            #   - Zones FIGÉES  (image statique) → accélération jusqu'à x4.0
            #     (l'utilisateur remplit un formulaire, attend un chargement)
            #   - Zones ACTIVES (mouvement)       → accélération douce max x1.5
            #     (navigation, scroll, démonstration)
            #
            # La vidéo source EST TOUJOURS COMPLÈTE — on ne coupe rien.
            # On accélère les zones qui le permettent pour coller au TTS.
            # L'audio TTS n'est jamais modifié (atempo max x1.15 si besoin).
            #
            if i + 1 < len(plan_items):
                next_start_ms   = float(plan_items[i+1]["start_ms"])
                silence_naturel = max(0.0, next_start_ms - seg_end_ms)
                silence_garde   = min(silence_naturel, MAX_SILENCE_MS)
            else:
                silence_garde   = 400.0

            # La durée cible du clip = tts + silence de transition
            target_ms    = tts_ms + silence_garde
            target_ms    = max(target_ms, 300.0)
            clip_start_s = seg_start_ms / 1000.0
            # Le clip va de seg_start jusqu'à next_seg_start (toute la source)
            # mais sera accéléré pour tenir dans target_ms
            clip_src_end_s = min(
                (float(plan_items[i+1]["start_ms"]) / 1000.0) if i+1 < len(plan_items)
                else video_dur,
                video_dur
            )
            clip_src_dur_s = max(0.1, clip_src_end_s - clip_start_s)

            # Atempo audio : seulement si le TTS dépasse légèrement le target
            ratio  = tts_ms / target_ms
            tempo  = min(ratio, 1.15) if ratio > 1.02 else 1.0
            adj_ms = tts_ms / tempo if tempo > 1.0 else tts_ms
            pad_ms = max(0.0, target_ms - adj_ms)

            item["budget_ms"]      = target_ms
            item["clip_start_s"]   = clip_start_s
            item["clip_src_dur_s"] = clip_src_dur_s   # durée source réelle
            item["clip_dur_s"]     = target_ms / 1000.0  # durée cible après accél
            item["tempo"]          = tempo
            item["adj_tts_ms"]     = adj_ms
            item["pad_ms"]         = pad_ms

            ws_status(job_id,
                f"  Seg {i+1:02d} | src {clip_src_dur_s*1000:.0f}ms → "
                f"cible {target_ms:.0f}ms | TTS {tts_ms:.0f}ms | "
                f"silence {silence_garde:.0f}ms")

        # ── Découpe vidéo + ajustement TTS ───────────────────────────────
        ws_progress(job_id, 3, 6, "Découpe des clips")

        video_clips      = []
        audio_segments   = []
        subtitle_events  = []
        audio_cursor_ms  = 0.0

        for i, item in enumerate(plan_items):
            ws_progress(job_id, i+1, len(plan_items), f"Clip {i+1}/{len(plan_items)}")
            is_last    = (i == len(plan_items) - 1)
            clip_path  = str(parts_dir / f"clip_{i:04d}.mp4")
            pause_path = str(parts_dir / f"pause_{i:04d}.mp4")
            tts_adj    = str(parts_dir / f"tts_{i:04d}.wav")

            # 1. Clip vidéo avec accélération ADAPTATIVE
            # La vidéo source est toujours intégrale (seg_start → next_seg_start)
            # mais accélérée intelligemment selon les zones figées vs actives
            clip_ok = extract_video_clip_adaptive(
                video_path  = video_path,
                start_s     = item["clip_start_s"],
                end_s       = item["clip_start_s"] + item.get("clip_src_dur_s", item["clip_dur_s"]),
                output_path = clip_path,
                video_w     = video_w,
                video_h     = video_h,
                target_dur_s = item["clip_dur_s"],
            )
            if not clip_ok:
                ws_status(job_id, f"  Clip {i+1} ignoré (extraction échouée)", "warn")
                continue

            # 2. TTS ajusté par atempo uniquement
            actual_ms = adjust_tts_to_budget(
                tts_path    = item["tts_path"],
                budget_ms   = item["budget_ms"],
                output_path = tts_adj,
                sample_rate = sample_rate,
            )
            if not os.path.exists(tts_adj):
                actual_ms = item["tts_ms"]
                tts_adj   = item["tts_path"]

            # 3. Pause freeze (sauf dernier)
            if not is_last:
                pause_ok = make_freeze_frame(
                    source_video = clip_path,
                    output_path  = pause_path,
                    duration_ms  = PAUSE_FREEZE_MS,
                    video_w      = video_w,
                    video_h      = video_h,
                )
                video_clips.append(clip_path)
                if pause_ok:
                    video_clips.append(pause_path)
            else:
                video_clips.append(clip_path)

            # 4. Sous-titres calés sur le curseur audio
            sub_s = int(audio_cursor_ms)
            sub_e = int(audio_cursor_ms + actual_ms)
            for ev in _split_subtitle_events(item["text"], sub_s, sub_e):
                subtitle_events.append(ev)

            # 5. Enregistrement de la position audio
            audio_segments.append({
                "path":     tts_adj,
                "start_ms": audio_cursor_ms,
                "dur_ms":   actual_ms,
            })
            audio_cursor_ms += actual_ms + item["pad_ms"]
            if not is_last:
                audio_cursor_ms += PAUSE_FREEZE_MS

        if not video_clips:
            raise RuntimeError("Aucun clip vidéo généré. La vidéo source est peut-être corrompue.")

        # ── Assemblage vidéo ──────────────────────────────────────────────
        ws_progress(job_id, 4, 6, "Assemblage vidéo")
        assembled = str(work_dir / "assembled.mp4")
        ws_status(job_id, f"Assemblage de {len(video_clips)} clips...")

        if not concat_clips(video_clips, assembled, parts_dir):
            raise RuntimeError(
                "L'assemblage des clips vidéo a échoué. "
                "Vérifiez que ffmpeg est bien installé."
            )

        # ── Audio composite ───────────────────────────────────────────────
        ws_progress(job_id, 5, 6, "Mixage audio")
        total_samples = int((audio_cursor_ms / 1000 + 2) * sample_rate)
        buf           = np.zeros(total_samples, dtype=np.int16)

        for seg_a in audio_segments:
            try:
                r_a = subprocess.run([
                    "ffmpeg", "-y", "-i", seg_a["path"],
                    "-ar", str(sample_rate), "-ac", "1",
                    "-f", "s16le", "pipe:1",
                ], capture_output=True)
                if r_a.returncode == 0 and r_a.stdout:
                    arr = np.frombuffer(r_a.stdout, dtype=np.int16).copy()
                    ss  = int(seg_a["start_ms"] * sample_rate / 1000)
                    es  = ss + len(arr)
                    if es > len(buf):
                        buf = np.concatenate([
                            buf, np.zeros(es - len(buf) + sample_rate, dtype=np.int16)
                        ])
                    buf[ss:es] = np.clip(
                        buf[ss:es].astype(np.int32) + arr.astype(np.int32),
                        np.iinfo(np.int16).min, np.iinfo(np.int16).max
                    ).astype(np.int16)
            except Exception as ex:
                ws_status(job_id, f"  Audio mixage ignoré : {ex}", "warn")

        composite_wav = str(work_dir / "composite.wav")
        with wave.open(composite_wav, "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2)
            wf.setframerate(sample_rate)
            wf.writeframes(buf.tobytes())

        # ── Sous-titres ASS ───────────────────────────────────────────────
        ass_path = None
        if subtitles_enabled and subtitle_events:
            ass_path = str(work_dir / "subtitles.ass")
            if not _generate_ass(subtitle_events, video_w, video_h, style, ass_path):
                ass_path = None
                ws_status(job_id,
                    "Sous-titres non générés — export sans sous-titres.", "warn")

        # ── Encodage final ────────────────────────────────────────────────
        ws_progress(job_id, 6, 6, "Encodage final")

        def _encode(ass=None):
            vf = f"ass='{ass.replace(chr(92), '/').replace(':', chr(92)+':')}'" if ass else None
            cmd = ["ffmpeg", "-y", "-i", assembled, "-i", composite_wav]
            if vf:
                cmd += ["-filter:v", vf]
            cmd += [
                "-map", "0:v:0", "-map", "1:a:0",
                "-c:v", "libx264", "-preset", VIDEO_ENCODE_PRESET, "-crf", "17",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart", "-shortest", final_path,
            ]
            return subprocess.run(cmd, capture_output=True, text=True)

        r = _encode(ass_path if ass_path and os.path.exists(ass_path) else None)
        if r.returncode != 0:
            ws_status(job_id, "Encodage avec sous-titres échoué — nouvel essai sans.", "warn")
            r = _encode(None)

        # Nettoyage
        for tmp in [composite_wav, assembled]:
            try: os.remove(tmp)
            except Exception: pass
        try: shutil.rmtree(str(parts_dir))
        except Exception: pass

        if not os.path.exists(final_path) or os.path.getsize(final_path) < 10000:
            raise RuntimeError(
                "Le fichier final est invalide ou absent. "
                f"Erreur ffmpeg : {r.stderr[-300:] if r else 'inconnue'}"
            )

        size_mb      = os.path.getsize(final_path) / (1024 * 1024)
        total_dur_s  = audio_cursor_ms / 1000.0
        download_url = f"{settings.MEDIA_URL.rstrip('/')}/exports/{job.pk}/final.mp4"

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "export_done",
                download_url=download_url,
                file_size_mb=round(size_mb, 1))
        ws_status(job_id,
            f"Export terminé : {size_mb:.1f} Mo | {total_dur_s:.1f}s | "
            f"{len(subtitle_events)} sous-titres", "ok")
        send_job_notification(job, "export_done", download_url=download_url)
        return {"status": "success", "download_url": download_url}

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur export {job_id}: {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        send_job_notification(job, "error")
        return {"status": "error", "message": msg}