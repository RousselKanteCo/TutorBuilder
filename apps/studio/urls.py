"""
apps/studio/urls.py — URLs de l'application studio (cockpit).
"""

from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from . import views

app_name = "studio"

urlpatterns = [
    # ── Dashboard ──
    path("", views.DashboardView.as_view(), name="dashboard"),

    # ── Cockpit ──
    path("cockpit/", views.CockpitView.as_view(), name="cockpit"),
    path("cockpit/<uuid:job_id>/", views.CockpitView.as_view(), name="cockpit_job"),

    # ── Projets ──
    path("projects/", views.ProjectListView.as_view(), name="project_list"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("projects/<uuid:pk>/", views.ProjectDetailView.as_view(), name="project_detail"),
    path("projects/<uuid:pk>/delete/", views.ProjectDeleteView.as_view(), name="project_delete"),

    # ── Jobs ──
    path("jobs/<uuid:pk>/", views.JobDetailView.as_view(), name="job_detail"),
    path("jobs/<uuid:job_id>/duplicate/", views.duplicate_job, name="job_duplicate"),  # ← NOUVEAU

    # ── Health check ──
    path("health/", views.health_check, name="health"),

] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)