"""
apps/studio/tasks/utils/audio.py — Utilitaires audio.
"""

import os
import wave
import subprocess
import logging

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050


def extraire_audio_wav(video_path: str, output_wav: str) -> bool:
    """Extrait l'audio d'une vidéo en WAV mono 22050Hz."""
    r = subprocess.run([
        "ffmpeg", "-y", "-i", video_path,
        "-ar", str(SAMPLE_RATE), "-ac", "1",
        "-sample_fmt", "s16", output_wav,
    ], capture_output=True, text=True)

    if r.returncode != 0:
        logger.error(f"Extraction audio échouée : {r.stderr[-300:]}")
        return False

    return os.path.exists(output_wav)


def extraire_waveform(wav_path: str, nb_points: int = 500) -> list:
    """Génère les données waveform pour l'affichage."""
    try:
        import numpy as np
        with wave.open(wav_path, "rb") as wf:
            n = wf.getnframes()
            if n == 0:
                return [0.0] * nb_points
            samples = (
                __import__("numpy")
                .frombuffer(wf.readframes(n), dtype=__import__("numpy").int16)
                .astype(float)
            )
        block = max(1, len(samples) // nb_points)
        return [
            round(float(abs(samples[i*block:min((i+1)*block, len(samples))]).max()) / 32767, 4)
            for i in range(nb_points)
        ]
    except Exception as e:
        logger.warning(f"Waveform échouée : {e}")
        return [0.0] * nb_points


def get_video_duration(video_path: str) -> float:
    """Retourne la durée d'une vidéo en secondes."""
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
    """Retourne les dimensions (width, height) d'une vidéo."""
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


def extraire_miniature(video_path: str, time_s: float, output_path: str) -> bool:
    """Extrait une miniature à un timestamp donné."""
    r = subprocess.run([
        "ffmpeg", "-y",
        "-ss", f"{time_s:.3f}",
        "-i", video_path,
        "-frames:v", "1",
        "-vf", "scale=160:90:flags=lanczos",
        "-q:v", "4",
        output_path,
    ], capture_output=True, text=True)
    return r.returncode == 0 and os.path.exists(output_path)

MIN_TTS_BYTES = 2000

def validate_tts_file(path: str) -> tuple:
    if not path:
        return False, "chemin vide"
    if not os.path.exists(path):
        return False, f"fichier introuvable : {path}"
    size = os.path.getsize(path)
    if size < MIN_TTS_BYTES:
        return False, f"fichier trop petit ({size} o)"
    try:
        import wave
        with wave.open(path, "rb") as wf:
            dur = (wf.getnframes() / wf.getframerate()) * 1000
        if dur < 100:
            return False, f"durée invalide ({dur:.0f}ms)"
        return True, ""
    except Exception as e:
        return False, f"erreur lecture WAV : {e}"


def get_wav_duration_ms(wav_path: str) -> float:
    try:
        import wave
        with wave.open(wav_path, "rb") as wf:
            return max(400.0, (wf.getnframes() / wf.getframerate()) * 1000.0)
    except Exception:
        return 3000.0