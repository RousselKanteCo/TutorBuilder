"""
apps/studio/tasks/task_synthesize.py — Génération voix TTS.

PRINCIPE :
    - On génère la voix pour chaque segment NON supprimé et NON vide
    - La voix est toujours à x1.0 — jamais modifiée
    - La vidéo s'adapte via speed_factor (déjà calculé à la transcription)
    - Les segments supprimés → silence
    - Les segments vides → silence
"""

import logging
import os
import time
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



def task_synthesize(job_id: str, tts_engine: str = "elevenlabs",
                    voice: str = "narrateur_pro", langue: str = "fr",
                    segment_ids: list = None):
    """
    Génère la voix TTS pour chaque segment du job.
    """
    from apps.studio.models import Job, Segment
    from tts_providers import TTSProviderFactory, TTSErrorCleAPI

    try:
        job = Job.objects.get(pk=job_id)
    except Job.DoesNotExist:
        return {"status": "error", "message": "Job introuvable"}

    job.set_status(Job.Status.SYNTHESIZING)
    ws_send(job_id, "status", message=f"Synthèse vocale ({tts_engine}) démarrée…", level="info")

    try:
        # ── Vérifier clé API ──────────────────────────────────────────────
        from django.conf import settings
        if tts_engine == "elevenlabs":
            api_key = getattr(settings, "ELEVENLABS_API_KEY", "").strip()
            if not api_key:
                raise TTSErrorCleAPI(
                    "Clé API ElevenLabs manquante. "
                    "Ajoutez ELEVENLABS_API_KEY dans votre .env."
                )
        elif tts_engine == "cartesia":
            api_key = getattr(settings, "CARTESIA_API_KEY", "").strip()
            if not api_key:
                raise TTSErrorCleAPI(
                    "Clé API Cartesia manquante. "
                    "Ajoutez CARTESIA_API_KEY dans votre .env."
                )
        else:
            api_key = ""

        provider = TTSProviderFactory.create(tts_engine, api_key=api_key)
        if not provider.est_disponible():
            raise RuntimeError(f"Moteur TTS '{tts_engine}' non disponible.")

        logger.info(f"Synthèse avec voix='{voice}' engine='{tts_engine}'")

        # ── Dossier TTS ───────────────────────────────────────────────────
        tts_dir = job.output_dir / "tts"
        tts_dir.mkdir(parents=True, exist_ok=True)

        # ── Charger les segments ──────────────────────────────────────────
        segments = list(Segment.objects.filter(job=job).order_by("index"))
        if not segments:
            raise RuntimeError("Aucun segment en base. Lancez la transcription d'abord.")

        # Segments à synthétiser = non supprimés + non vides
        all_segments = [
            s for s in segments
            if s.text and s.text.strip() and len(s.text.strip()) >= 3
        ]

        # Déterminer les segments à synthétiser :
        # - Si segment_ids fournis → exactement ceux-là (régénération manuelle)
        # - Sinon → union de : segments sans audio + segments modifiés
        if segment_ids:
            to_synthesize = [s for s in all_segments if str(s.pk) in [str(sid) for sid in segment_ids]]
            ws_send(job_id, "status",
                    message=f"Regénération de {len(to_synthesize)} segment(s) modifié(s)…",
                    level="info")
        else:
            segment_ids_set = set(str(sid) for sid in (segment_ids or []))
            to_synthesize   = []
            already_ok      = []

            for s in all_segments:
                audio_existe = s.audio_file and os.path.exists(str(s.audio_file))
                if not audio_existe:
                    to_synthesize.append(s)
                else:
                    already_ok.append(s.index)

            nb_sans_audio = len(to_synthesize)
            nb_deja_ok    = len(already_ok)

            if nb_deja_ok:
                logger.info(f"{nb_deja_ok} segments déjà générés — ignorés")

            ws_send(job_id, "status",
                    message=f"{len(to_synthesize)} segment(s) à générer ({nb_deja_ok} déjà OK)…",
                    level="info")

        total   = len(to_synthesize)
        nb_ok   = 0
        nb_fail = 0
        echecs  = []

        for i, seg in enumerate(to_synthesize):
            ws_send(job_id, "tts_progress", current=i + 1, total=total)

            filename   = f"seg_{seg.index:04d}.wav"
            filepath   = None
            last_err   = ""
            RETRY_DELAYS = [0, 3, 7]

            for attempt in range(3):
                wait = RETRY_DELAYS[attempt]
                if wait > 0:
                    ws_send(job_id, "status",
                            message=f"Seg {seg.index} — attente {wait}s…", level="warn")
                    time.sleep(wait)

                try:
                    chemin = provider.generer(
                        texte      = seg.text.strip(),
                        voix       = voice,
                        output_dir = str(tts_dir),
                        filename   = filename,
                        langue     = langue,
                    )

                    # Valider le fichier
                    from .utils.audio import validate_tts_file
                    valide, raison = validate_tts_file(chemin)
                    if valide:
                        filepath = chemin
                        break
                    else:
                        last_err = raison
                        logger.warning(f"Seg {seg.index} tentative {attempt+1} invalide : {raison}")

                except TTSErrorCleAPI as e:
                    # Erreur clé → arrêt immédiat
                    raise

                except Exception as e:
                    last_err = str(e)
                    logger.warning(f"Seg {seg.index} tentative {attempt+1} : {last_err}")

            if filepath:
                # Mesurer la durée réelle du fichier audio
                from .utils.audio import get_wav_duration_ms
                actual_ms = get_wav_duration_ms(filepath)

                # Sauvegarder en base
                seg.audio_file   = filepath
                seg.actual_tts_ms = actual_ms
                seg.save(update_fields=["audio_file", "actual_tts_ms"])

                nb_ok += 1
                ws_send(job_id, "status",
                        message=f"Seg {seg.index} OK — {actual_ms:.0f}ms", level="ok")
            else:
                nb_fail += 1
                echecs.append(seg.index)
                logger.error(f"Seg {seg.index} échoué : {last_err}")
                ws_send(job_id, "status",
                        message=f"Seg {seg.index} échoué : {last_err}", level="err")

        # ── Résultat ──────────────────────────────────────────────────────
        if nb_ok == 0:
            raise RuntimeError(
                f"Aucun segment généré ({nb_fail} échec(s)). "
                "Vérifiez votre clé API et votre connexion."
            )

        job.set_status(Job.Status.DONE)
        ws_send(job_id, "tts_done",
                nb_ok=nb_ok, nb_total=total, nb_echecs=nb_fail,
                echecs=echecs)
        ws_send(job_id, "status",
                message=f"Voix générée : {nb_ok}/{total} segments.",
                level="ok" if nb_fail == 0 else "warn")

        return {
            "status":    "success" if nb_fail == 0 else "partial",
            "nb_ok":     nb_ok,
            "nb_total":  total,
            "nb_echecs": nb_fail,
        }

    except Exception as e:
        msg = str(e)
        logger.error(f"Erreur synthèse {job_id} : {msg}", exc_info=True)
        job.set_status(Job.Status.ERROR, error=msg)
        ws_send(job_id, "error", message=msg)
        return {"status": "error", "message": msg}