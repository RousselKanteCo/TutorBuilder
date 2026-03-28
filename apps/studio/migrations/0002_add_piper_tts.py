from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("studio", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="job",
            name="tts_engine",
            field=models.CharField(
                choices=[
                    ("coqui",      "Coqui TTS (local)"),
                    ("piper",      "Piper TTS (local, rapide)"),
                    ("elevenlabs", "ElevenLabs (cloud)"),
                    ("bark",       "Bark / Suno (local expressif)"),
                ],
                default="piper",
                max_length=20,
                verbose_name="moteur TTS",
            ),
        ),
    ]