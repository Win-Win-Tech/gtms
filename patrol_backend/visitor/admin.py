from django.contrib import admin

from .models import Visitor, VisitorAsset, VisitorEntry


@admin.register(Visitor)
class VisitorAdmin(admin.ModelAdmin):
    list_display = (
        "visitor_name",
        "ic_passport_number",
        "location",
        "phone_number",
        "is_deleted",
        "created_on",
    )
    list_filter = ("is_deleted", "location")
    search_fields = ("visitor_name", "ic_passport_number", "phone_number")


@admin.register(VisitorEntry)
class VisitorEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "visitor",
        "status",
        "visitor_type",
        "location",
        "host",
        "check_in_time",
        "check_out_time",
        "is_deleted",
    )
    list_filter = ("status", "visitor_type", "is_deleted", "location")
    search_fields = ("qr_token", "visitor__visitor_name", "visitor__ic_passport_number")


@admin.register(VisitorAsset)
class VisitorAssetAdmin(admin.ModelAdmin):
    list_display = ("id", "visitor_entry", "asset_type", "created_on")
    list_filter = ("asset_type",)
