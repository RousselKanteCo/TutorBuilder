"""
apps/studio/tasks/task_export.py — Assemblage vidéo final.

PRINCIPE ABSOLU :
    - La voix est SACRÉE — jamais modifiée, jamais accélérée
    - La vidéo s'adapte toujours à la voix
    - Si actual_tts_ms > durée clip planifiée → on ralentit la vidéo davantage
    - Chaque segment respecte exactement speed_factor défini par l'user
    - Les silences gardent leur speed_factor
    - Les segments supprimés sont ignorés

FLUX :
    1. Valider tous les segments
    2. Pour chaque segment : extraire clip + appliquer speed_factor réel
    3. Pour chaque segment avec audio : poser la voix TTS
    4. Concaténer tous les clips
    5. Générer les sous-titres ASS
    6. Optionnellement brûler les sous-titres
    7. Export final
"""

import logging
import os
import subprocess
import tempfile
import shutil
from pathlib import Path

logger = logging.getLogger(__name__)

# Limites ffmpeg
SPEED_MIN = 0.25
SPEED_MAX = 4.0


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


def run_ffmpeg(args: list, label: str = "") -> bool:
    """Exécute une commande ffmpeg et retourne True si succès."""
    cmd = ["ffmpeg", "-y"] + args
    logger.debug(f"ffmpeg {label}: {' '.join(cmd)}")
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        logger.error(f"ffmpeg {label} FAILED:\n{r.stderr[-500:]}")
        return False
    return True


def _get_clip_duration_ms(clip_path: str) -> float:
    """Mesure la durée réelle d'un clip avec ffprobe."""
    try:
        r = subprocess.run([
            "ffprobe", "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            clip_path,
        ], capture_output=True, text=True)
        return float(r.stdout.strip()) * 1000.0
    except Exception:
        return 0.0


def get_video_fps(video_path: str) -> float:
    """Récupère le FPS de la vidéo."""
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ], capture_output=True, text=True)
    try:
        num, den = r.stdout.strip().split("/")
        return float(num) / float(den)
    except Exception:
        return 30.0


def get_video_has_audio(video_path: str) -> bool:
    """Vérifie si la vidéo a une piste audio."""
    r = subprocess.run([
        "ffprobe", "-v", "error",
        "-select_streams", "a:0",
        "-show_entries", "stream=codec_type",
        "-of", "default=noprint_wrappers=1:nokey=1",
        video_path,
    ], capture_output=True, text=True)
    return "audio" in r.stdout


