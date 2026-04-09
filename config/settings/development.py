"""
config/settings/development.py — Paramètres de développement local.
Hérite de base.py et active debug toolbar, SQLite fallback, etc.
"""

from .base import *  # noqa: F401, F403

# ─────────────────────────────────────────
#  DÉVELOPPEMENT
# ─────────────────────────────────────────

DEBUG = True

# En dev, on peut utiliser SQLite pour démarrer vite sans PostgreSQL
DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default=f"sqlite:///{BASE_DIR / 'db_dev.sqlite3'}",
    )
}

# ─────────────────────────────────────────
#  CHANNELS — Mémoire en dev (pas besoin de Redis)
# ─────────────────────────────────────────
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels.layers.InMemoryChannelLayer",
    }
}

# ─────────────────────────────────────────
#  EMAILS (sortie console en dev)
# ─────────────────────────────────────────

EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"


# ─────────────────────────────────────────
#  CACHE (local-memory en dev, Redis en prod)
# ─────────────────────────────────────────

CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
    }
}


# ─────────────────────────────────────────
#  SÉCURITÉ DÉSACTIVÉE EN DEV
# ─────────────────────────────────────────

SECURE_SSL_REDIRECT = False
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# ─────────────────────────────────────────
#  ALLOWED HOSTS — ngrok en dev
# ─────────────────────────────────────────

ALLOWED_HOSTS = ['localhost', '127.0.0.1', '.ngrok-free.app', '.ngrok-free.dev']

CSRF_TRUSTED_ORIGINS = ['https://*.ngrok-free.app', 'https://*.ngrok-free.dev']