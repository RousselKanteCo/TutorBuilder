"""
apps/api/urls.py — Routes de l'API REST.
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView, SpectacularRedocView
from . import views

app_name = "api"

router = DefaultRouter()
router.register(r"projects", views.ProjectViewSet, basename="project")
router.register(r"jobs", views.JobViewSet, basename="job")

urlpatterns = [
    path("", include(router.urls)),
    path("health/", views.HealthView.as_view(), name="health"),
    path("providers/", views.ProvidersView.as_view(), name="providers"),
    path("tasks/<str:task_id>/", views.TaskStatusView.as_view(), name="task_status"),
    path("segments/<int:pk>/", views.SegmentUpdateView.as_view(), name="segment_update"),

    # ── Synthèse : statut détaillé (lu depuis synthesis_plan.json) ──
    path("jobs/<uuid:job_id>/synthesis_status/", views.SynthesisStatusView.as_view(), name="synthesis_status"),

    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema"), name="redoc"),
]