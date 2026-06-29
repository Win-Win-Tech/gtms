from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from .models import QuickPayDisbursement
from .serializers import QuickPayDisbursementSerializer, QuickPayMarkPaidSerializer
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


class QuickPayDisbursementViewSet(AdminOnlyMixin, viewsets.ReadOnlyModelViewSet):
    queryset = QuickPayDisbursement.objects.select_related(
        "user", "location", "exported_by", "paid_marked_by"
    ).all()
    serializer_class = QuickPayDisbursementSerializer
    http_method_names = ["get", "post", "head", "options"]

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

        return qs.order_by("-exported_on")

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        try:
            disbursement = QuickPayDisbursement.objects.get(pk=pk)
        except QuickPayDisbursement.DoesNotExist:
            return Response({"error": "Quick pay disbursement not found"}, status=status.HTTP_404_NOT_FOUND)

        location_id, err = _resolve_location_id(request)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if str(disbursement.location_id) != str(location_id):
            return Response({"error": "Location mismatch"}, status=status.HTTP_400_BAD_REQUEST)

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
