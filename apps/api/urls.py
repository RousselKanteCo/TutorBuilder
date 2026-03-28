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
    # ── Racine API ──
    path("", include(router.urls)),

    # ── Système ──
    path("health/", views.HealthView.as_view(), name="health"),
    path("providers/", views.ProvidersView.as_view(), name="providers"),

    # ── Tâches Celery (polling HTTP) ──
    path("tasks/<str:task_id>/", views.TaskStatusView.as_view(), name="task_status"),

    # ── Segments ──
    path("segments/<int:pk>/", views.SegmentUpdateView.as_view(), name="segment_update"),

    # ── Documentation Swagger (drf_spectacular) ──  ← les 3 lignes manquantes
    path("schema/", SpectacularAPIView.as_view(), name="schema"),
    path("docs/", SpectacularSwaggerView.as_view(url_name="api:schema"), name="swagger-ui"),
    path("redoc/", SpectacularRedocView.as_view(url_name="api:schema"), name="redoc"),
]