from django.urls import path

from .views import (
    VisitorApproveView,
    VisitorCancelView,
    VisitorCheckInView,
    VisitorCheckOutView,
    VisitorEntryDetailView,
    VisitorEntryExportView,
    VisitorEntryListView,
    VisitorPassDownloadView,
    VisitorQrScanView,
    VisitorRescheduleView,
    VisitorRevertView,
    VisitorSearchView,
)
from .views_ai import VisitorAiExtractView

urlpatterns = [
    path("search/", VisitorSearchView.as_view(), name="visitor-search"),
    path("ai/extract/", VisitorAiExtractView.as_view(), name="visitor-ai-extract"),
    path("qr-scan/", VisitorQrScanView.as_view(), name="visitor-qr-scan"),
    path("entries/", VisitorEntryListView.as_view(), name="visitor-entries-list"),
    path("entries/export/", VisitorEntryExportView.as_view(), name="visitor-entries-export"),
    path("entries/checkin/", VisitorCheckInView.as_view(), name="visitor-entries-checkin"),
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
