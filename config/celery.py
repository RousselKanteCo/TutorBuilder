"""
config/celery.py — Configuration Celery pour TutoBuilder Vision.

Portage depuis server.py (Monument V8) vers l'architecture Django.
Les tâches sont dans apps/studio/tasks.py.

Lancement :
    celery -A config.celery worker --loglevel=info --concurrency=2
    celery -A config.celery flower --port=5555
"""

import os
from celery import Celery
from django.conf import settings

# Définir le module de settings Django par défaut
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.development")

app = Celery("tutobuilder")

# Lire la config Celery depuis les settings Django (préfixe CELERY_)
app.config_from_object("django.conf:settings", namespace="CELERY")

# Découverte automatique des tâches dans tous les apps installés
app.autodiscover_tasks(lambda: settings.INSTALLED_APPS)


@app.task(bind=True, ignore_result=True)
def debug_task(self):
    """Tâche de test pour vérifier que Celery fonctionne."""
    print(f"Request: {self.request!r}")
