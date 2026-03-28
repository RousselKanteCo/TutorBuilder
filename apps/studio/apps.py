from django.apps import AppConfig


class StudioConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "apps.studio"
    verbose_name = "Studio — Cockpit de production"

    def ready(self):
        # Importer les signals quand l'app est prête
        import apps.studio.signals  # noqa: F401
