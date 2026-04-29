"""apps/studio/urls.py"""
from django.urls import path
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.auth.views import LoginView
from .views import (
    JobUploadView, JobCheckDuplicateView, JobReuseView, JobDetailView,
    CockpitView, ProjectListAPIView, ProjectCreateView,
    TranscribeView, SynthesizeView, SetVoiceView, ExportView, ExportStatusView, BurnSubtitlesView,
    GenerateSubtitlesView, SubtitlesStatusView,
    SegmentListView, SegmentSaveView, SegmentSaveAllView, SegmentImportScriptView, SegmentAudioView,
    SegmentSetTrimView,
    dashboard_view, logout_view, ProjectCreateAPIView, ProjectDeleteAPIView, JobDeleteAPIView,
)

app_name = "studio"

urlpatterns = [
    # ── Dashboard ──
    path("",          dashboard_view, name="dashboard"),
    path("logout/",   logout_view,    name="logout"),

    # ── Cockpit ──
    path("cockpit/",               CockpitView.as_view(), name="cockpit"),
    path("cockpit/<uuid:job_id>/", CockpitView.as_view(), name="cockpit_job"),

    # ── Projets ──
    path("projects/new/",                      ProjectCreateView.as_view(),       name="project_create"),
    path("api/projects/",                      ProjectListAPIView.as_view(),      name="project_list_api"),
    path("api/projects/create/",               ProjectCreateAPIView.as_view(),    name="project_create_api"),
    path("api/projects/<uuid:project_id>/delete/", ProjectDeleteAPIView.as_view(), name="project_delete_api"),

    # ── Jobs ──
    path("api/jobs/",                           JobUploadView.as_view(),         name="job_upload"),
    path("api/jobs/check-duplicate/",           JobCheckDuplicateView.as_view(), name="job_check_duplicate"),
    path("api/jobs/reuse/",                     JobReuseView.as_view(),          name="job_reuse"),
    path("api/jobs/<uuid:job_id>/",             JobDetailView.as_view(),         name="job_detail"),
    path("api/jobs/<uuid:job_id>/delete/",      JobDeleteAPIView.as_view(),      name="job_delete_api"),
    path("api/jobs/<uuid:job_id>/transcribe/",  TranscribeView.as_view(),        name="job_transcribe"),
    path("api/jobs/<uuid:job_id>/synthesize/",  SynthesizeView.as_view(),        name="job_synthesize"),
    path("api/jobs/<uuid:job_id>/set-voice/",   SetVoiceView.as_view(),          name="job_set_voice"),
    path("api/jobs/<uuid:job_id>/export/",              ExportView.as_view(),        name="job_export"),
    path("api/jobs/<uuid:job_id>/export/status/",       ExportStatusView.as_view(),  name="job_export_status"),
    path("api/jobs/<uuid:job_id>/export/burn/",         BurnSubtitlesView.as_view(),       name="job_burn"),
    path("api/jobs/<uuid:job_id>/generate-subtitles/",  GenerateSubtitlesView.as_view(),    name="job_generate_subtitles"),
    path("api/jobs/<uuid:job_id>/subtitles/status/",    SubtitlesStatusView.as_view(),      name="job_subtitles_status"),
    path("api/jobs/<uuid:job_id>/segments/",                    SegmentListView.as_view(),         name="segment_list"),
    path("api/jobs/<uuid:job_id>/segments/save-all/",           SegmentSaveAllView.as_view(),      name="segment_save_all"),
    path("api/jobs/<uuid:job_id>/segments/import-script/",      SegmentImportScriptView.as_view(), name="segment_import_script"),
    path("api/jobs/<uuid:job_id>/segments/<int:seg_id>/save/",  SegmentSaveView.as_view(),         name="segment_save"),
    path("api/jobs/<uuid:job_id>/segments/<int:segment_idx>/set-trim/", SegmentSetTrimView.as_view(), name="segment_set_trim"),
    path("api/jobs/<uuid:job_id>/segments/<int:seg_id>/audio/", SegmentAudioView.as_view(),        name="segment_audio"),
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT) \
  + static("/outputs/", document_root=settings.OUTPUTS_ROOT)