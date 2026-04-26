"""
apps/studio/views/jobs.py — Vues API Jobs.
"""

import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser

from ..models import Job, Project
from ..serializers import JobUploadSerializer, JobResponseSerializer

logger = logging.getLogger(__name__)


class JobUploadView(APIView):
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def post(self, request):
        serializer = JobUploadSerializer(
            data=request.data, context={"request": request}
        )
        if not serializer.is_valid():
            return Response({"errors": serializer.errors}, status=status.HTTP_400_BAD_REQUEST)
        try:
            job = serializer.save()
            logger.info(f"Job créé : {job.pk} — {job.display_name}")
            return Response(JobResponseSerializer(job).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Erreur création job : {e}", exc_info=True)
            return Response({"error": "Erreur lors de la création du job."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class JobCheckDuplicateView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        filename = request.data.get('filename', '')
        size     = request.data.get('size', 0)
        duration = request.data.get('duration', None)

        if not filename or not size:
            return Response({"duplicate": False})

        try:
            size = int(size)
        except (ValueError, TypeError):
            return Response({"duplicate": False})

        jobs = Job.objects.filter(
            project__owner=request.user,
            video_filename=filename,
        ).exclude(status=Job.Status.ERROR)

        for job in jobs:
            try:
                existing_size = job.video_file.size
            except Exception:
                continue

            if existing_size != size:
                continue

            if duration is not None and job.video_duration_ms is not None:
                try:
                    if abs(round(job.video_duration_ms / 1000) - int(duration)) > 1:
                        continue
                except (ValueError, TypeError):
                    pass

            logger.info(f"Doublon détecté : job={job.pk}")
            return Response({
                "duplicate":      True,
                "job_id":         str(job.pk),
                "existing_title": job.display_name,
                "filename":       job.video_filename,
                "size":           existing_size,
                "duration":       round(job.video_duration_ms / 1000) if job.video_duration_ms else None,
            })

        return Response({"duplicate": False})


class JobReuseView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        source_job_id = request.data.get('source_job_id')
        project_id    = request.data.get('project_id')
        title         = request.data.get('title', '').strip()

        if not source_job_id or not project_id:
            return Response({"error": "source_job_id et project_id sont obligatoires."}, status=status.HTTP_400_BAD_REQUEST)

        try:
            source_job = Job.objects.get(pk=source_job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job source introuvable."}, status=status.HTTP_404_NOT_FOUND)

        try:
            project = Project.objects.get(pk=project_id, owner=request.user)
        except Project.DoesNotExist:
            return Response({"error": "Projet introuvable."}, status=status.HTTP_404_NOT_FOUND)

        try:
            new_job = Job.objects.create(
                project           = project,
                title             = title or source_job.display_name,
                video_file        = source_job.video_file.name,
                video_filename    = source_job.video_filename,
                video_duration_ms = source_job.video_duration_ms,
                status            = Job.Status.PENDING,
            )
            logger.info(f"Job réutilisé : nouveau={new_job.pk} source={source_job.pk}")
            return Response(JobResponseSerializer(new_job).data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"Erreur reuse : {e}", exc_info=True)
            return Response({"error": "Erreur lors de la réutilisation."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class JobDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, job_id):
        try:
            job = Job.objects.get(pk=job_id, project__owner=request.user)
        except Job.DoesNotExist:
            return Response({"error": "Job introuvable."}, status=status.HTTP_404_NOT_FOUND)

        video_url = ''
        if job.video_file:
            try:
                video_url = request.build_absolute_uri(job.video_file.url)
            except Exception:
                pass

        return Response({
            "id":             str(job.pk),
            "title":          job.title,
            "display_name":   job.display_name,
            "video_filename": job.video_filename,
            "video_url":      video_url,
            "status":         job.status,
            "status_label":   job.get_status_display(),
            "created_at":     job.created_at.isoformat(),
            "project": {
                "id":   str(job.project.pk),
                "name": job.project.name,
            },
        })