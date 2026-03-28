"""
config/settings/base.py — Paramètres communs à tous les environnements.
Les valeurs sensibles sont lues depuis .env via django-environ.
"""

import environ
import os
from pathlib import Path

# ─────────────────────────────────────────
#  CHEMINS DE BASE
# ─────────────────────────────────────────

# BASE_DIR pointe sur la racine du projet (parent de config/)
BASE_DIR = Path(__file__).resolve().parent.parent.parent

env = environ.Env(
    DJANGO_DEBUG=(bool, False),
    MAX_UPLOAD_SIZE_MB=(int, 500),
    USE_S3=(bool, False),
)

# Lire le fichier .env s'il existe
environ.Env.read_env(BASE_DIR / ".env")


# ─────────────────────────────────────────
#  SÉCURITÉ
# ─────────────────────────────────────────

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env("DJANGO_DEBUG")
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=["localhost", "127.0.0.1"])


# ─────────────────────────────────────────
#  APPLICATIONS INSTALLÉES
# ─────────────────────────────────────────

DJANGO_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "channels",
    "django_extensions",
    "storages",
]

LOCAL_APPS = [
    "apps.studio",
    "apps.api",
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS


# ─────────────────────────────────────────
#  MIDDLEWARE
# ─────────────────────────────────────────

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",    # Fichiers statiques
    "corsheaders.middleware.CorsMiddleware",          # CORS (avant CommonMiddleware)
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ─────────────────────────────────────────
#  URLs & WSGI / ASGI
# ─────────────────────────────────────────

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"   # Django Channels


# ─────────────────────────────────────────
#  TEMPLATES
# ─────────────────────────────────────────

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


# ─────────────────────────────────────────
#  BASE DE DONNÉES
# ─────────────────────────────────────────

DATABASES = {
    "default": {
        "ENGINE": env("DB_ENGINE", default="django.db.backends.postgresql"),
        "NAME": env("DB_NAME", default="tutobuilder"),
        "USER": env("DB_USER", default="tutobuilder"),
        "PASSWORD": env("DB_PASSWORD", default="changeme"),
        "HOST": env("DB_HOST", default="localhost"),
        "PORT": env("DB_PORT", default="5432"),
    }
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"


# ─────────────────────────────────────────
#  DJANGO CHANNELS (WebSocket)
# ─────────────────────────────────────────

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [env("REDIS_URL", default="redis://localhost:6379/0")],
        },
    },
}


# ─────────────────────────────────────────
#  CELERY
# ─────────────────────────────────────────

CELERY_BROKER_URL = env("CELERY_BROKER_URL", default="redis://localhost:6379/0")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", default="redis://localhost:6379/0")
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 600        # 10 min max par tâche
CELERY_TASK_SOFT_TIME_LIMIT = 540   # Warning à 9 min
CELERY_WORKER_MAX_TASKS_PER_CHILD = 50  # Redémarre pour libérer mémoire
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
CELERY_TASK_EAGER_PROPAGATES = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)

# Les tâches sont définies dans apps/studio/tasks.py
CELERY_IMPORTS = ["apps.studio.tasks"]


# ─────────────────────────────────────────
#  DJANGO REST FRAMEWORK
# ─────────────────────────────────────────

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
        "rest_framework.authentication.BasicAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 20,
}

# Documentation OpenAPI
SPECTACULAR_SETTINGS = {
    "TITLE": "TutoBuilder Vision API",
    "DESCRIPTION": "API pour la production de tutoriels vidéo assistée par IA",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}


# ─────────────────────────────────────────
#  CORS
# ─────────────────────────────────────────

CORS_ALLOW_ALL_ORIGINS = DEBUG  # Permissif en dev uniquement
CORS_ALLOWED_ORIGINS = env.list(
    "CORS_ALLOWED_ORIGINS",
    default=["http://localhost:3000", "http://127.0.0.1:8000"],
)
CORS_ALLOW_CREDENTIALS = True


# ─────────────────────────────────────────
#  FICHIERS STATIQUES & MÉDIAS
# ─────────────────────────────────────────

STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / env("MEDIA_ROOT", default="media")

# Dossier pour les fichiers générés (audio TTS, vidéos finales)
OUTPUTS_ROOT = BASE_DIR / env("OUTPUTS_ROOT", default="outputs")

# Dossier pour les modèles IA locaux (Vosk, etc.)
MODELS_ROOT = BASE_DIR / env("MODELS_ROOT", default="models")


# ─────────────────────────────────────────
#  STOCKAGE S3 / MINIO (optionnel)
# ─────────────────────────────────────────

USE_S3 = env("USE_S3")

if USE_S3:
    DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL", default=None)
    AWS_S3_FILE_OVERWRITE = False
    AWS_DEFAULT_ACL = None


# ─────────────────────────────────────────
#  UPLOAD
# ─────────────────────────────────────────

MAX_UPLOAD_SIZE_MB = env("MAX_UPLOAD_SIZE_MB")
MAX_UPLOAD_SIZE = MAX_UPLOAD_SIZE_MB * 1024 * 1024

DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE
FILE_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_SIZE


# ─────────────────────────────────────────
#  API EXTERNES
# ─────────────────────────────────────────

ELEVENLABS_API_KEY = env("ELEVENLABS_API_KEY", default="")


# ─────────────────────────────────────────
#  INTERNATIONALISATION
# ─────────────────────────────────────────

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Europe/Paris"
USE_I18N = True
USE_TZ = True


# ─────────────────────────────────────────
#  VALIDATION MOTS DE PASSE
# ─────────────────────────────────────────

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ─────────────────────────────────────────
#  LOGGING
# ─────────────────────────────────────────

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {process:d} {thread:d} {message}",
            "style": "{",
        },
        "simple": {
            "format": "{levelname} {asctime} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "simple",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "django": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "apps.studio": {
            "handlers": ["console"],
            "level": "DEBUG",
            "propagate": False,
        },
    },
}
