import json
from decimal import Decimal

from django.db.models import Q
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.models import User
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request

from .advance_services import (
    create_payroll_advance,
    delete_payroll_advance,
    get_advance_summary,
    update_payroll_advance,
)
from .models import EmployeePayrollProfile, PayrollAdvance
from .serializers import (
    PayrollAdvanceCreateSerializer,
    PayrollAdvanceSerializer,
    PayrollAdvanceUpdateSerializer,
)
from .services import gather_quick_pay_report_context, resolve_quick_pay_date_range
from .views import AdminOnlyMixin


def _resolve_location_id(request):
    location_id = request.query_params.get("location_id") or request.data.get("location_id")
    if not location_id:
        if getattr(request.user, "is_superuser", False):
            return None, "location_id is required"
        location_id = str(getattr(request.user, "location_id", "") or "")
    if not location_id:
        return None, "location_id is required"
    return location_id, None


class PayrollAdvanceViewSet(AdminOnlyMixin, viewsets.ModelViewSet):
    queryset = PayrollAdvance.objects.select_related("user", "location").prefetch_related("recoveries").all()
    serializer_class = PayrollAdvanceSerializer
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    def get_queryset(self):
        qs = super().get_queryset()
        location_id, err = _resolve_location_id(self.request)
        if err:
            return qs.none()
        qs = qs.filter(location_id=location_id)

        settlement_month = self.request.query_params.get("settlement_month")
        if settlement_month:
            qs = qs.filter(settlement_month=settlement_month)

        user_id = self.request.query_params.get("user_id")
        if user_id:
            qs = qs.filter(user_id=user_id)

        status_filter = self.request.query_params.get("status")
        if status_filter:
            qs = qs.filter(status=status_filter)

        return qs.order_by("-advance_date", "-created_on")

    def create(self, request, *args, **kwargs):
        serializer = PayrollAdvanceCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        location_id, err = _resolve_location_id(request)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if str(data["location_id"]) != str(location_id):
            return Response({"error": "location_id mismatch"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            employee = User.objects.get(id=data["user_id"], is_deleted=False)
        except User.DoesNotExist:
            return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        try:
            from scheduler.models import Location

            location = Location.objects.get(id=location_id, is_deleted=False)
            advance = create_payroll_advance(
                user=employee,
                location=location,
                amount=data["amount"],
                advance_date=data["advance_date"],
                settlement_month=data.get("settlement_month") or None,
                payment_mode=data.get("payment_mode") or "cash",
                reference_no=data.get("reference_no"),
                notes=data.get("notes"),
                acting_user=request.user,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(
            PayrollAdvanceSerializer(advance).data,
            status=status.HTTP_201_CREATED,
        )

    def _get_advance_for_request(self, pk):
        location_id, err = _resolve_location_id(self.request)
        if err:
            return None, Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        advance = self.get_queryset().filter(pk=pk, location_id=location_id).first()
        if not advance:
            return None, Response({"error": "Advance not found"}, status=status.HTTP_404_NOT_FOUND)
        return advance, None

    def partial_update(self, request, *args, **kwargs):
        advance, err_resp = self._get_advance_for_request(kwargs.get("pk"))
        if err_resp:
            return err_resp

        serializer = PayrollAdvanceUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data:
            return Response({"error": "No fields to update"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = update_payroll_advance(
                advance,
                amount=data.get("amount"),
                advance_date=data.get("advance_date"),
                payment_mode=data.get("payment_mode"),
                reference_no=data.get("reference_no"),
                notes=data.get("notes"),
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(PayrollAdvanceSerializer(updated).data)

    def destroy(self, request, *args, **kwargs):
        advance, err_resp = self._get_advance_for_request(kwargs.get("pk"))
        if err_resp:
            return err_resp

        try:
            delete_payroll_advance(advance)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["get"], url_path="summary")
    def summary(self, request):
        location_id, err = _resolve_location_id(request)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

        settlement_month = request.query_params.get("settlement_month")
        if not settlement_month:
            return Response({"error": "settlement_month is required"}, status=status.HTTP_400_BAD_REQUEST)

        profiles = EmployeePayrollProfile.objects.filter(
            location_id=location_id,
            is_active=True,
        ).select_related("user")

        search = (request.query_params.get("search") or "").strip()
        if search:
            profiles = profiles.filter(
                Q(user__name__icontains=search) | Q(user__employee_code__icontains=search)
            )

        rows = []
        for profile in profiles:
            user_id = profile.user_id
            summary = get_advance_summary(user_id, settlement_month)
            if (
                Decimal(summary["advance_given"]) <= 0
                and Decimal(summary["outstanding_advance"]) <= 0
                and Decimal(summary["quick_paid_total"]) <= 0
            ):
                continue
            rows.append(
                {
                    "user_id": str(user_id),
                    "employee_code": profile.user.employee_code,
                    "user_name": profile.user.name,
                    **summary,
                }
            )

        return Response({"results": rows, "settlement_month": settlement_month})


class QuickPayPreviewView(AdminOnlyMixin, APIView):
    """Preview Quick Pay rows with advance recovery suggestions (no export file)."""

    def get(self, request):
        try:
            params = request.query_params
            location_id, err = _resolve_location_id(request)
            if err:
                return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

            date_filter = params.get("date_filter", "today")
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            start_date, end_date = resolve_quick_pay_date_range(
                date_filter,
                params.get("start_date"),
                params.get("end_date"),
                user_tz,
            )

            user_ids_raw = params.get("user_ids")
            user_ids = None
            if user_ids_raw:
                user_ids = [part.strip() for part in str(user_ids_raw).split(",") if part.strip()]

            field_config_overrides = None
            field_config_map_raw = params.get("field_config_map")
            if field_config_map_raw:
                try:
                    parsed = json.loads(field_config_map_raw)
                except json.JSONDecodeError:
                    return Response({"error": "field_config_map must be valid JSON"}, status=status.HTTP_400_BAD_REQUEST)
                if isinstance(parsed, dict):
                    field_config_overrides = {
                        str(user_id): str(config_id)
                        for user_id, config_id in parsed.items()
                        if user_id and config_id
                    }

            context = gather_quick_pay_report_context(
                location_id=location_id,
                start_date=start_date,
                end_date=end_date,
                user_tz=user_tz,
                acting_user=request.user,
                search=params.get("search"),
                role=params.get("role"),
                date_filter=date_filter,
                user_ids=user_ids,
                field_config_overrides=field_config_overrides,
                advance_recovery_overrides=None,
            )

            def _serialize_row(row):
                return {
                    "user_id": row.get("user_id"),
                    "employee_code": row.get("employee_code"),
                    "name": row.get("name"),
                    "salary_type": row.get("salary_type"),
                    "gross_earnings": str(row.get("gross_earnings", row.get("gross_salary", "0"))),
                    "pf": str(row.get("pf", "0")),
                    "esi": str(row.get("esi", "0")),
                    "pt": str(row.get("pt", "0")),
                    "total_deductions": str(row.get("total_deductions", "0")),
                    "net_before_advance": str(row.get("net_before_advance", "0")),
                    "outstanding_advance": str(row.get("outstanding_advance", "0")),
                    "suggested_advance_recovery": str(row.get("suggested_advance_recovery", "0")),
                    "advance_recovery": str(row.get("advance_recovery", "0")),
                    "net_paid": str(row.get("net_paid", row.get("salary", "0"))),
                }

            return Response(
                {
                    "hourly_rows": [_serialize_row(r) for r in context.get("hourly_rows") or []],
                    "monthly_rows": [_serialize_row(r) for r in context.get("monthly_rows") or []],
                    "period_label": context.get("period_label"),
                    "settlement_month": context.get("settlement_month"),
                    "start_date": str(start_date),
                    "end_date": str(end_date),
                }
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as exc:
            return Response(
                {"error": f"Failed to preview quick pay report: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
