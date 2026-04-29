"""
apps/studio/tasks/task_subtitles.py — Génération sous-titres via ElevenLabs Speech-to-Text.

FLUX :
    1. Envoyer final.mp4 à ElevenLabs /v1/speech-to-text
    2. Récupérer les mots avec timestamps
    3. Générer fichier ASS
    4. Brûler ASS sur final.mp4 → final_subtitled.mp4
"""

import logging
import os
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def ws_send(job_id, msg_type, **kwargs):
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


def task_generate_subtitles(job_id: str):
    """
    Génère les sous-titres en utilisant le fichier SRT créé lors de l'export.
    Synchronisation parfaite garantie car les timecodes viennent de l'export.
    """
    from apps.studio.models import Job

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    output_dir  = job.output_dir
    final_path  = output_dir / "final.mp4"
    srt_path    = output_dir / "subtitles.srt"
    subbed_path = output_dir / "final_subtitled.mp4"

    if not final_path.exists():
        ws_send(job_id, "status", message="Aucune vidéo finale — exportez d'abord.", level="error")
        return {"status": "error", "message": "final.mp4 introuvable"}

    if not srt_path.exists():
        ws_send(job_id, "status", message="Fichier SRT introuvable — réexportez d'abord.", level="error")
        return {"status": "error", "message": "subtitles.srt introuvable"}

    ws_send(job_id, "status", message="Brûlage des sous-titres...", level="info")
    logger.info(f"Sous-titres : utilisation de subtitles.srt existant — job={job_id}")

    # ── Brûler les sous-titres sur la vidéo ──────────────────────────────
    cmd = [
        "ffmpeg", "-y",
        "-i", str(final_path),
        "-vf", "subtitles=subtitles.srt:force_style='FontName=Arial,FontSize=14,PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H66000000,Outline=0,Shadow=0,Bold=0,MarginV=30,Alignment=2,BorderStyle=4,MarginL=60,MarginR=60'",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-c:a", "copy",
        str(subbed_path),
    ]

    logger.info(f"ffmpeg sous-titres : {' '.join(cmd)}")
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=600,
                            cwd=str(output_dir))

    if result.returncode != 0:
        msg = f"ffmpeg erreur : {result.stderr[-500:]}"
        logger.error(msg)
        ws_send(job_id, "status", message="Erreur lors du brûlage des sous-titres.", level="error")
        error_path = output_dir / "subtitles_error.txt"
        error_path.write_text(msg)
        return {"status": "error", "message": msg}

    # Nettoyer fichier filter script Windows
    try:
        filter_script_path = output_dir / "filter.txt"
        if filter_script_path.exists():
            filter_script_path.unlink()
    except Exception:
        pass

    # ── Sauvegarder l'URL en base ─────────────────────────────────────────
    from django.conf import settings as django_settings
    rel = subbed_path.relative_to(django_settings.OUTPUTS_ROOT)
    subtitled_url = f"/outputs/{str(rel).replace(chr(92), '/')}"

    job.subtitled_url = subtitled_url
    job.save(update_fields=["subtitled_url"])

    ws_send(job_id, "subtitles_done",
            subtitled_url=subtitled_url,
            message="Sous-titres générés avec succès !")

    logger.info(f"Sous-titres OK : {subbed_path} — job={job_id}")
    return {"status": "ok", "subtitled_url": subtitled_url}


# ─────────────────────────────────────────────────────────────────────────────
#  HELPERS
# ─────────────────────────────────────────────────────────────────────────────

def _group_words_to_subtitles(words, max_chars=50, max_duration_s=4.0):
    """Groupe les mots en lignes de sous-titres."""
    subtitles = []
    current_words = []
    current_start = None
    current_text  = ""

    for w in words:
        word_text  = w.get("text", "").strip()
        word_start = float(w.get("start", 0))
        word_end   = float(w.get("end", word_start + 0.3))

        if not word_text:
            continue

        if current_start is None:
            current_start = word_start

        test_text = (current_text + " " + word_text).strip()
        duration  = word_end - current_start

        if (len(test_text) > max_chars or duration > max_duration_s) and current_words:
            subtitles.append({
                "start": current_start,
                "end":   current_words[-1].get("end", current_start + 1),
                "text":  current_text.strip(),
            })
            current_words = [w]
            current_start = word_start
            current_text  = word_text
        else:
            current_words.append(w)
            current_text = test_text

    if current_words:
        subtitles.append({
            "start": current_start,
            "end":   current_words[-1].get("end", current_start + 1),
            "text":  current_text.strip(),
        })

    return subtitles


def _generate_srt(subtitles):
    """Génère un fichier SRT à partir des sous-titres."""
    lines = []
    for i, sub in enumerate(subtitles, 1):
        start = _seconds_to_srt_tc(sub["start"])
        end   = _seconds_to_srt_tc(sub["end"])
        # Ajouter des espaces pour simuler le padding
        text  = f"  {sub['text']}  "
        lines.append(f"{i}\n{start} --> {end}\n{text}\n")
    return "\n".join(lines)


def _seconds_to_srt_tc(seconds):
    """Convertit des secondes en timecode SRT (HH:MM:SS,mmm)."""
    h   = int(seconds // 3600)
    m   = int((seconds % 3600) // 60)
    s   = int(seconds % 60)
    ms  = int(round((seconds % 1) * 1000))
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _generate_ass(subtitles):
    """Génère un fichier ASS à partir des sous-titres."""
    header = """\
[Script Info]
ScriptType: v4.00+
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,52,&H00FFFFFF,&H000000FF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,3,1,2,80,80,60,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    lines = []
    for sub in subtitles:
        start = _seconds_to_ass_tc(sub["start"])
        end   = _seconds_to_ass_tc(sub["end"])
        text  = sub["text"].replace("\n", "\\N")
        lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}")

    return header + "\n".join(lines)


def _seconds_to_ass_tc(seconds):
    """Convertit des secondes en timecode ASS (H:MM:SS.cc)."""
    h  = int(seconds // 3600)
    m  = int((seconds % 3600) // 60)
    s  = int(seconds % 60)
    cs = int(round((seconds % 1) * 100))
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _get_video_duration(video_path):
    """Retourne la durée en secondes d'une vidéo via ffprobe."""
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=30,
        )
        return float(result.stdout.strip())
    except Exception:
        return 60.0


def _fake_words_from_text(text, total_duration):
    """Fallback : distribue les mots uniformément si pas de timestamps."""
    words = text.strip().split()
    if not words:
        return []
    step = total_duration / len(words)
    return [
        {"text": w, "start": i * step, "end": (i + 1) * step}
        for i, w in enumerate(words)
    ]