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
    VehicleMovementReportExportPdfView,
    VehicleMovementReportExportView,
    VehicleMovementReportView,
)
from .views_ai import VisitorAiExtractV2View, VisitorAiExtractView
from .views_v5 import (
    VehicleMovementReportExportPdfViewV5,
    VehicleMovementReportExportViewV5,
    VehicleMovementReportViewV5,
    VisitorApproveViewV5,
    VisitorCancelViewV5,
    VisitorCheckInViewV5,
    VisitorCheckOutViewV5,
    VisitorCompleteInviteViewV5,
    VisitorEntryDetailViewV5,
    VisitorEntryExportPdfViewV5,
    VisitorEntryExportViewV5,
    VisitorEntryListViewV5,
    VisitorInviteCreateViewV5,
    VisitorPassDownloadViewV5,
    VisitorQrScanViewV5,
    VisitorRescheduleViewV5,
    VisitorRevertViewV5,
    VisitorSearchViewV5,
)

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
    path(
        "reports/vehicle-movement/",
        VehicleMovementReportView.as_view(),
        name="visitor-vehicle-movement-report",
    ),
    path(
        "reports/vehicle-movement/export/",
        VehicleMovementReportExportView.as_view(),
        name="visitor-vehicle-movement-export",
    ),
    path(
        "reports/vehicle-movement/export-pdf/",
        VehicleMovementReportExportPdfView.as_view(),
        name="visitor-vehicle-movement-export-pdf",
    ),
    # --- v5 (site-wise) ---
    path("v5/search/", VisitorSearchViewV5.as_view(), name="visitor-search-v5"),
    path("v5/qr-scan/", VisitorQrScanViewV5.as_view(), name="visitor-qr-scan-v5"),
    path("v5/entries/", VisitorEntryListViewV5.as_view(), name="visitor-entries-list-v5"),
    path(
        "v5/entries/export/",
        VisitorEntryExportViewV5.as_view(),
        name="visitor-entries-export-v5",
    ),
    path(
        "v5/entries/export-pdf/",
        VisitorEntryExportPdfViewV5.as_view(),
        name="visitor-entries-export-pdf-v5",
    ),
    path(
        "v5/entries/checkin/",
        VisitorCheckInViewV5.as_view(),
        name="visitor-entries-checkin-v5",
    ),
    path(
        "v5/entries/invite/",
        VisitorInviteCreateViewV5.as_view(),
        name="visitor-entries-invite-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/complete-invite/",
        VisitorCompleteInviteViewV5.as_view(),
        name="visitor-entries-complete-invite-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/",
        VisitorEntryDetailViewV5.as_view(),
        name="visitor-entries-detail-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/approve/",
        VisitorApproveViewV5.as_view(),
        name="visitor-entries-approve-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/revert/",
        VisitorRevertViewV5.as_view(),
        name="visitor-entries-revert-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/cancel/",
        VisitorCancelViewV5.as_view(),
        name="visitor-entries-cancel-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/pass/",
        VisitorPassDownloadViewV5.as_view(),
        name="visitor-entries-pass-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/reschedule/",
        VisitorRescheduleViewV5.as_view(),
        name="visitor-entries-reschedule-v5",
    ),
    path(
        "v5/entries/<uuid:entry_id>/checkout/",
        VisitorCheckOutViewV5.as_view(),
        name="visitor-entries-checkout-v5",
    ),
    path(
        "v5/reports/vehicle-movement/",
        VehicleMovementReportViewV5.as_view(),
        name="visitor-vehicle-movement-report-v5",
    ),
    path(
        "v5/reports/vehicle-movement/export/",
        VehicleMovementReportExportViewV5.as_view(),
        name="visitor-vehicle-movement-export-v5",
    ),
    path(
        "v5/reports/vehicle-movement/export-pdf/",
        VehicleMovementReportExportPdfViewV5.as_view(),
        name="visitor-vehicle-movement-export-pdf-v5",
    ),
]
