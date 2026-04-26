"""
apps/studio/views/dashboard.py — Dashboard principal.
"""
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import logout
from django.http import JsonResponse
from django.views.decorators.http import require_POST
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated

from ..models import Project, Job

logger = logging.getLogger(__name__)


@login_required
def dashboard_view(request):
    """Page principale — liste tous les projets et jobs."""
    from django.core.paginator import Paginator

    page_num = request.GET.get('page', 1)
    projects_qs = Project.objects.filter(
        owner=request.user
    ).prefetch_related("jobs").order_by("-updated_at")

    paginator = Paginator(projects_qs, 5)  # 5 projets par page
    page_obj  = paginator.get_page(page_num)

    # Stats globales
    total_projects = projects_qs.count()
    total_jobs     = Job.objects.filter(project__owner=request.user).count()
    done_jobs      = Job.objects.filter(project__owner=request.user, status="done").count()

    import json
    from django.utils.formats import date_format

    # Préparer les jobs par projet en JSON pour le front
    jobs_by_project = {}
    for project in page_obj.object_list:
        jobs_by_project[str(project.pk)] = [
            {
                "id":            str(j.pk),
                "display_name":  j.display_name,
                "video_filename": j.video_filename or '',
                "status":        j.status,
                "status_display": j.get_status_display(),
                "created_at":    j.created_at.strftime("%d/%m/%Y"),
            }
            for j in project.jobs.order_by("-created_at")
        ]

    return render(request, "studio/dashboard.html", {
        "page_obj":        page_obj,
        "projects":        page_obj.object_list,
        "total_projects":  total_projects,
        "total_jobs":      total_jobs,
        "done_jobs":       done_jobs,
        "jobs_by_project": json.dumps(jobs_by_project),
    })


def logout_view(request):
    """Déconnexion."""
    logout(request)
    return redirect('/accounts/login/')


class ProjectCreateAPIView(APIView):
    """POST /api/projects/create/ — Créer un projet."""
    permission_classes = [IsAuthenticated]

    def post(self, request):
        name = request.data.get("name", "").strip()
        desc = request.data.get("description", "").strip()

        if not name:
            return Response({"error": "Le nom du projet est requis."}, status=400)

        project = Project.objects.create(
            owner=request.user,
            name=name,
            description=desc,
        )
        return Response({
            "id":   str(project.pk),
            "name": project.name,
        }, status=201)


class ProjectDeleteAPIView(APIView):
    """DELETE /api/projects/<id>/delete/ — Supprimer un projet."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, project_id):
        project = get_object_or_404(Project, pk=project_id, owner=request.user)
        project.delete()
        return Response({"status": "deleted"})


class JobDeleteAPIView(APIView):
    """DELETE /api/jobs/<id>/delete/ — Supprimer un job."""
    permission_classes = [IsAuthenticated]

    def delete(self, request, job_id):
        job = get_object_or_404(Job, pk=job_id, project__owner=request.user)
        job.delete()
        return Response({"status": "deleted"})