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
from django.views.decorators.cache import never_cache
from django.utils.decorators import method_decorator

from .models import Project, Job

logger = logging.getLogger(__name__)
from pathlib import Path


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
            "total_done":     Job.objects.filter(project__owner=self.request.user, status="done").count(),
            "active_jobs":    Job.objects.filter(
                project__owner=self.request.user,
                status__in=["extracting", "transcribing", "synthesizing", "uploading"]
            ).count(),
            "recent_jobs":    Job.objects.filter(
                project__owner=self.request.user
            ).order_by("-created_at").select_related("project")[:50],
        })
        return ctx


# ─────────────────────────────────────────
#  COCKPIT — never_cache pour forcer
#  le rechargement chez tous les clients
# ─────────────────────────────────────────

from pathlib import Path
from django.conf import settings

@method_decorator(never_cache, name='dispatch')
class CockpitView(LoginRequiredMixin, TemplateView):
    template_name = "studio/cockpit.html"
 
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
 
        # Projets de l'utilisateur (pour le sélecteur)
        ctx["user_projects"] = Project.objects.filter(
            owner=self.request.user
        ).order_by("-updated_at")
 
        # Langues disponibles
        ctx["languages"] = [
            ("fr", "Français"),
            ("en", "Anglais"),
            ("es", "Espagnol"),
            ("de", "Allemand"),
            ("it", "Italien"),
            ("pt", "Portugais"),
        ]
 
        # Job courant (si job_id dans l'URL)
        job_id = self.kwargs.get("job_id")
        job    = None
 
        if job_id:
            try:
                job = Job.objects.get(
                    pk=job_id,
                    project__owner=self.request.user
                )
            except Job.DoesNotExist:
                pass
 
        ctx["job"] = job
 
        if job:
            # ── URL vidéo source ─────────────────────────────────────────
            # On construit l'URL relative depuis MEDIA_URL
            video_url = ""
            if job.video_file:
                try:
                    # job.video_file.url retourne /media/jobs/.../video.mp4
                    video_url = job.video_file.url
                except Exception:
                    # Fallback : construire manuellement
                    rel = str(job.video_file).replace("\\", "/")
                    video_url = f"{settings.MEDIA_URL.rstrip('/')}/{rel.lstrip('/')}"
 
            ctx["video_url"] = video_url
 
            # ── URL vidéo finale ─────────────────────────────────────────
            final_url = ""
            exports_dir = Path(settings.MEDIA_ROOT) / "exports" / str(job.pk)
            final_path  = exports_dir / "final.mp4"
 
            if final_path.exists() and final_path.stat().st_size > 10000:
                final_url = f"{settings.MEDIA_URL.rstrip('/')}/exports/{job.pk}/final.mp4"
 
            ctx["final_url"] = final_url
 
        else:
            ctx["video_url"] = ""
            ctx["final_url"] = ""
 
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