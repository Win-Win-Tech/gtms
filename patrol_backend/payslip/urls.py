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


router = DefaultRouter()
router.register(r"payroll-profiles", EmployeePayrollProfileViewSet, basename="payroll-profile")
router.register(r"templates", PayslipTemplateViewSet, basename="payslip-template")
router.register(r"field-configs", PayslipFieldConfigViewSet, basename="payslip-field-config")
router.register(r"fields", PayslipFieldViewSet, basename="payslip-field")
router.register(r"records", PayslipRecordViewSet, basename="payslip-record")
router.register(r"advances", PayrollAdvanceViewSet, basename="payroll-advance")
router.register(r"quick-pay-disbursements", QuickPayDisbursementViewSet, basename="quick-pay-disbursement")

urlpatterns = [
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

