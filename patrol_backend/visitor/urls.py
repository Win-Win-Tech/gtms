from django.urls import path

from .views import (
    VisitorApproveView,
    VisitorCancelView,
    VisitorCheckInView,
    VisitorCheckOutView,
    VisitorCompleteInviteView,
    VisitorEntryDetailView,
    VisitorEntryExportPdfView,
    VisitorEntryExportView,
    VisitorEntryListView,
    VisitorInviteCreateView,
    VisitorPassDownloadView,
    VisitorQrScanView,
    VisitorRescheduleView,
    VisitorRevertView,
    VisitorSearchView,
)
from .views_ai import VisitorAiExtractV2View, VisitorAiExtractView

urlpatterns = [
    path("search/", VisitorSearchView.as_view(), name="visitor-search"),
    path("ai/extract/", VisitorAiExtractView.as_view(), name="visitor-ai-extract"),
    path("ai/extract-v2/", VisitorAiExtractV2View.as_view(), name="visitor-ai-extract-v2"),
    path("qr-scan/", VisitorQrScanView.as_view(), name="visitor-qr-scan"),
    path("entries/", VisitorEntryListView.as_view(), name="visitor-entries-list"),
    path("entries/export/", VisitorEntryExportView.as_view(), name="visitor-entries-export"),
    path(
        "entries/export-pdf/",
        VisitorEntryExportPdfView.as_view(),
        name="visitor-entries-export-pdf",
    ),
    path("entries/checkin/", VisitorCheckInView.as_view(), name="visitor-entries-checkin"),
    path("entries/invite/", VisitorInviteCreateView.as_view(), name="visitor-entries-invite"),
    path(
        "entries/<uuid:entry_id>/complete-invite/",
        VisitorCompleteInviteView.as_view(),
        name="visitor-entries-complete-invite",
    ),
    path(
        "entries/<uuid:entry_id>/",
        VisitorEntryDetailView.as_view(),
        name="visitor-entries-detail",
    ),
    path(
        "entries/<uuid:entry_id>/approve/",
        VisitorApproveView.as_view(),
        name="visitor-entries-approve",
    ),
    path(
        "entries/<uuid:entry_id>/revert/",
        VisitorRevertView.as_view(),
        name="visitor-entries-revert",
    ),
    path(
        "entries/<uuid:entry_id>/cancel/",
        VisitorCancelView.as_view(),
        name="visitor-entries-cancel",
    ),
    path(
        "entries/<uuid:entry_id>/pass/",
        VisitorPassDownloadView.as_view(),
        name="visitor-entries-pass",
    ),
    path(
        "entries/<uuid:entry_id>/reschedule/",
        VisitorRescheduleView.as_view(),
        name="visitor-entries-reschedule",
    ),
    path(
        "entries/<uuid:entry_id>/checkout/",
        VisitorCheckOutView.as_view(),
        name="visitor-entries-checkout",
    ),
]
