from django.utils import timezone
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request

from .advance_services import delete_quick_pay_disbursement, update_quick_pay_disbursement
from .models import QuickPayDisbursement
from .serializers import (
    QuickPayBulkMarkPaidSerializer,
    QuickPayDisbursementSerializer,
    QuickPayDisbursementUpdateSerializer,
    QuickPayMarkPaidSerializer,
)
from .services import resolve_quick_pay_date_range
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


class QuickPayDisbursementViewSet(
    AdminOnlyMixin,
    mixins.ListModelMixin,
    mixins.RetrieveModelMixin,
    viewsets.GenericViewSet,
):
    queryset = QuickPayDisbursement.objects.select_related(
        "user", "location", "exported_by", "paid_marked_by"
    ).all()
    serializer_class = QuickPayDisbursementSerializer
    http_method_names = ["get", "patch", "delete", "post", "head", "options"]

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

        payment_status = self.request.query_params.get("payment_status")
        if payment_status:
            qs = qs.filter(payment_status=payment_status)

        date_filter = self.request.query_params.get("date_filter")
        if date_filter:
            try:
                user_tz = get_user_timezone_from_request(self.request, location_id=location_id)
                start_date, end_date = resolve_quick_pay_date_range(
                    date_filter,
                    self.request.query_params.get("start_date"),
                    self.request.query_params.get("end_date"),
                    user_tz,
                )
                qs = qs.filter(
                    exported_on__date__gte=start_date,
                    exported_on__date__lte=end_date,
                )
            except ValueError as exc:
                return qs.none()

        return qs.order_by("-exported_on")

    def _get_disbursement_for_request(self, pk):
        location_id, err = _resolve_location_id(self.request)
        if err:
            return None, Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        disbursement = self.get_queryset().filter(pk=pk).first()
        if not disbursement:
            return None, Response({"error": "Quick pay disbursement not found"}, status=status.HTTP_404_NOT_FOUND)
        return disbursement, None

    def partial_update(self, request, *args, **kwargs):
        disbursement, err_resp = self._get_disbursement_for_request(kwargs.get("pk"))
        if err_resp:
            return err_resp

        serializer = QuickPayDisbursementUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        if not data:
            return Response({"error": "No fields to update"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            updated = update_quick_pay_disbursement(
                disbursement,
                advance_recovery=data.get("advance_recovery"),
                paid_amount=data.get("paid_amount"),
                payment_mode=data.get("payment_mode"),
                payment_ref_no=data.get("payment_ref_no"),
                payment_notes=data.get("payment_notes"),
                acting_user=request.user,
            )
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(QuickPayDisbursementSerializer(updated).data)

    def destroy(self, request, *args, **kwargs):
        disbursement, err_resp = self._get_disbursement_for_request(kwargs.get("pk"))
        if err_resp:
            return err_resp

        try:
            delete_quick_pay_disbursement(disbursement)
        except ValueError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        disbursement, err_resp = self._get_disbursement_for_request(pk)
        if err_resp:
            return err_resp

        if disbursement.payment_status == "PAID":
            return Response({"error": "Already marked as paid"}, status=status.HTTP_400_BAD_REQUEST)

        serializer = QuickPayMarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        disbursement.payment_status = "PAID"
        disbursement.paid_amount = data["paid_amount"]
        disbursement.paid_on = data.get("paid_on") or timezone.now()
        disbursement.payment_mode = data.get("payment_mode") or "cash"
        disbursement.payment_ref_no = data.get("payment_ref_no") or ""
        disbursement.payment_notes = data.get("payment_notes") or ""
        disbursement.paid_marked_by = request.user
        disbursement.save()

        return Response(QuickPayDisbursementSerializer(disbursement).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-mark-paid")
    def bulk_mark_paid(self, request):
        location_id, err = _resolve_location_id(request)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

        serializer = QuickPayBulkMarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        ids = data["ids"]
        paid_amounts = data.get("paid_amounts") or {}

        marked = []
        skipped = []
        now = timezone.now()

        for disbursement_id in ids:
            disbursement = QuickPayDisbursement.objects.filter(
                pk=disbursement_id,
                location_id=location_id,
            ).first()
            if not disbursement:
                skipped.append({"id": str(disbursement_id), "reason": "Not found"})
                continue
            if disbursement.payment_status == "PAID":
                skipped.append({"id": str(disbursement_id), "reason": "Already paid"})
                continue

            paid_amount = paid_amounts.get(str(disbursement_id))
            if paid_amount is None:
                paid_amount = disbursement.net_paid

            disbursement.payment_status = "PAID"
            disbursement.paid_amount = paid_amount
            disbursement.paid_on = now
            disbursement.payment_mode = data.get("payment_mode") or "cash"
            disbursement.payment_ref_no = data.get("payment_ref_no") or ""
            disbursement.payment_notes = data.get("payment_notes") or ""
            disbursement.paid_marked_by = request.user
            disbursement.save()
            marked.append(str(disbursement_id))

        return Response(
            {
                "marked_count": len(marked),
                "skipped_count": len(skipped),
                "marked": marked,
                "skipped": skipped,
            },
            status=status.HTTP_200_OK,
        )
