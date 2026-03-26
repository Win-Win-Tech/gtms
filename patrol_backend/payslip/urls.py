from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    EmployeePayrollProfileViewSet,
    PayslipTemplateViewSet,
    PayslipFieldConfigViewSet,
    PayslipFieldViewSet,
    PayslipRecordViewSet,
)


router = DefaultRouter()
router.register(r"payroll-profiles", EmployeePayrollProfileViewSet, basename="payroll-profile")
router.register(r"templates", PayslipTemplateViewSet, basename="payslip-template")
router.register(r"field-configs", PayslipFieldConfigViewSet, basename="payslip-field-config")
router.register(r"fields", PayslipFieldViewSet, basename="payslip-field")
router.register(r"records", PayslipRecordViewSet, basename="payslip-record")

urlpatterns = [
    path("", include(router.urls)),
]

