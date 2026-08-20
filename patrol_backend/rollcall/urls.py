from django.urls import path

from .views import (
    RollCallDashboardFilterView,
    RollCallEndSessionView,
    RollCallExcelExportView,
    RollCallPdfExportView,
    RollCallSessionListView,
    RollCallStartSessionView,
)
from .views_v5 import (
    RollCallDashboardFilterViewV5,
    RollCallEndSessionViewV5,
    RollCallExcelExportViewV5,
    RollCallPdfExportViewV5,
    RollCallSessionListViewV5,
    RollCallStartSessionViewV5,
)

urlpatterns = [
    path("sessions/", RollCallSessionListView.as_view(), name="rollcall-sessions-list"),
    path("sessions/export/", RollCallExcelExportView.as_view(), name="rollcall-sessions-export"),
    path(
        "sessions/export-pdf/",
        RollCallPdfExportView.as_view(),
        name="rollcall-sessions-export-pdf",
    ),
    path("sessions/start/", RollCallStartSessionView.as_view(), name="rollcall-sessions-start"),
    path(
        "sessions/<uuid:session_id>/end/",
        RollCallEndSessionView.as_view(),
        name="rollcall-sessions-end",
    ),
    path(
        "dashboard/filter/",
        RollCallDashboardFilterView.as_view(),
        name="rollcall-dashboard-filter",
    ),
    path("v5/sessions/", RollCallSessionListViewV5.as_view(), name="rollcall-sessions-list-v5"),
    path(
        "v5/sessions/export/",
        RollCallExcelExportViewV5.as_view(),
        name="rollcall-sessions-export-v5",
    ),
    path(
        "v5/sessions/export-pdf/",
        RollCallPdfExportViewV5.as_view(),
        name="rollcall-sessions-export-pdf-v5",
    ),
    path(
        "v5/sessions/start/",
        RollCallStartSessionViewV5.as_view(),
        name="rollcall-sessions-start-v5",
    ),
    path(
        "v5/sessions/<uuid:session_id>/end/",
        RollCallEndSessionViewV5.as_view(),
        name="rollcall-sessions-end-v5",
    ),
    path(
        "v5/dashboard/filter/",
        RollCallDashboardFilterViewV5.as_view(),
        name="rollcall-dashboard-filter-v5",
    ),
]
