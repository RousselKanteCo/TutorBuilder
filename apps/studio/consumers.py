"""
apps/studio/consumers.py — Consumers WebSocket pour la progression des jobs.

Remplacement des signaux Qt de engine_ia.py :
    AnalyseWorker.status      → canal WS "status"
    AnalyseWorker.waveform_ready → canal WS "waveform"
    TTSWorker.progress        → canal WS "tts_progress"
    TTSWorker.finished        → canal WS "tts_done"

Protocole de messages (JSON) :
    → Serveur vers client :
        {"type": "status",       "message": "🔊 Extraction audio..."}
        {"type": "progress",     "step": 2, "total": 4, "label": "Transcription"}
        {"type": "waveform",     "data": [0.1, 0.4, ...]}
        {"type": "segments",     "data": [...]}
        {"type": "tts_progress", "current": 3, "total": 10}
        {"type": "tts_done",     "files": [...]}
        {"type": "error",        "message": "..."}

    ← Client vers serveur :
        {"action": "ping"}
        {"action": "cancel"}    (à implémenter)
"""

import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)


class JobProgressConsumer(AsyncWebsocketConsumer):
    """
    Consumer WebSocket attaché à un job spécifique.

    Chaque job a son propre groupe Channels (job_<uuid>).
    Les tâches Celery envoient des messages à ce groupe via
    channel_layer.group_send() depuis tasks.py.
    """

    async def connect(self):
        self.job_id = self.scope["url_route"]["kwargs"]["job_id"]
        self.group_name = f"job_{self.job_id}"

        # Rejoindre le groupe du job
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name,
        )

        await self.accept()

        # Envoyer un message de bienvenue
        await self.send(json.dumps({
            "type": "connected",
            "job_id": self.job_id,
            "message": "Connexion WebSocket établie",
        }))

        logger.debug(f"WebSocket connecté — job {self.job_id}")

    async def disconnect(self, close_code):
        # Quitter le groupe
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name,
        )
        logger.debug(f"WebSocket déconnecté — job {self.job_id} (code: {close_code})")

    async def receive(self, text_data):
        """Messages reçus du navigateur."""
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        action = data.get("action")

        if action == "ping":
            await self.send(json.dumps({"type": "pong"}))

        elif action == "cancel":
            # TODO : révoquer la tâche Celery
            await self.send(json.dumps({
                "type": "status",
                "message": "⚠️ Annulation non encore implémentée.",
            }))

    # ── Handlers de messages envoyés par les tâches Celery ──
    # Chaque méthode correspond à un type de message Channels.
    # Convention : le type "job.status" → méthode "job_status"

    async def job_status(self, event):
        """Relaie un message de statut vers le navigateur."""
        await self.send(json.dumps({
            "type": "status",
            "message": event["message"],
        }))

    async def job_progress(self, event):
        """Relaie la progression d'une étape."""
        await self.send(json.dumps({
            "type": "progress",
            "step": event.get("step"),
            "total": event.get("total"),
            "label": event.get("label"),
            "percent": event.get("percent", 0),
        }))

    async def job_waveform(self, event):
        """Relaie les données waveform (liste de floats)."""
        await self.send(json.dumps({
            "type": "waveform",
            "data": event["data"],
        }))

    async def job_segments(self, event):
        """Relaie les segments de transcription."""
        await self.send(json.dumps({
            "type": "segments",
            "data": event["data"],
        }))

    async def job_tts_progress(self, event):
        """Relaie la progression du TTS."""
        await self.send(json.dumps({
            "type": "tts_progress",
            "current": event["current"],
            "total": event["total"],
            "message": event.get("message", ""),
        }))

    async def job_tts_done(self, event):
        """Relaie la fin de la synthèse vocale."""
        await self.send(json.dumps({
            "type": "tts_done",
            "files": event.get("files", []),
            "nb_ok": event.get("nb_ok", 0),
            "nb_total": event.get("nb_total", 0),
        }))

    async def job_export_done(self, event):
        """Relaie la fin du montage vidéo final."""
        await self.send(json.dumps({
            "type":         "export_done",
            "download_url": event.get("download_url", ""),
            "file_size_mb": event.get("file_size_mb", 0),
        }))

    async def job_error(self, event):
        """Relaie une erreur."""
        await self.send(json.dumps({
            "type": "error",
            "message": event["message"],
        }))