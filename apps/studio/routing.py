"""
apps/studio/routing.py — Routes WebSocket pour Django Channels.

Le WebSocket remplace les signaux Qt de l'app desktop :
    Qt signal : worker.status.connect(self.ui.log_monitor.append)
    Web equiv : WebSocket → JS → mise à jour de la console dans le cockpit

URL : ws://host/ws/job/<job_id>/
"""

from django.urls import re_path
from . import consumers

websocket_urlpatterns = [
    # Canal de progression par job
    # Le job_id (UUID) permet au consumer de rejoindre le bon groupe Channels
    re_path(
        r"ws/job/(?P<job_id>[0-9a-f-]+)/$",
        consumers.JobProgressConsumer.as_asgi(),
    ),
]
