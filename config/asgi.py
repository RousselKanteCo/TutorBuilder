"""
config/asgi.py — Point d'entrée ASGI pour Django Channels.

Gère à la fois :
- Les requêtes HTTP classiques (Django views)
- Les connexions WebSocket (Django Channels → consumers.py)

Le WebSocket remplace les signaux Qt (pyqtSignal) de l'app desktop.
Daphne sert ce fichier à la place de Uvicorn.

Lancement dev :
    daphne -p 8000 config.asgi:application

Lancement prod (via Docker) :
    daphne -b 0.0.0.0 -p 8000 config.asgi:application
"""

import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

# Application Django standard (HTTP)
django_asgi_app = get_asgi_application()

# Import des routes WebSocket après initialisation Django
from apps.studio.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    # Requêtes HTTP → Django views classiques
    "http": django_asgi_app,

    # Connexions WebSocket → Django Channels consumers
    # AllowedHostsOriginValidator vérifie l'origine (sécurité CSRF WebSocket)
    # AuthMiddlewareStack injecte request.user dans le scope
    "websocket": AllowedHostsOriginValidator(
        AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        )
    ),
})
