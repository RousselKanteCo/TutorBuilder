"""
apps/studio/notifications.py — Notifications email TutorBuilder Vision

Envoi automatique d'emails à chaque étape importante :
  - Transcription terminée
  - Synthèse vocale terminée
  - Vidéo finale prête (avec lien de téléchargement)
  - Erreur lors d'un traitement
"""

import logging
from django.core.mail import send_mail
from django.conf import settings
from django.template.loader import render_to_string

logger = logging.getLogger(__name__)


def get_user_email(job) -> str | None:
    """Récupère l'email de l'utilisateur propriétaire du job."""
    try:
        return job.project.owner.email or None
    except Exception:
        return None


def send_job_notification(job, event: str, download_url: str = None):
    """
    Envoie un email de notification selon l'événement.

    Events :
      - 'transcribed'  → transcription terminée
      - 'tts_done'     → synthèse vocale terminée
      - 'export_done'  → vidéo finale prête
      - 'error'        → erreur
    """
    email = get_user_email(job)
    if not email:
        logger.warning(f"Notification ignorée — pas d'email pour job {job.pk}")
        return

    job_name  = job.video_filename or str(job.pk)[:8]
    proj_name = job.project.name
    base_url  = getattr(settings, "BASE_URL", "http://localhost:8000")
    job_url   = f"{base_url}/cockpit/{job.pk}/"

    # ── Contenu selon l'événement ─────────────────────────────────────────
    if event == "transcribed":
        subject = f"✅ Transcription terminée — {proj_name}"
        body = f"""Bonjour,

La transcription de votre vidéo "{job_name}" est terminée.

📝 {job.segments.count()} segments ont été transcrits.

Vous pouvez maintenant corriger le script et générer la voix off :
{job_url}

—
TutorBuilder Vision"""

    elif event == "tts_done":
        subject = f"🎙️ Voix off générée — {proj_name}"
        body = f"""Bonjour,

La synthèse vocale de votre vidéo "{job_name}" est terminée.

Vous pouvez maintenant assembler et exporter la vidéo finale :
{job_url}

—
TutorBuilder Vision"""

    elif event == "export_done":
        subject = f"🎬 Votre vidéo est prête — {proj_name}"
        dl_link = download_url or job_url
        body = f"""Bonjour,

Votre tutoriel vidéo "{job_name}" est prêt !

📥 Télécharger la vidéo :
{dl_link}

Ou accéder au cockpit :
{job_url}

—
TutorBuilder Vision"""

    elif event == "error":
        subject = f"❌ Erreur de traitement — {proj_name}"
        error_msg = job.error_message or "Erreur inconnue"
        body = f"""Bonjour,

Une erreur est survenue lors du traitement de "{job_name}" :

{error_msg}

Vous pouvez reprendre depuis le cockpit :
{job_url}

—
TutorBuilder Vision"""

    else:
        return

    # ── Envoi ─────────────────────────────────────────────────────────────
    try:
        send_mail(
            subject      = subject,
            message      = body,
            from_email   = getattr(settings, "DEFAULT_FROM_EMAIL", "noreply@univers-ia.pro"),
            recipient_list = [email],
            fail_silently  = False,
        )
        logger.info(f"Email '{event}' envoyé à {email} pour job {job.pk}")
    except Exception as e:
        logger.error(f"Échec envoi email '{event}' : {e}")