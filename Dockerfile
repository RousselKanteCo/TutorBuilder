FROM python:3.10-slim

# Dépendances système
RUN apt-get update && apt-get install -y \
    ffmpeg \
    git \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Installer les dépendances Python
COPY requirements.txt .
RUN pip install --no-cache-dir \
    Django \
    django-environ \
    djangorestframework \
    django-cors-headers \
    drf-spectacular \
    channels \
    channels-redis \
    daphne \
    django-extensions \
    django-storages \
    whitenoise \
    celery \
    redis \
    python-dotenv \
    Pillow \
    numpy \
    scipy \
    soundfile \
    requests \
    openai-whisper \
    pyttsx3 \
    psycopg2-binary

# Copier le code
COPY . .

# Variables d'environnement
ENV DJANGO_SETTINGS_MODULE=config.settings.production
ENV PYTHONUNBUFFERED=1

# Collecter les fichiers statiques
RUN python manage.py collectstatic --noinput || true

EXPOSE 8000

RUN mkdir -p /app/logs

CMD ["daphne", "-b", "0.0.0.0", "-p", "8000", "config.asgi:application"]