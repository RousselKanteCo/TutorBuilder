"""
config/settings/production.py — Paramètres de production.
Hérite de base.py et active les sécurités HTTPS, le cache Redis, etc.
"""

from .base import *  # noqa: F401, F403

# ─────────────────────────────────────────
#  SÉCURITÉ HTTPS
# ─────────────────────────────────────────

DEBUG = False

SECURE_SSL_REDIRECT = False
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
CSRF_COOKIE_HTTPONLY = True


# ─────────────────────────────────────────
#  BASE DE DONNÉES (SQLite)
# ─────────────────────────────────────────

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}


# ─────────────────────────────────────────
#  CACHE REDIS (optionnel)
# ─────────────────────────────────────────

_redis_url = env("REDIS_URL", default="")
if _redis_url:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": _redis_url,
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
    "filename": "/app/logs/django.log",
    "maxBytes": 10 * 1024 * 1024,
    "backupCount": 5,
    "formatter": "verbose",
}

LOGGING["root"]["handlers"].append("file")  # type: ignore[name-defined]