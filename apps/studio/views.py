"""
apps/studio/views.py — Vues Django du cockpit TutoBuilder.
"""

import logging
from django.views.generic import (
    TemplateView, ListView, DetailView,
    CreateView, DeleteView,
)
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from .models import Project, Job

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────
#  HEALTH CHECK (Docker)
# ─────────────────────────────────────────

def health_check(request):
    from django.db import connection
    try:
        connection.ensure_connection()
        db_ok = True
    except Exception:
        db_ok = False

    status = 200 if db_ok else 503
    return JsonResponse({
        "status": "ok" if db_ok else "error",
        "db": "ok" if db_ok else "down",
    }, status=status)


# ─────────────────────────────────────────
#  DASHBOARD
# ─────────────────────────────────────────

class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "studio/dashboard.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        user_projects = Project.objects.filter(owner=self.request.user)
        ctx.update({
            "projects":       user_projects.order_by("-updated_at")[:6],
            "total_projects": user_projects.count(),
            "total_jobs":     Job.objects.filter(project__owner=self.request.user).count(),
            "recent_jobs":    Job.objects.filter(
                project__owner=self.request.user
            ).order_by("-created_at").select_related("project")[:5],
        })
        return ctx


# ─────────────────────────────────────────
#  COCKPIT (interface principale)
# ─────────────────────────────────────────

class CockpitView(LoginRequiredMixin, TemplateView):
    template_name = "studio/cockpit.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        job = None
        video_url = ""
        final_url = ""
        job_id = self.kwargs.get("job_id")

        if job_id:
            job = get_object_or_404(
                Job.objects.select_related("project"),
                pk=job_id,
                project__owner=self.request.user,
            )
            try:
                if job.video_file:
                    video_url = job.video_file.url
            except Exception:
                video_url = ""

            # Vérifier si la vidéo finale existe sur le disque
            from pathlib import Path
            from django.conf import settings
            final_path = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk) / "final.mp4"
            if final_path.exists() and final_path.stat().st_size > 10_000:
                final_url = f"{settings.MEDIA_URL.rstrip('/')}/exports/{job.pk}/final.mp4"

        ctx.update({
            "job":           job,
            "video_url":     video_url,
            "final_url":     final_url,   # ← nouveau
            "stt_engines":   Job.STTEngine.choices,
            "tts_engines":   Job.TTSEngine.choices,
            "languages":     Job.Language.choices,
            "user_projects": Project.objects.filter(
                owner=self.request.user
            ).order_by("name"),
        })
        return ctx
# ─────────────────────────────────────────
#  PROJETS
# ─────────────────────────────────────────

class ProjectListView(LoginRequiredMixin, ListView):
    model = Project
    template_name = "studio/project_list.html"
    context_object_name = "projects"
    paginate_by = 12

    def get_queryset(self):
        return Project.objects.filter(
            owner=self.request.user
        ).prefetch_related("jobs").order_by("-updated_at")


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model = Project
    template_name = "studio/project_form.html"
    fields = ["name", "description"]
    success_url = reverse_lazy("studio:dashboard")

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class ProjectDetailView(LoginRequiredMixin, DetailView):
    model = Project
    template_name = "studio/project_detail.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["jobs"] = self.object.jobs.order_by("-created_at")
        return ctx


class ProjectDeleteView(LoginRequiredMixin, DeleteView):
    model = Project
    template_name = "studio/project_confirm_delete.html"
    success_url = reverse_lazy("studio:project_list")

    def get_queryset(self):
        return Project.objects.filter(owner=self.request.user)


# ─────────────────────────────────────────
#  JOBS
# ─────────────────────────────────────────

class JobDetailView(LoginRequiredMixin, DetailView):
    model = Job
    template_name = "studio/job_detail.html"
    context_object_name = "job"

    def get_queryset(self):
        return Job.objects.filter(
            project__owner=self.request.user
        ).select_related("project").prefetch_related("segments")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["segments"] = self.object.segments.order_by("index")
        return ctx