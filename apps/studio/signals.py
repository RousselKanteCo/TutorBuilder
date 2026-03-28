"""
apps/studio/signals.py — Signaux Django pour le studio.

Les signaux remplacent une partie des connexions Qt (connect/disconnect)
de l'app desktop, notamment pour le nettoyage automatique des fichiers.
"""

from django.db.models.signals import post_delete
from django.dispatch import receiver


# Les imports des modèles sont faits en lazy pour éviter les imports circulaires

@receiver(post_delete, sender="studio.Job")
def supprimer_fichiers_job(sender, instance, **kwargs):
    """
    Supprime les fichiers uploadés et générés quand un Job est supprimé.
    Équivalent de l'endpoint DELETE /job/{job_id} dans server.py.
    """
    import os
    import shutil

    # Supprimer la vidéo uploadée
    if instance.video_file and hasattr(instance.video_file, "path"):
        try:
            if os.path.exists(instance.video_file.path):
                os.remove(instance.video_file.path)
        except Exception:
            pass

    # Supprimer le dossier de sortie du job
    from django.conf import settings
    output_dir = settings.OUTPUTS_ROOT / str(instance.pk)
    if output_dir.is_dir():
        shutil.rmtree(output_dir, ignore_errors=True)
