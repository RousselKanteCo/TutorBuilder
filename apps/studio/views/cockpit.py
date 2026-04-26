"""
apps/studio/views/cockpit.py
"""

import json
from django.views.generic import TemplateView
from django.contrib.auth.mixins import LoginRequiredMixin
from ..models import Project, Job


class CockpitView(LoginRequiredMixin, TemplateView):
    template_name = "studio/cockpit.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["user_projects"] = Project.objects.filter(
            owner=self.request.user
        ).order_by("-updated_at")

        job_id = self.kwargs.get("job_id")
        job    = None
        video_url  = ''
        job_data   = '{}'

        if job_id:
            try:
                job = Job.objects.get(pk=job_id, project__owner=self.request.user)

                # URL vidéo
                if job.video_file:
                    try:
                        video_url = job.video_file.url
                    except Exception:
                        pass

                # Données job pour le JS
                job_data = json.dumps({
                    "id":             str(job.pk),
                    "title":          job.title,
                    "display_name":   job.display_name,
                    "video_filename": job.video_filename,
                    "video_url":      video_url,
                    "status":         job.status,
                    "project_id":     str(job.project.pk),
                    "project_name":   job.project.name,
                    "waveform_data":  job.waveform_data or [],
                })

            except Job.DoesNotExist:
                pass

        ctx["job"]       = job
        ctx["video_url"] = video_url
        ctx["job_data"]  = job_data

        return ctx