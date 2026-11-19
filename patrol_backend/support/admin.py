from django.contrib import admin

from .models import (
    SupportTicket,
    TicketActivityLog,
    TicketAttachment,
    TicketCategory,
    TicketComment,
)


@admin.register(TicketCategory)
class TicketCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "is_active", "sort_order")


@admin.register(SupportTicket)
class SupportTicketAdmin(admin.ModelAdmin):
    list_display = (
        "ticket_number",
        "subject",
        "status",
        "priority",
        "created_by",
        "current_assignee",
        "created_on",
    )
    list_filter = ("status", "priority")
    search_fields = ("ticket_number", "subject")


admin.site.register(TicketComment)
admin.site.register(TicketAttachment)
admin.site.register(TicketActivityLog)
