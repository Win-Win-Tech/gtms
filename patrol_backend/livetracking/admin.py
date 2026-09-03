from django.contrib import admin

from .models import (
    SiteAlertRecipientConfig,
    TrackingAlert,
    TrackingAlertRecipient,
    UserLiveLocation,
    UserLocationHistory,
)


@admin.register(TrackingAlert)
class TrackingAlertAdmin(admin.ModelAdmin):
    list_display = (
        "alert_type",
        "subject_user",
        "site",
        "is_active",
        "created_at",
    )
    list_filter = ("alert_type", "is_active", "location")
    search_fields = ("subject_user__email", "subject_user__name", "site__name")
    readonly_fields = ("created_at",)


@admin.register(TrackingAlertRecipient)
class TrackingAlertRecipientAdmin(admin.ModelAdmin):
    list_display = ("alert", "user", "channel", "read_at", "created_at")
    list_filter = ("channel", "read_at")
    search_fields = ("user__email", "alert__id")


@admin.register(SiteAlertRecipientConfig)
class SiteAlertRecipientConfigAdmin(admin.ModelAdmin):
    list_display = (
        "site",
        "subject_role",
        "recipient_role",
        "notify_boundary_breach",
        "notify_location_missing",
    )
    list_filter = ("notify_boundary_breach", "notify_location_missing")
    search_fields = ("site__name", "subject_role__name", "recipient_role__name")


@admin.register(UserLiveLocation)
class UserLiveLocationAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "location",
        "assigned_site",
        "boundary_state",
        "last_location_at",
        "last_updated",
    )
    list_filter = ("boundary_state",)


@admin.register(UserLocationHistory)
class UserLocationHistoryAdmin(admin.ModelAdmin):
    list_display = ("user", "location", "timestamp")
    list_filter = ("timestamp",)
