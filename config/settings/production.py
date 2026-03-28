"""
config/settings/production.py — Paramètres de production.
Hérite de base.py et active les sécurités HTTPS, le cache Redis, etc.
"""

from .base import *  # noqa: F401, F403

# ─────────────────────────────────────────
#  SÉCURITÉ HTTPS
# ─────────────────────────────────────────

DEBUG = False

SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000          # 1 an
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True


# ─────────────────────────────────────────
#  BASE DE DONNÉES (PostgreSQL obligatoire en prod)
# ─────────────────────────────────────────

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("DB_NAME"),
        "USER": env("DB_USER"),
        "PASSWORD": env("DB_PASSWORD"),
        "HOST": env("DB_HOST", default="db"),
        "PORT": env("DB_PORT", default="5432"),
        "CONN_MAX_AGE": 600,            # Connexions persistantes (10 min)
    }
}


# ─────────────────────────────────────────
#  CACHE REDIS (sessions, rate-limiting)
# ─────────────────────────────────────────

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": env("REDIS_URL", default="redis://redis:6379/1"),
    }
}

SESSION_ENGINE = "django.contrib.sessions.backends.cache"
SESSION_CACHE_ALIAS = "default"


# ─────────────────────────────────────────
#  EMAILS
# ─────────────────────────────────────────

EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="smtp.mailgun.org")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_USE_TLS = True
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="noreply@tutobuilder.io")


# ─────────────────────────────────────────
#  LOGGING (fichier en prod)
# ─────────────────────────────────────────

LOGGING["handlers"]["file"] = {  # type: ignore[name-defined]
    "class": "logging.handlers.RotatingFileHandler",
    "filename": "/var/log/tutobuilder/django.log",
    "maxBytes": 10 * 1024 * 1024,  # 10 Mo
    "backupCount": 5,
    "formatter": "verbose",
}

LOGGING["root"]["handlers"].append("file")  # type: ignore[name-defined]
