#!/usr/bin/env python
"""
manage.py — Point d'entrée Django CLI.

Usage :
    python manage.py runserver        # Dev HTTP (sans WebSocket)
    python manage.py migrate
    python manage.py createsuperuser
    python manage.py collectstatic

Pour WebSocket (dev + prod) : utiliser daphne via config/asgi.py
"""

import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django introuvable. Vérifiez que l'environnement virtuel est activé "
            "et que les dépendances sont installées : pip install -r requirements.txt"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
