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
        import json
        ctx = super().get_context_data(**kwargs)
        user_projects = Project.objects.filter(owner=self.request.user)

        # Tous les projets avec leurs jobs préchargés
        projects = list(
            user_projects.prefetch_related("jobs").order_by("-updated_at")
        )

        # Annoter jobs_count et jobs_json sur chaque projet (pour le template)
        for p in projects:
            jobs = list(p.jobs.order_by("-created_at"))
            p.jobs_count = len(jobs)
            p.jobs_json  = json.dumps([{
                "id":           str(j.pk),
                "filename":     j.video_filename,
                "status":       j.status,
                "status_label": j.get_status_display(),
                "language":     j.language or "fr",
                "tts_ready":    j.status in ("done", "synthesizing"),
            } for j in jobs], ensure_ascii=False)

        # JSON global pour la modale "Réutiliser" (PROJECTS_DATA dans le JS)
        projects_json = json.dumps([{
            "id":   str(p.pk),
            "name": p.name,
            "jobs": [{
                "id":           str(j.pk),
                "filename":     j.video_filename,
                "status":       j.status,
                "status_label": j.get_status_display(),
                "language":     j.language or "fr",
                "tts_ready":    j.status in ("done", "synthesizing"),
            } for j in p.jobs.order_by("-created_at")]
        } for p in projects], ensure_ascii=False)

        ctx.update({
            "projects":       projects,
            "projects_json":  projects_json,
            "total_projects": user_projects.count(),
            "total_jobs":     Job.objects.filter(project__owner=self.request.user).count(),
            "total_done":     Job.objects.filter(project__owner=self.request.user, status="done").count(),
            "active_jobs":    Job.objects.filter(
                project__owner=self.request.user,
                status__in=["extracting", "transcribing", "synthesizing", "uploading"]
            ).count(),
        })
        return ctx


# ─────────────────────────────────────────
#  COCKPIT
# ─────────────────────────────────────────

from pathlib import Path
from django.conf import settings

@method_decorator(never_cache, name='dispatch')
class CockpitView(LoginRequiredMixin, TemplateView):
    template_name = "studio/cockpit.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)

        ctx["user_projects"] = Project.objects.filter(
            owner=self.request.user
        ).order_by("-updated_at")

        ctx["languages"] = [
            ("fr", "Français"),
            ("en", "Anglais"),
            ("es", "Espagnol"),
            ("de", "Allemand"),
            ("it", "Italien"),
            ("pt", "Portugais"),
        ]

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

        # Mode de reprise depuis le dashboard (video | transcript | voice)
        ctx["reuse_mode"] = self.request.GET.get("mode", "")

        if job:
            video_url = ""
            if job.video_file:
                try:
                    video_url = job.video_file.url
                except Exception:
                    rel = str(job.video_file).replace("\\", "/")
                    video_url = f"{settings.MEDIA_URL.rstrip('/')}/{rel.lstrip('/')}"

            ctx["video_url"] = video_url

            final_url  = ""
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


# ─────────────────────────────────────────
#  DUPLIQUER UN JOB (Réutiliser la vidéo)
# ─────────────────────────────────────────

import shutil
from django.shortcuts import redirect
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST

@require_POST
@login_required
def duplicate_job(request, job_id):
    """
    Crée un nouveau job en copiant la vidéo du job source.
    Redirige directement vers le cockpit du nouveau job.
    """
    import uuid

    source = get_object_or_404(Job, pk=job_id, project__owner=request.user)

    # Créer le nouveau job avec les mêmes paramètres
    new_job = Job.objects.create(
        project=source.project,
        video_filename=source.video_filename,
        stt_engine=source.stt_engine,
        tts_engine=source.tts_engine,
        language=source.language,
        status=Job.Status.PENDING,
    )

    # Copier le fichier vidéo physiquement
    if source.video_file and source.video_file.name:
        src_path  = Path(source.video_file.path)
        if src_path.exists():
            # Construire le nouveau chemin dans le même dossier jobs/<new_pk>/
            new_dir  = Path(settings.MEDIA_ROOT) / 'jobs' / str(new_job.pk)
            new_dir.mkdir(parents=True, exist_ok=True)
            new_path = new_dir / src_path.name
            shutil.copy2(str(src_path), str(new_path))

            # Mettre à jour le champ FileField
            rel = str(new_path.relative_to(settings.MEDIA_ROOT))
            new_job.video_file = rel
            new_job.save(update_fields=['video_file'])

    new_job.output_dir.mkdir(parents=True, exist_ok=True)

    return redirect('studio:cockpit_job', job_id=new_job.pk)