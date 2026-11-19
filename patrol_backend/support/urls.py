from django.urls import path

from .views import (
    MyTicketListCreateView,
    SupportAgentListView,
    SupportDashboardView,
    SupportTicketExportExcelView,
    SupportTicketExportPdfView,
    SupportTicketQueueView,
    SupportTicketReportView,
    SupportUserDetailView,
    SupportUserListCreateView,
    TicketActionsView,
    TicketActivityListView,
    TicketAssignView,
    TicketAttachmentDownloadView,
    TicketAttachmentUploadView,
    TicketCategoryListView,
    TicketCloseView,
    TicketCommentCreateView,
    TicketCreateView,
    TicketDetailView,
    TicketInvalidateView,
    TicketPickupView,
    TicketReopenView,
    TicketResolveView,
    TicketTransferView,
)

urlpatterns = [
    path("categories/", TicketCategoryListView.as_view(), name="support-categories"),
    path("dashboard/", SupportDashboardView.as_view(), name="support-dashboard"),
    path("reports/", SupportTicketReportView.as_view(), name="support-ticket-report"),
    path(
        "reports/export-excel/",
        SupportTicketExportExcelView.as_view(),
        name="support-ticket-export-excel",
    ),
    path(
        "reports/export-pdf/",
        SupportTicketExportPdfView.as_view(),
        name="support-ticket-export-pdf",
    ),
    path(
        "support-users/",
        SupportUserListCreateView.as_view(),
        name="support-users-list-create",
    ),
    path(
        "support-users/<uuid:pk>/",
        SupportUserDetailView.as_view(),
        name="support-users-detail",
    ),
    path("tickets/", TicketCreateView.as_view(), name="support-ticket-create"),
    path("tickets/my/", MyTicketListCreateView.as_view(), name="support-tickets-my"),
    path("tickets/<uuid:pk>/", TicketDetailView.as_view(), name="support-ticket-detail"),
    path(
        "tickets/<uuid:pk>/actions/",
        TicketActionsView.as_view(),
        name="support-ticket-actions",
    ),
    path(
        "tickets/<uuid:pk>/comments/",
        TicketCommentCreateView.as_view(),
        name="support-ticket-comment",
    ),
    path(
        "tickets/<uuid:pk>/activity/",
        TicketActivityListView.as_view(),
        name="support-ticket-activity",
    ),
    path(
        "tickets/<uuid:pk>/attachments/",
        TicketAttachmentUploadView.as_view(),
        name="support-ticket-attachment-upload",
    ),
    path(
        "tickets/<uuid:pk>/attachments/<uuid:attachment_id>/download/",
        TicketAttachmentDownloadView.as_view(),
        name="support-ticket-attachment-download",
    ),
    path(
        "tickets/<uuid:pk>/pickup/",
        TicketPickupView.as_view(),
        name="support-ticket-pickup",
    ),
    path(
        "tickets/<uuid:pk>/assign/",
        TicketAssignView.as_view(),
        name="support-ticket-assign",
    ),
    path(
        "tickets/<uuid:pk>/transfer/",
        TicketTransferView.as_view(),
        name="support-ticket-transfer",
    ),
    path(
        "tickets/<uuid:pk>/invalidate/",
        TicketInvalidateView.as_view(),
        name="support-ticket-invalidate",
    ),
    path(
        "tickets/<uuid:pk>/resolve/",
        TicketResolveView.as_view(),
        name="support-ticket-resolve",
    ),
    path(
        "tickets/<uuid:pk>/close/",
        TicketCloseView.as_view(),
        name="support-ticket-close",
    ),
    path(
        "tickets/<uuid:pk>/reopen/",
        TicketReopenView.as_view(),
        name="support-ticket-reopen",
    ),
    path("support/tickets/", SupportTicketQueueView.as_view(), name="support-ticket-queue"),
    path("support/agents/", SupportAgentListView.as_view(), name="support-agents"),
]
