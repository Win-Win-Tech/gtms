from django.urls import path

from reports.views import (
    ReportEmailConfigView,
    ReportEmailLogListView,
    ReportEmailReportTypesView,
    ReportEmailTestSendView,
)

urlpatterns = [
    path("email-config/", ReportEmailConfigView.as_view(), name="report-email-config"),
    path(
        "email-config/report-types/",
        ReportEmailReportTypesView.as_view(),
        name="report-email-types",
    ),
    path("email-config/test-send/", ReportEmailTestSendView.as_view(), name="report-email-test"),
    path("email-logs/", ReportEmailLogListView.as_view(), name="report-email-logs"),
]
