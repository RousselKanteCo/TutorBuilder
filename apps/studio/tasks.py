"""
apps/studio/tasks.py — Tâches Celery pour TutoBuilder Vision.
"""

import os
import logging
from pathlib import Path
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


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
        result = extraire_audio_wav(video_path, output_wav=wav_path)
        if result is None:
            raise RuntimeError("ffmpeg introuvable ou échec extraction.")

        ws_status(job_id, "✅ Audio extrait.")

        ws_status(job_id, "📊 Génération de la waveform...")
        ws_progress(job_id, 2, 4, "Waveform")
        waveform = _extraire_waveform(wav_path)
        job.waveform_data = waveform
        job.save(update_fields=["waveform_data"])
        ws_send(job_id, "waveform", data=waveform)
        ws_status(job_id, f"✅ Waveform générée ({len(waveform)} points).")

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


@shared_task(bind=True, name="studio.synthesize")
def task_synthesize(self, job_id: str, segments_data: list,
                    tts_engine: str = "coqui", voix: str = "default", langue: str = "fr"):
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
            provider_kwargs["api_key"] = settings.ELEVENLABS_API_KEY

        provider = TTSProviderFactory.create(tts_engine, **provider_kwargs)

        if provider is None or not provider.est_disponible():
            ws_status(job_id, f"⚠️ Provider '{tts_engine}' indisponible — tentative Windows TTS...")
            provider = TTSProviderFactory.create("pyttsx3")
            if provider and provider.est_disponible():
                tts_engine = "pyttsx3"
                ws_status(job_id, "✅ Fallback Windows TTS (pyttsx3) activé.")
            else:
                raise RuntimeError("Aucun provider TTS disponible.")

        ws_status(job_id, f"🎙️ Synthèse vocale via {provider.nom} — {len(segments_data)} segments...")

        output_dir = str(job.output_dir / "tts")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        chemins = []
        total = len(segments_data)

        for i, seg in enumerate(segments_data):
            texte = seg.get("text", "").strip()
            if not texte:
                chemins.append(None)
                continue

            ws_send(job_id, "tts_progress", current=i + 1, total=total, message=f"Segment {i+1}/{total}...")

            try:
                chemin = provider.generer(
                    texte=texte, voix=voix,
                    output_dir=output_dir,
                    filename=f"seg_{i:04d}.wav",
                    langue=langue,
                )
                if chemin and os.path.exists(chemin):
                    chemins.append(chemin)
                    Segment.objects.filter(job=job, index=seg.get("index", i)).update(audio_file=chemin)
                else:
                    chemins.append(None)
            except Exception as e:
                logger.warning(f"Segment {i} TTS échoué : {e}")
                chemins.append(None)

        nb_ok = sum(1 for c in chemins if c is not None)
        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done", files=chemins, nb_ok=nb_ok, nb_total=total)
        ws_status(job_id, f"✅ Synthèse terminée — {nb_ok}/{total} segments générés.")

        return {"status": "success", "job_id": job_id, "engine": tts_engine, "nb_generated": nb_ok, "nb_total": total}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur synthèse job {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ Erreur : {error_msg}")
        return {"status": "error", "message": error_msg}


@shared_task(bind=True, name="studio.export")
def task_export(self, job_id: str):
    """
    Montage final en 2 temps :
    1. Python/numpy : construire une piste audio composite.
    2. ffmpeg : fusionner avec la vidéo source.
    """
    import subprocess
    import wave
    import numpy as np
    from apps.studio.models import Job, Segment

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    ws_status(job_id, "🎬 Démarrage du montage vidéo...")

    try:
        video_path     = str(job.video_file.path)
        output_dir     = job.output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        composite_path = str(output_dir / "composite.wav")
        final_path     = str(output_dir / "final.mp4")

        # ── Récupérer segments avec audio ──
        segments = list(Segment.objects.filter(job=job).exclude(audio_file="").order_by("index"))
        if not segments:
            raise RuntimeError("Aucun segment audio trouvé. Lancez d'abord la synthèse vocale.")

        ws_status(job_id, f"📦 {len(segments)} segments en base.")

        # ── Vérifier quels fichiers existent réellement ──
        segments_ok = []
        for seg in segments:
            path = seg.audio_file
            if path and os.path.exists(path):
                segments_ok.append(seg)
                logger.info(f"  ✓ Segment {seg.index} : {path} ({os.path.getsize(path)} bytes)")
            else:
                logger.warning(f"  ✗ Segment {seg.index} : fichier introuvable → {path}")

        if not segments_ok:
            raise RuntimeError(
                "Aucun fichier WAV trouvé sur le disque.\n"
                "Les chemins en base ne correspondent pas aux fichiers.\n"
                f"Exemple : {segments[0].audio_file}"
            )

        ws_status(job_id, f"✅ {len(segments_ok)}/{len(segments)} fichiers audio vérifiés.")

        # ── Lire les paramètres du premier fichier valide ──
        with wave.open(segments_ok[0].audio_file, "rb") as wf:
            sample_rate  = wf.getframerate()
            n_channels   = wf.getnchannels()
            sample_width = wf.getsampwidth()

        ws_status(job_id, f"🎵 Paramètres audio : {sample_rate}Hz · {n_channels}ch · {sample_width*8}bit")

        # ── Buffer silencieux ──
        last_end_ms  = max(s.end_ms for s in segments_ok)
        total_ms     = last_end_ms + 2000  # 2s de marge
        total_frames = int(total_ms * sample_rate / 1000)
        dtype        = np.int16 if sample_width == 2 else np.int32
        buffer       = np.zeros(total_frames * n_channels, dtype=dtype)

        ws_status(job_id, f"🔇 Buffer : {total_ms/1000:.1f}s · {total_frames * n_channels} samples")

        # ── Poser chaque segment au bon timecode ──
        mixed_count = 0
        for seg in segments_ok:
            try:
                with wave.open(seg.audio_file, "rb") as wf:
                    # Vérifier compatibilité
                    if wf.getframerate() != sample_rate:
                        logger.warning(f"Segment {seg.index} : sample rate différent ({wf.getframerate()} vs {sample_rate}), ignoré")
                        continue
                    raw       = wf.readframes(wf.getnframes())
                    seg_array = np.frombuffer(raw, dtype=dtype).copy()

                if len(seg_array) == 0:
                    logger.warning(f"Segment {seg.index} : fichier WAV vide, ignoré")
                    continue

                start_sample = int(seg.start_ms * sample_rate / 1000) * n_channels
                end_sample   = start_sample + len(seg_array)

                if start_sample >= len(buffer):
                    logger.warning(f"Segment {seg.index} : hors buffer (start={start_sample} >= {len(buffer)})")
                    continue

                if end_sample > len(buffer):
                    seg_array  = seg_array[:len(buffer) - start_sample]
                    end_sample = len(buffer)

                buffer[start_sample:end_sample] = np.clip(
                    buffer[start_sample:end_sample].astype(np.int32) + seg_array.astype(np.int32),
                    np.iinfo(dtype).min, np.iinfo(dtype).max,
                ).astype(dtype)

                mixed_count += 1
                logger.info(f"  Segment {seg.index} posé à {seg.start_ms}ms → sample {start_sample}")

            except Exception as e:
                logger.warning(f"Segment {seg.index} ignoré : {e}")

        ws_status(job_id, f"✅ {mixed_count} segments mixés dans le buffer.")

        # ── Vérifier que le buffer n'est pas vide ──
        max_val = int(np.max(np.abs(buffer)))
        ws_status(job_id, f"📊 Valeur max buffer : {max_val} (0 = silence total)")
        if max_val == 0:
            raise RuntimeError(
                "Le buffer audio est complètement silencieux.\n"
                "Les segments WAV existent mais leur contenu n'a pas pu être mixé."
            )

        # ── Écrire le WAV composite ──
        with wave.open(composite_path, "wb") as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(buffer.tobytes())

        composite_size = os.path.getsize(composite_path)
        ws_status(job_id, f"💾 composite.wav créé : {composite_size // 1024} Ko")

        # ── ffmpeg : fusionner vidéo + audio ──
        ws_status(job_id, "⚙️ Encodage ffmpeg en cours...")

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", composite_path,
            "-map", "0:v:0",       # Vidéo du fichier source
            "-map", "1:a:0",       # Audio du composite
            "-c:v", "copy",        # Pas de réencodage vidéo
            "-c:a", "aac",
            "-b:a", "192k",
            "-async", "1",         # Synchronisation audio/vidéo
            final_path,
        ]

        logger.info(f"ffmpeg cmd : {' '.join(cmd)}")
        result = subprocess.run(cmd, capture_output=True, text=True)

        # Logger stderr complet pour diagnostic
        if result.stderr:
            for line in result.stderr.split('\n')[-20:]:
                if line.strip():
                    logger.info(f"ffmpeg: {line}")

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg erreur (code {result.returncode}) : {result.stderr[-400:]}")

        if not os.path.exists(final_path):
            raise RuntimeError("Le fichier final n'a pas été créé.")

        # Nettoyer composite temporaire
        try:
            os.remove(composite_path)
        except Exception:
            pass

        file_size_mb = os.path.getsize(final_path) / (1024 * 1024)
        job.set_status(Job.Status.DONE)

        download_url = f"/outputs/{job.pk}/final.mp4"
        ws_send(job_id, "export_done", download_url=download_url, file_size_mb=round(file_size_mb, 1))
        ws_status(job_id, f"✅ Montage terminé — {file_size_mb:.1f} Mo")

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



        ws_status(job_id, f"📦 {len(segments)} segments audio trouvés.")

        first_valid = next((s.audio_file for s in segments if s.audio_file and os.path.exists(s.audio_file)), None)
        if not first_valid:
            raise RuntimeError("Fichiers WAV introuvables sur le disque.")

        with wave.open(first_valid, "rb") as wf:
            sample_rate  = wf.getframerate()
            n_channels   = wf.getnchannels()
            sample_width = wf.getsampwidth()

        ws_status(job_id, f"🎵 Audio : {sample_rate}Hz, {n_channels}ch, {sample_width*8}bit")

        last_seg     = max(segments, key=lambda s: s.end_ms)
        total_ms     = last_seg.end_ms + 1000
        total_frames = int(total_ms * sample_rate / 1000)
        dtype        = np.int16 if sample_width == 2 else np.int32
        buffer       = np.zeros(total_frames * n_channels, dtype=dtype)

        ws_status(job_id, f"🔇 Buffer silencieux créé ({total_ms/1000:.1f}s)")

        for i, seg in enumerate(segments):
            if not seg.audio_file or not os.path.exists(seg.audio_file):
                continue
            try:
                with wave.open(seg.audio_file, "rb") as wf:
                    raw       = wf.readframes(wf.getnframes())
                    seg_array = np.frombuffer(raw, dtype=dtype).copy()

                start_sample = int(seg.start_ms * sample_rate / 1000) * n_channels
                end_sample   = start_sample + len(seg_array)

                if start_sample >= len(buffer):
                    continue
                if end_sample > len(buffer):
                    seg_array  = seg_array[:len(buffer) - start_sample]
                    end_sample = len(buffer)

                buffer[start_sample:end_sample] = np.clip(
                    buffer[start_sample:end_sample].astype(np.int32) + seg_array.astype(np.int32),
                    np.iinfo(dtype).min, np.iinfo(dtype).max,
                ).astype(dtype)

            except Exception as e:
                logger.warning(f"Segment {i} ignoré : {e}")

        ws_status(job_id, "✅ Piste audio composite construite.")

        with wave.open(composite_path, "wb") as wf:
            wf.setnchannels(n_channels)
            wf.setsampwidth(sample_width)
            wf.setframerate(sample_rate)
            wf.writeframes(buffer.tobytes())

        ws_status(job_id, "⚙️ Fusion vidéo + audio via ffmpeg...")
        ws_progress(job_id, 1, 1, "Encodage final…")

        cmd = [
            "ffmpeg", "-y",
            "-i", video_path,
            "-i", composite_path,
            "-map", "0:v",
            "-map", "1:a",
            "-c:v", "copy",
            "-c:a", "aac",
            "-b:a", "192k",
            "-shortest",
            final_path,
        ]

        result = subprocess.run(cmd, capture_output=True, text=True)

        if result.returncode != 0:
            raise RuntimeError(f"ffmpeg erreur : {result.stderr[-500:]}")

        if not os.path.exists(final_path):
            raise RuntimeError("Le fichier final n'a pas été créé.")

        try:
            os.remove(composite_path)
        except Exception:
            pass

        file_size_mb = os.path.getsize(final_path) / (1024 * 1024)
        job.set_status(Job.Status.DONE)

        download_url = f"/outputs/{job.pk}/final.mp4"
        ws_send(job_id, "export_done", download_url=download_url, file_size_mb=round(file_size_mb, 1))
        ws_status(job_id, f"✅ Montage terminé — {file_size_mb:.1f} Mo")

        return {"status": "success", "job_id": job_id, "final_path": final_path,
                "download_url": download_url, "file_size_mb": round(file_size_mb, 1)}

    except Exception as e:
        error_msg = str(e)
        logger.error(f"Erreur export job {job_id}: {error_msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=error_msg)
        ws_status(job_id, f"❌ Erreur montage : {error_msg}")
        return {"status": "error", "message": error_msg}


def _extraire_waveform(wav_path: str, nb_points: int = 500) -> list:
    import wave
    import numpy as np
    try:
        with wave.open(wav_path, "rb") as wf:
            n_frames = wf.getnframes()
            if n_frames == 0:
                return [0.0] * nb_points
            raw     = wf.readframes(n_frames)
            samples = np.frombuffer(raw, dtype=np.int16).astype(np.float32)

        block_size = max(1, len(samples) // nb_points)
        result = []
        for i in range(nb_points):
            start = i * block_size
            end   = min(start + block_size, len(samples))
            if start >= len(samples):
                result.append(0.0)
            else:
                result.append(round(float(np.max(np.abs(samples[start:end])) / 32767.0), 4))
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
                paths.append(f"/outputs/{job.pk}/thumbs/{filename}")
            if len(paths) >= nb_max:
                break

        cap.release()
        return paths

    except ImportError:
        logger.warning("OpenCV non installé, vignettes ignorées.")
        return []
    except Exception as e:
        logger.warning(f"Vignettes extraction failed: {e}")
        return []