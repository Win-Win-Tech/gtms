"""Payslip v5 APIs — site-wise employee scope. Live /payslip/ URLs unchanged."""

from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from authapp.models import User

from .advance_views import PayrollAdvanceViewSet, QuickPayPreviewView
from .quick_pay_views import QuickPayDisbursementViewSet
from .site_filter import (
    assert_employee_in_payslip_scope,
    filter_queryset_by_user_scope,
    get_scoped_user_ids,
    inject_scoped_user_ids_param,
    resolve_site_name,
)
from .views import (
    EmployeePayrollProfileViewSet,
    HourlyWageSummaryReportView,
    PayslipRecordViewSet,
)


def _v5_error(exc):
    if isinstance(exc, ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": detail}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, PermissionDenied):
        return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    raise exc


class PayslipV5ScopeMixin:
    def _filter_by_scope(self, queryset):
        try:
            return filter_queryset_by_user_scope(queryset, self.request)
        except (ValidationError, PermissionDenied):
            return queryset.none()

    def list(self, request, *args, **kwargs):
        try:
            get_scoped_user_ids(request)
            return super().list(request, *args, **kwargs)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

    def retrieve(self, request, *args, **kwargs):
        try:
            get_scoped_user_ids(request)
            return super().retrieve(request, *args, **kwargs)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class EmployeePayrollProfileViewSetV5(PayslipV5ScopeMixin, EmployeePayrollProfileViewSet):
    def get_queryset(self):
        return self._filter_by_scope(super().get_queryset())

    def perform_create(self, serializer):
        user = serializer.validated_data.get("user")
        if user:
            assert_employee_in_payslip_scope(self.request, user)
        super().perform_create(serializer)

    def perform_update(self, serializer):
        user = serializer.validated_data.get("user")
        if user:
            assert_employee_in_payslip_scope(self.request, user)
        super().perform_update(serializer)

    @action(detail=False, methods=["get"], url_path="employees")
    def employees(self, request):
        try:
            scoped = get_scoped_user_ids(request)
            scoped_ids = set(scoped) if scoped is not None else None
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        response = super().employees(request)
        if response.status_code != status.HTTP_200_OK:
            return response
        if scoped_ids is not None:
            filtered = [row for row in response.data if str(row.get("id")) in scoped_ids]
            return Response(filtered, status=status.HTTP_200_OK)
        return response


class PayslipRecordViewSetV5(PayslipV5ScopeMixin, PayslipRecordViewSet):
    def get_queryset(self):
        return self._filter_by_scope(super().get_queryset())

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        try:
            user_id = request.data.get("user_id")
            if user_id:
                employee = User.objects.get(id=user_id, is_deleted=False)
                assert_employee_in_payslip_scope(request, employee)
        except User.DoesNotExist:
            pass
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        return super().generate(request)

    @action(detail=False, methods=["post"], url_path="generate-bulk")
    def generate_bulk(self, request):
        try:
            scoped = get_scoped_user_ids(request)
            scoped_ids = set(scoped) if scoped is not None else None
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        if scoped_ids is not None:
            payload = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
            user_ids = payload.get("user_ids") or []
            payload["user_ids"] = [uid for uid in user_ids if str(uid) in scoped_ids]
            if not payload["user_ids"]:
                return Response(
                    {"error": "No employees in the selected site scope"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            request._full_data = payload
            request._data = payload
        return super().generate_bulk(request)


class PayrollAdvanceViewSetV5(PayslipV5ScopeMixin, PayrollAdvanceViewSet):
    def get_queryset(self):
        return self._filter_by_scope(super().get_queryset())

    def create(self, request, *args, **kwargs):
        try:
            user_id = request.data.get("user_id")
            if user_id:
                employee = User.objects.get(id=user_id, is_deleted=False)
                assert_employee_in_payslip_scope(request, employee)
        except User.DoesNotExist:
            pass
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        return super().create(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        try:
            scoped = get_scoped_user_ids(request)
            scoped_ids = set(scoped) if scoped is not None else None
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        response = super().summary(request)
        if response.status_code != status.HTTP_200_OK:
            return response
        if scoped_ids is not None:
            rows = [
                row
                for row in response.data.get("results", [])
                if str(row.get("user_id")) in scoped_ids
            ]
            payload = dict(response.data)
            payload["results"] = rows
            return Response(payload, status=status.HTTP_200_OK)
        return response


class QuickPayDisbursementViewSetV5(PayslipV5ScopeMixin, QuickPayDisbursementViewSet):
    def get_queryset(self):
        return self._filter_by_scope(super().get_queryset())

    @action(detail=False, methods=["post"], url_path="bulk-mark-paid")
    def bulk_mark_paid(self, request):
        try:
            get_scoped_user_ids(request)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        return super().bulk_mark_paid(request)


class QuickPayPreviewViewV5(QuickPayPreviewView):
    def get(self, request):
        try:
            inject_scoped_user_ids_param(request)
            response = super().get(request)
            if response.status_code != status.HTTP_200_OK:
                return response
            site_name = resolve_site_name(request)
            if site_name:
                payload = dict(response.data)
                payload["site_name"] = site_name
                return Response(payload)
            return response
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class HourlyWageSummaryReportViewV5(HourlyWageSummaryReportView):
    def _build_context(self, request):
        inject_scoped_user_ids_param(request)
        context, start_date, end_date = super()._build_context(request)
        site_name = resolve_site_name(request)
        if site_name:
            context = dict(context)
            context["site_name"] = site_name
        return context, start_date, end_date

    def get(self, request, export_format):
        try:
            return super().get(request, export_format)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
