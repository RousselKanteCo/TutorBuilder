"""
apps/studio/urls.py — URLs de l'application studio (cockpit).

Toutes les vues HTML (templates Django) sont ici.
Les endpoints REST sont dans apps/api/urls.py.
"""

from django.urls import path
from . import views

app_name = "studio"

urlpatterns = [
    # ── Dashboard : liste des projets ──
    path("", views.DashboardView.as_view(), name="dashboard"),

    # ── Cockpit : interface de production (équivalent PyQt6 MonumentV8) ──
    path("cockpit/", views.CockpitView.as_view(), name="cockpit"),
    path("cockpit/<uuid:job_id>/", views.CockpitView.as_view(), name="cockpit_job"),

    # ── Projets ──
    path("projects/", views.ProjectListView.as_view(), name="project_list"),
    path("projects/new/", views.ProjectCreateView.as_view(), name="project_create"),
    path("projects/<uuid:pk>/", views.ProjectDetailView.as_view(), name="project_detail"),
    path("projects/<uuid:pk>/delete/", views.ProjectDeleteView.as_view(), name="project_delete"),

    # ── Jobs ──
    path("jobs/<uuid:pk>/", views.JobDetailView.as_view(), name="job_detail"),

    # ── Health check (utilisé par Docker) ──
    path("health/", views.health_check, name="health"),
]
