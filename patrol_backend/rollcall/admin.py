from django.contrib import admin

from .models import RollCallSession


@admin.register(RollCallSession)
class RollCallSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "location",
        "shift",
        "shift_date",
        "status",
        "started_by",
        "ended_by",
        "started_at",
        "ended_at",
        "is_deleted",
    )
    list_filter = ("status", "shift_date", "is_deleted")
    search_fields = ("id", "started_by__name", "ended_by__name")
    raw_id_fields = ("location", "shift", "started_by", "ended_by", "deleted_by")