def task_export(job_id: str, burn_subtitles: bool = False, subtitle_style: dict = None):
    """
    Assemble la vidéo finale avec voix TTS et sous-titres.
    """
    from apps.studio.models import Job, Segment

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.SYNTHESIZING)
    ws_send(job_id, "status", message="Export démarré…", level="info")

    # Dossier de travail
    work_dir  = job.output_dir / "export_work"
    output_dir = job.output_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    video_path = str(job.video_file.path)
    fps        = get_video_fps(video_path)

    try:
        # ── 1. Charger et valider les segments ────────────────────────────
        segments = list(
            Segment.objects.filter(job=job)
            .order_by("index")
        )

        if not segments:
            raise RuntimeError("Aucun segment en base.")

        # Filtrer les supprimés
        segments_actifs = [s for s in segments if not _is_deleted(s)]

        if not segments_actifs:
            raise RuntimeError("Tous les segments ont été supprimés.")

        ws_send(job_id, "status",
                message=f"{len(segments_actifs)} segments à assembler…", level="info")

        # ── 2. Traiter chaque segment ─────────────────────────────────────
        clips        = []  # liste des fichiers clip finaux
        subtitle_events = []  # pour le fichier ASS

        cursor_ms = 0.0  # position dans la vidéo finale

        for i, seg in enumerate(segments_actifs):
            ws_send(job_id, "export_progress", current=i + 1, total=len(segments_actifs))

            clip_path = str(work_dir / f"clip_{i:04d}.mp4")
            dur_ms    = seg.end_ms - seg.start_ms

            # ── Calculer le speed_factor réel ─────────────────────────────
            speed = float(seg.speed_factor or 1.0)
            speed = max(SPEED_MIN, min(SPEED_MAX, speed))

            has_audio   = bool(seg.text and seg.text.strip() and seg.audio_file and os.path.exists(str(seg.audio_file)))
            actual_tts  = float(seg.actual_tts_ms or 0)

            if has_audio and actual_tts > 0:
                # Durée planifiée du clip
                duree_clip_planifiee = dur_ms / speed

                if actual_tts > duree_clip_planifiee:
                    # La voix déborde → ralentir la vidéo davantage
                    speed_reel = dur_ms / actual_tts
                    speed_reel = max(SPEED_MIN, speed_reel)
                    logger.info(
                        f"Seg {seg.index} : débordement détecté "
                        f"(tts={actual_tts:.0f}ms > clip={duree_clip_planifiee:.0f}ms) "
                        f"→ speed ajusté {speed:.3f} → {speed_reel:.3f}"
                    )
                    speed = speed_reel

            # Durée réelle du clip après speed_factor
            duree_clip_ms = dur_ms / speed

            # ── Extraire et ajuster le clip vidéo ─────────────────────────
            ok = _extraire_clip(
                video_path = video_path,
                start_ms   = seg.start_ms,
                dur_ms     = dur_ms,
                speed      = speed,
                has_audio  = has_audio,
                audio_path = str(seg.audio_file) if has_audio else None,
                output     = clip_path,
                fps        = fps,
            )

            if not ok:
                logger.warning(f"Seg {seg.index} : extraction échouée, clip silence généré")
                clip_path = str(work_dir / f"clip_{i:04d}_fallback.mp4")
                _generer_clip_silence(duree_clip_ms, clip_path, fps)

            clips.append(clip_path)

            # ── Mesurer la durée RÉELLE du clip généré ────────────────────
            duree_clip_reelle_ms = _get_clip_duration_ms(clip_path)
            if duree_clip_reelle_ms <= 0:
                duree_clip_reelle_ms = duree_clip_ms  # fallback théorique

            # ── Ajouter événement sous-titre ──────────────────────────────
            if seg.text and seg.text.strip():
                subtitle_events.append({
                    "start_ms": cursor_ms,
                    "end_ms":   cursor_ms + duree_clip_reelle_ms,
                    "text":     seg.text.strip(),
                })

            cursor_ms += duree_clip_reelle_ms

        # ── 3. Concaténer tous les clips ──────────────────────────────────
        ws_send(job_id, "status", message="Concaténation des clips…", level="info")

        concat_list = work_dir / "concat.txt"
        with open(concat_list, "w", encoding="utf-8") as f:
            for clip in clips:
                # Échapper les chemins Windows
                safe = clip.replace("\\", "/")
                f.write(f"file '{safe}'\n")

        assembled_path = str(output_dir / "assembled.mp4")
        ok = run_ffmpeg([
            "-f", "concat", "-safe", "0",
            "-i", str(concat_list),
            "-c", "copy",
            assembled_path,
        ], "concat")

        if not ok:
            # Fallback avec re-encodage
            ok = run_ffmpeg([
                "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "aac", "-b:a", "192k",
                assembled_path,
            ], "concat_reencode")

        if not ok:
            raise RuntimeError("La concaténation des clips a échoué.")

        # ── 4. Générer les sous-titres SRT + VTT ─────────────────────────
        ws_send(job_id, "status", message="Génération des sous-titres…", level="info")
        srt_path = str(output_dir / "subtitles.srt")
        vtt_path = str(output_dir / "subtitles.vtt")

        # Découper les événements longs en sous-événements
        subtitle_events_expanded = _expand_events(subtitle_events)

        _generer_srt(subtitle_events_expanded, srt_path)
        _generer_vtt(subtitle_events_expanded, vtt_path)

        # ── 5. Brûler les sous-titres si demandé ──────────────────────────
        final_path = str(output_dir / "final.mp4")

        if burn_subtitles:
            ws_send(job_id, "status", message="Application des sous-titres…", level="info")

            font_size = (subtitle_style or {}).get("font_size", 28)
            position  = (subtitle_style or {}).get("position", 2)
            y_pos     = "h-th-40" if position == 2 else "40"

            # Construire les filtres drawtext sur les événements découpés
            filters = []
            for ev in subtitle_events_expanded:
                # Échapper les caractères spéciaux pour drawtext
                text = ev["text"]
                text = text.replace("\\", "\\\\")
                text = text.replace("'",  "\u2019")  # apostrophe typographique
                text = text.replace(":",  "\\:")
                text = text.replace("%",  "\\%")
                start_s = ev["start_ms"] / 1000.0
                end_s   = ev["end_ms"]   / 1000.0

                filters.append(
                    f"drawtext=text='{text}'"
                    f":fontsize={font_size}"
                    f":fontcolor=white"
                    f":bordercolor=black:borderw=2"
                    f":x=(w-tw)/2:y={y_pos}"
                    f":enable='between(t\\,{start_s:.3f}\\,{end_s:.3f})'"
                )

            vf_filter = ",".join(filters) if filters else "null"

            # Écrire le filtre dans un fichier script pour éviter les problèmes de ligne de commande
            filter_script = str(output_dir / "filter_script.txt")
            with open(filter_script, "w", encoding="utf-8") as f:
                f.write(vf_filter)

            ok = run_ffmpeg([
                "-i", assembled_path,
                "-filter_script:v", filter_script,
                "-c:v", "libx264", "-preset", "fast", "-crf", "23",
                "-c:a", "copy",
                final_path,
            ], "burn_subs")

            # Nettoyer
            try: os.remove(filter_script)
            except Exception: pass

            if not ok:
                logger.warning("Sous-titres échoués — export sans sous-titres")
                shutil.copy(assembled_path, final_path)
        else:
            shutil.copy(assembled_path, final_path)

        # ── 6. Construire l'URL de téléchargement ─────────────────────────
        from django.conf import settings
        try:
            rel = Path(final_path).relative_to(settings.OUTPUTS_ROOT)
            download_url = f"/outputs/{str(rel).replace(chr(92), '/')}"
        except ValueError:
            download_url = ""

        # ── 7. Nettoyer les fichiers temporaires ──────────────────────────
        try:
            shutil.rmtree(str(work_dir), ignore_errors=True)
        except Exception:
            pass

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "export_done", download_url=download_url)
        ws_send(job_id, "status", message="Export terminé !", level="ok")

        logger.info(f"Export OK : job={job_id} → {final_path}")
        return {
            "status":       "success",
            "final_path":   final_path,
            "download_url": download_url,
            "vtt_path":     vtt_path,
            "duration_ms":  cursor_ms,
        }

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur export {job_id} : {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        # Nettoyer quand même
        try:
            shutil.rmtree(str(work_dir), ignore_errors=True)
        except Exception:
            pass
        return {"status": "error", "message": msg}


# ═══════════════════════════════════════════════════════════
#  HELPERS
# ═══════════════════════════════════════════════════════════

def _is_deleted(seg) -> bool:
    """Un segment est supprimé si son texte est vide ET son audio aussi."""
    return not seg.text and not seg.audio_file


def _extraire_clip(video_path, start_ms, dur_ms, speed, has_audio,
                   audio_path, output, fps) -> bool:
    """
    Extrait un clip vidéo, applique speed_factor, et colle l'audio TTS.
    """
    start_s = start_ms / 1000.0
    dur_s   = dur_ms   / 1000.0

    # Filtre vidéo speed
    speed   = max(SPEED_MIN, min(SPEED_MAX, speed))
    pts     = round(1.0 / speed, 6)

    video_filter = f"setpts={pts}*PTS"

    if has_audio and audio_path:
        # Avec audio TTS
        ok = run_ffmpeg([
            "-ss", f"{start_s:.3f}",
            "-t",  f"{dur_s:.3f}",
            "-i",  video_path,
            "-i",  audio_path,
            "-filter_complex",
            f"[0:v]{video_filter}[v]",
            "-map", "[v]",
            "-map", "1:a",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output,
        ], f"clip_audio_{start_ms}")

    else:
        # Sans audio (silence) — générer audio silence de même durée
        duree_clip_s = dur_s / speed
        ok = run_ffmpeg([
            "-ss", f"{start_s:.3f}",
            "-t",  f"{dur_s:.3f}",
            "-i",  video_path,
            "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
            "-filter_complex",
            f"[0:v]{video_filter}[v]",
            "-map", "[v]",
            "-map", "1:a",
            "-t",  f"{duree_clip_s:.3f}",
            "-c:v", "libx264", "-preset", "fast", "-crf", "23",
            "-c:a", "aac", "-b:a", "192k",
            "-movflags", "+faststart",
            output,
        ], f"clip_silence_{start_ms}")

    return ok


def _generer_clip_silence(duree_ms, output, fps) -> bool:
    """Génère un clip noir silencieux de secours."""
    duree_s = duree_ms / 1000.0
    return run_ffmpeg([
        "-f", "lavfi",
        "-i", f"color=c=black:s=1920x1080:r={fps:.2f}",
        "-f", "lavfi",
        "-i", "anullsrc=r=44100:cl=stereo",
        "-t", f"{duree_s:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "aac",
        output,
    ], "clip_fallback")


def _burn_subtitles_sync(video_path, ass_path, output_path, font_size=28, position=2):
    """
    Intègre les sous-titres dans la vidéo via drawtext.
    Utilise un fichier script ffmpeg pour éviter les problèmes de ligne de commande.
    La police Arial sur Windows supporte les caractères français.
    """
    import sys

    # Lire les événements depuis le SRT
    srt_path = ass_path.replace("subtitles.ass", "subtitles.srt")
    events   = _parse_srt(srt_path)

    if not events:
        logger.error("Aucun événement SRT trouvé")
        return False

    y_pos    = "h-th-50" if position == 2 else "50"
    font     = "Arial" if sys.platform == "win32" else "DejaVu Sans"
    filters  = []

    for ev in events:
        # Remplacer les caractères problématiques
        text = ev["text"]
        text = text.replace("\\", "/")
        text = text.replace("'",  "\u2019")   # apostrophe typographique
        text = text.replace("\n", " ")
        # Échapper pour le script ffmpeg
        text = text.replace(":", "\\:")
        text = text.replace("%", "\\%")
        text = text.replace("[", "\\[").replace("]", "\\]")

        start_s = ev["start_ms"] / 1000.0
        end_s   = ev["end_ms"]   / 1000.0

        # Box autour du texte = fond semi-transparent
        filters.append(
            f"drawtext="
            f"text='{text}'"
            f":font='{font}'"
            f":fontsize={font_size}"
            f":fontcolor=white"
            f":box=1:boxcolor=black@0.65:boxborderw=8"
            f":x=(w-tw)/2"
            f":y={y_pos}"
            f":enable='between(t\\,{start_s:.3f}\\,{end_s:.3f})'"
        )

    # Écrire dans un fichier script
    script_path = output_path + ".filter.txt"
    with open(script_path, "w", encoding="utf-8") as f:
        f.write(",".join(filters))

    ok = run_ffmpeg([
        "-i", video_path,
        "-filter_script:v", script_path,
        "-c:v", "libx264", "-preset", "fast", "-crf", "23",
        "-c:a", "copy",
        output_path,
    ], "burn_sync")

    try:
        os.remove(script_path)
    except Exception:
        pass

    return ok


def _parse_srt(srt_path: str) -> list:
    """Parse un fichier SRT et retourne les événements."""
    events = []
    if not os.path.exists(srt_path):
        return events
    try:
        with open(srt_path, "r", encoding="utf-8") as f:
            content = f.read()
        blocks = content.strip().split("\n\n")
        for block in blocks:
            lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
            if len(lines) < 3:
                continue
            # Ligne 2 = timecodes
            tc_line = next((l for l in lines if "-->" in l), None)
            if not tc_line:
                continue
            start_str, end_str = tc_line.split("-->")
            def srt_to_ms(s):
                s = s.strip().replace(",", ".")
                h, m, rest = s.split(":")
                sec, ms = rest.split(".")
                return int(h)*3600000 + int(m)*60000 + int(sec)*1000 + int(ms[:3])
            text_lines = [l for l in lines if "-->" not in l and not l.isdigit()]
            events.append({
                "start_ms": srt_to_ms(start_str),
                "end_ms":   srt_to_ms(end_str),
                "text":     " ".join(text_lines),
            })
    except Exception as e:
        logger.error(f"Erreur parsing SRT : {e}")
    return events


MAX_CHARS_PER_SCREEN = 80   # max caractères affichés à la fois
MAX_CHARS_PER_LINE   = 42   # max caractères par ligne


def _split_event_in_time(ev, max_chars=MAX_CHARS_PER_SCREEN):
    """
    Découpe un événement long en plusieurs sous-événements
    répartis proportionnellement dans le temps.
    Ex: texte de 200 chars sur 10s → 3 sous-titres de ~67 chars × 3.3s chacun
    """
    text     = ev["text"].strip()
    start_ms = ev["start_ms"]
    end_ms   = ev["end_ms"]
    dur_ms   = end_ms - start_ms

    if len(text) <= max_chars:
        return [ev]

    # Découper en chunks de max_chars caractères (par mots)
    words   = text.split()
    chunks  = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = (current + " " + word).strip()
        else:
            if current:
                chunks.append(current)
            current = word
    if current:
        chunks.append(current)

    if not chunks:
        return [ev]

    # Répartir le temps proportionnellement à la longueur des chunks
    total_chars = sum(len(c) for c in chunks)
    result      = []
    cursor      = start_ms

    for i, chunk in enumerate(chunks):
        proportion = len(chunk) / total_chars
        chunk_dur  = int(dur_ms * proportion)
        chunk_end  = cursor + chunk_dur if i < len(chunks) - 1 else end_ms
        result.append({
            "start_ms": cursor,
            "end_ms":   chunk_end,
            "text":     chunk,
        })
        cursor = chunk_end

    return result


def _expand_events(events):
    """Développe tous les événements longs en sous-événements."""
    result = []
    for ev in events:
        result.extend(_split_event_in_time(ev))
    return result


def _wrap_text(text, max_chars=MAX_CHARS_PER_LINE):
    """Découpe le texte en max 2 lignes de max_chars caractères."""
    words   = text.split()
    lines   = []
    current = ""
    for word in words:
        if len(current) + len(word) + 1 <= max_chars:
            current = (current + " " + word).strip()
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return "\n".join(lines[:2])


def _generer_vtt(events, vtt_path, offset_ms=-200):
    """Génère un fichier WebVTT pour le player HTML5.
    offset_ms : décalage à appliquer (négatif = avancer les sous-titres)
    """
    def ms_to_vtt(ms):
        ms  = max(0, int(ms))
        h   = ms // 3600000; ms -= h * 3600000
        m   = ms // 60000;   ms -= m * 60000
        s   = ms // 1000;    ms -= s * 1000
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"

    lines = ["WEBVTT", ""]
    for i, ev in enumerate(events, 1):
        start = max(0, ev["start_ms"] + offset_ms)
        end   = max(start + 100, ev["end_ms"] + offset_ms)
        text  = _wrap_text(ev["text"])
        lines.append(str(i))
        lines.append(f"{ms_to_vtt(start)} --> {ms_to_vtt(end)} align:center line:85%")
        lines.append(text)
        lines.append("")

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"VTT généré : {len(events)} lignes → {vtt_path}")


