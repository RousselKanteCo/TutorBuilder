"""
config/wsgi.py — Point d'entrée WSGI (utilisé par Gunicorn en prod sans WebSocket).
En pratique, on utilise ASGI (daphne) pour avoir les WebSockets.
"""

import os
from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

application = get_wsgi_application()
