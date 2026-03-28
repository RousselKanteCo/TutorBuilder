"""
apps/studio/admin.py — Interface d'administration Django pour le studio.
"""

from django.contrib import admin
from django.utils.html import format_html
from .models import Project, Job, Segment


# ─────────────────────────────────────────
#  SEGMENT (inline dans Job)
# ─────────────────────────────────────────

class SegmentInline(admin.TabularInline):
    model = Segment
    fields = ("index", "start_timecode_display", "text", "text_translated", "audio_file")
    readonly_fields = ("index", "start_timecode_display")
    extra = 0
    ordering = ["index"]
    max_num = 200  # Limiter pour ne pas surcharger l'admin

    @admin.display(description="Timecode")
    def start_timecode_display(self, obj):
        return obj.start_timecode


# ─────────────────────────────────────────
#  JOB
# ─────────────────────────────────────────

@admin.register(Job)
class JobAdmin(admin.ModelAdmin):
    list_display = (
        "id_short", "project", "video_filename",
        "status_badge", "stt_engine", "tts_engine",
        "language", "segments_count", "created_at",
    )
    list_filter = ("status", "stt_engine", "tts_engine", "language")
    search_fields = ("id", "video_filename", "project__name")
    readonly_fields = (
        "id", "created_at", "updated_at", "completed_at",
        "celery_task_id", "waveform_data", "thumbnail_paths",
        "segments_count",
    )
    ordering = ["-created_at"]
    inlines = [SegmentInline]

    fieldsets = (
        ("Identifiants", {
            "fields": ("id", "project", "celery_task_id"),
        }),
        ("Fichier source", {
            "fields": ("video_file", "video_filename", "video_duration_ms"),
        }),
        ("Configuration", {
            "fields": ("stt_engine", "tts_engine", "tts_voice", "language"),
        }),
        ("État", {
            "fields": ("status", "error_message", "created_at", "updated_at", "completed_at"),
        }),
        ("Données dérivées", {
            "fields": ("waveform_data", "thumbnail_paths"),
            "classes": ("collapse",),
        }),
    )

    @admin.display(description="ID")
    def id_short(self, obj):
        return str(obj.pk)[:8] + "…"

    @admin.display(description="Statut")
    def status_badge(self, obj):
        colors = {
            "pending":      "#888",
            "uploading":    "#3B82F6",
            "extracting":   "#F59E0B",
            "transcribing": "#8B5CF6",
            "transcribed":  "#06B6D4",
            "synthesizing": "#EC4899",
            "done":         "#10B981",
            "error":        "#EF4444",
        }
        color = colors.get(obj.status, "#888")
        return format_html(
            '<span style="background:{};color:#fff;padding:2px 8px;'
            'border-radius:12px;font-size:11px">{}</span>',
            color, obj.get_status_display(),
        )


# ─────────────────────────────────────────
#  PROJECT
# ─────────────────────────────────────────

class JobInline(admin.TabularInline):
    model = Job
    fields = ("id", "status", "stt_engine", "tts_engine", "language", "created_at")
    readonly_fields = ("id", "status", "stt_engine", "tts_engine", "language", "created_at")
    extra = 0
    ordering = ["-created_at"]
    max_num = 20
    show_change_link = True


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "owner", "jobs_count", "created_at", "updated_at")
    list_filter = ("owner",)
    search_fields = ("name", "owner__username")
    readonly_fields = ("id", "created_at", "updated_at")
    inlines = [JobInline]

    @admin.display(description="Nb jobs")
    def jobs_count(self, obj):
        return obj.jobs.count()
