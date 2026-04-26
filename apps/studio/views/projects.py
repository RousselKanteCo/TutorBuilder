"""
apps/studio/views/projects.py
"""

from django.views.generic import CreateView
from django.contrib.auth.mixins import LoginRequiredMixin
from django.urls import reverse_lazy
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from ..models import Project


class ProjectCreateView(LoginRequiredMixin, CreateView):
    model         = Project
    fields        = ["name", "description"]
    template_name = "studio/project_form.html"
    success_url   = reverse_lazy("studio:cockpit")

    def form_valid(self, form):
        form.instance.owner = self.request.user
        return super().form_valid(form)


class ProjectListAPIView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        projects = Project.objects.filter(
            owner=request.user
        ).order_by("-updated_at").values("id", "name")
        return Response(list(projects))