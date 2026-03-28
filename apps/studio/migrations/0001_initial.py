"""
Migration initiale — Crée les tables Project, Job, Segment.
Générée automatiquement, puis vérifiée manuellement.
"""

import django.db.models.deletion
import uuid
import apps.studio.models
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ─────────────────────────────────────────
        #  PROJECT
        # ─────────────────────────────────────────
        migrations.CreateModel(
            name="Project",
            fields=[
                ("id", models.UUIDField(
                    default=uuid.uuid4, editable=False,
                    primary_key=True, serialize=False,
                )),
                ("name", models.CharField(max_length=255, verbose_name="nom")),
                ("description", models.TextField(blank=True, verbose_name="description")),
                ("created_at", models.DateTimeField(
                    auto_now_add=True, verbose_name="créé le",
                )),
                ("updated_at", models.DateTimeField(
                    auto_now=True, verbose_name="mis à jour le",
                )),
                ("owner", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="projects",
                    to=settings.AUTH_USER_MODEL,
                    verbose_name="propriétaire",
                )),
            ],
            options={
                "verbose_name": "projet",
                "verbose_name_plural": "projets",
                "ordering": ["-updated_at"],
            },
        ),

        # ─────────────────────────────────────────
        #  JOB
        # ─────────────────────────────────────────
        migrations.CreateModel(
            name="Job",
            fields=[
                ("id", models.UUIDField(
                    default=uuid.uuid4, editable=False,
                    primary_key=True, serialize=False,
                )),
                ("video_file", models.FileField(
                    max_length=500,
                    upload_to=apps.studio.models.video_upload_path,
                    verbose_name="fichier vidéo",
                )),
                ("video_filename", models.CharField(
                    blank=True, max_length=255, verbose_name="nom original",
                )),
                ("video_duration_ms", models.PositiveIntegerField(
                    blank=True, null=True, verbose_name="durée (ms)",
                )),
                ("stt_engine", models.CharField(
                    choices=[
                        ("whisper", "Whisper (OpenAI)"),
                        ("faster_whisper", "Faster-Whisper"),
                        ("vosk", "Vosk (local léger)"),
                    ],
                    default="faster_whisper",
                    max_length=20,
                    verbose_name="moteur STT",
                )),
                ("tts_engine", models.CharField(
                    choices=[
                        ("coqui", "Coqui TTS (local)"),
                        ("elevenlabs", "ElevenLabs (cloud)"),
                        ("bark", "Bark / Suno (local expressif)"),
                    ],
                    default="coqui",
                    max_length=20,
                    verbose_name="moteur TTS",
                )),
                ("tts_voice", models.CharField(
                    default="default", max_length=100, verbose_name="voix TTS",
                )),
                ("language", models.CharField(
                    choices=[
                        ("fr", "Français"), ("en", "English"),
                        ("es", "Español"), ("de", "Deutsch"),
                    ],
                    default="fr",
                    max_length=5,
                    verbose_name="langue",
                )),
                ("status", models.CharField(
                    choices=[
                        ("pending", "En attente"),
                        ("uploading", "Upload en cours"),
                        ("extracting", "Extraction audio"),
                        ("transcribing", "Transcription"),
                        ("transcribed", "Transcription terminée"),
                        ("synthesizing", "Synthèse vocale"),
                        ("done", "Terminé"),
                        ("error", "Erreur"),
                    ],
                    default="pending",
                    max_length=20,
                    verbose_name="statut",
                )),
                ("error_message", models.TextField(blank=True, verbose_name="message d'erreur")),
                ("celery_task_id", models.CharField(
                    blank=True, max_length=255, verbose_name="ID tâche Celery",
                )),
                ("waveform_data", models.JSONField(
                    blank=True, default=list, verbose_name="données waveform",
                )),
                ("thumbnail_paths", models.JSONField(
                    blank=True, default=list, verbose_name="chemins vignettes",
                )),
                ("created_at", models.DateTimeField(
                    auto_now_add=True, verbose_name="créé le",
                )),
                ("updated_at", models.DateTimeField(
                    auto_now=True, verbose_name="mis à jour le",
                )),
                ("completed_at", models.DateTimeField(
                    blank=True, null=True, verbose_name="terminé le",
                )),
                ("project", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="jobs",
                    to="studio.project",
                    verbose_name="projet",
                )),
            ],
            options={
                "verbose_name": "job",
                "verbose_name_plural": "jobs",
                "ordering": ["-created_at"],
            },
        ),

        # ─────────────────────────────────────────
        #  SEGMENT
        # ─────────────────────────────────────────
        migrations.CreateModel(
            name="Segment",
            fields=[
                ("id", models.BigAutoField(
                    auto_created=True, primary_key=True, serialize=False,
                )),
                ("index", models.PositiveSmallIntegerField(verbose_name="index")),
                ("start_ms", models.PositiveIntegerField(verbose_name="début (ms)")),
                ("end_ms", models.PositiveIntegerField(verbose_name="fin (ms)")),
                ("text", models.TextField(verbose_name="texte")),
                ("text_translated", models.TextField(
                    blank=True, verbose_name="texte traduit",
                )),
                ("audio_file", models.CharField(
                    blank=True, max_length=500, verbose_name="fichier audio",
                )),
                ("job", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="segments",
                    to="studio.job",
                    verbose_name="job",
                )),
            ],
            options={
                "verbose_name": "segment",
                "verbose_name_plural": "segments",
                "ordering": ["job", "index"],
                "unique_together": {("job", "index")},
            },
        ),

        # ─────────────────────────────────────────
        #  INDEX pour les requêtes fréquentes
        # ─────────────────────────────────────────
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["status"], name="job_status_idx"),
        ),
        migrations.AddIndex(
            model_name="job",
            index=models.Index(fields=["project", "-created_at"], name="job_project_date_idx"),
        ),
        migrations.AddIndex(
            model_name="segment",
            index=models.Index(fields=["job", "index"], name="segment_job_index_idx"),
        ),
    ]