def _generer_srt(events, srt_path):
    """Génère un fichier SRT standard."""
    def ms_to_srt(ms):
        ms  = max(0, int(ms))
        h   = ms // 3600000; ms -= h * 3600000
        m   = ms // 60000;   ms -= m * 60000
        s   = ms // 1000;    ms -= s * 1000
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    lines = []
    for i, ev in enumerate(events, 1):
        lines.append(str(i))
        lines.append(f"{ms_to_srt(ev['start_ms'])} --> {ms_to_srt(ev['end_ms'])}")
        lines.append(ev["text"])
        lines.append("")

    with open(srt_path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))

    logger.info(f"SRT généré : {len(events)} lignes → {srt_path}")


def _generer_ass(events, ass_path, style):
    """
    Génère un fichier de sous-titres ASS.
    """
    font_size  = style.get("font_size",  28)
    font_name  = style.get("font_name",  "Arial")
    primary    = style.get("primary",    "&H00FFFFFF")  # blanc
    outline    = style.get("outline",    "&H00000000")  # noir
    position   = style.get("position",  2)  # 2=bas centre, 8=haut centre

    header = f"""[Script Info]
ScriptType: v4.00+
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font_name},{font_size},{primary},&H000000FF,{outline},&H80000000,0,0,0,0,100,100,0,0,1,2,1,{position},10,10,30,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    def ms_to_ass(ms):
        ms   = max(0, int(ms))
        h    = ms // 3600000
        ms  -= h * 3600000
        m    = ms // 60000
        ms  -= m * 60000
        s    = ms // 1000
        cs   = (ms % 1000) // 10
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"

    lines = []
    for ev in events:
        text = ev["text"].replace("\n", "\\N")
        lines.append(
            f"Dialogue: 0,{ms_to_ass(ev['start_ms'])},{ms_to_ass(ev['end_ms'])},"
            f"Default,,0,0,0,,{text}"
        )

    with open(ass_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")

    logger.info(f"ASS généré : {len(lines)} lignes → {ass_path}")