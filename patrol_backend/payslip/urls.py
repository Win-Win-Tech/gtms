from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EmployeePayrollProfileViewSet,
    HourlyWageSummaryReportView,
    PayslipTemplateViewSet,
    PayslipFieldConfigViewSet,
    PayslipFieldViewSet,
    PayslipRecordViewSet,
)
from .advance_views import PayrollAdvanceViewSet, QuickPayPreviewView
from .quick_pay_views import QuickPayDisbursementViewSet
from .views_v5 import (
    EmployeePayrollProfileViewSetV5,
    HourlyWageSummaryReportViewV5,
    PayrollAdvanceViewSetV5,
    PayslipRecordViewSetV5,
    QuickPayDisbursementViewSetV5,
    QuickPayPreviewViewV5,
)


router = DefaultRouter()
router.register(r"payroll-profiles", EmployeePayrollProfileViewSet, basename="payroll-profile")
router.register(r"templates", PayslipTemplateViewSet, basename="payslip-template")
router.register(r"field-configs", PayslipFieldConfigViewSet, basename="payslip-field-config")
router.register(r"fields", PayslipFieldViewSet, basename="payslip-field")
router.register(r"records", PayslipRecordViewSet, basename="payslip-record")
router.register(r"advances", PayrollAdvanceViewSet, basename="payroll-advance")
router.register(r"quick-pay-disbursements", QuickPayDisbursementViewSet, basename="quick-pay-disbursement")

v5_router = DefaultRouter()
v5_router.register(r"payroll-profiles", EmployeePayrollProfileViewSetV5, basename="payroll-profile-v5")
v5_router.register(r"records", PayslipRecordViewSetV5, basename="payslip-record-v5")
v5_router.register(r"advances", PayrollAdvanceViewSetV5, basename="payroll-advance-v5")
v5_router.register(
    r"quick-pay-disbursements",
    QuickPayDisbursementViewSetV5,
    basename="quick-pay-disbursement-v5",
)

urlpatterns = [
    path(
        "v5/reports/quick-pay-preview/",
        QuickPayPreviewViewV5.as_view(),
        name="quick-pay-preview-v5",
    ),
    path(
        "v5/reports/hourly-wage-summary/export-excel/",
        HourlyWageSummaryReportViewV5.as_view(),
        {"export_format": "excel"},
        name="hourly-wage-summary-export-excel-v5",
    ),
    path(
        "v5/reports/hourly-wage-summary/export-pdf/",
        HourlyWageSummaryReportViewV5.as_view(),
        {"export_format": "pdf"},
        name="hourly-wage-summary-export-pdf-v5",
    ),
    path("v5/", include(v5_router.urls)),
    path(
        "reports/quick-pay-preview/",
        QuickPayPreviewView.as_view(),
        name="quick-pay-preview",
    ),
    path(
        "reports/hourly-wage-summary/export-excel/",
        HourlyWageSummaryReportView.as_view(),
        {"export_format": "excel"},
        name="hourly-wage-summary-export-excel",
    ),
    path(
        "reports/hourly-wage-summary/export-pdf/",
        HourlyWageSummaryReportView.as_view(),
        {"export_format": "pdf"},
        name="hourly-wage-summary-export-pdf",
    ),
    path("", include(router.urls)),
]

