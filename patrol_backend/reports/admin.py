from django.contrib import admin

from reports.models import (
    LocationReportEmailConfig,
    LocationReportEmailItem,
    ReportEmailLog,
)


class LocationReportEmailItemInline(admin.TabularInline):
    model = LocationReportEmailItem
    extra = 0


@admin.register(LocationReportEmailConfig)
class LocationReportEmailConfigAdmin(admin.ModelAdmin):
    list_display = ("location", "is_enabled", "send_time", "recipients")
    inlines = [LocationReportEmailItemInline]


@admin.register(ReportEmailLog)
class ReportEmailLogAdmin(admin.ModelAdmin):
    list_display = ("location", "report_code", "status", "schedule_key", "sent_at")
    list_filter = ("status", "report_code")
