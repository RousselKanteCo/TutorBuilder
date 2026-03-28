"""
config/urls.py — URLs racines du projet TutoBuilder Vision.

Arborescence :
    /                    → studio (cockpit principal)
    /api/                → API REST (DRF)
    /api/schema/         → Schéma OpenAPI
    /api/docs/           → Swagger UI
    /admin/              → Interface admin Django
    /media/              → Fichiers uploadés (dev uniquement)
"""

from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularSwaggerView,
    SpectacularRedocView,
)

urlpatterns = [
    # ── Admin ──
    path("admin/", admin.site.urls),

    # ── Application principale (cockpit) ──
    path("", include("apps.studio.urls", namespace="studio")),

    # ── API REST ──
    path("api/", include("apps.api.urls", namespace="api")),

    # ── Documentation OpenAPI ──
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="swagger-ui"),
    path("api/redoc/", SpectacularRedocView.as_view(url_name="schema"), name="redoc"),

    # ── Authentification ──
    path("accounts/", include("django.contrib.auth.urls")),
]

# ── Fichiers médias en développement uniquement ──
# En production, Nginx sert directement /media/ et /outputs/
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static("/outputs/", document_root=settings.OUTPUTS_ROOT)
