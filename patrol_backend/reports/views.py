from datetime import time

from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from reports.constants import REPORT_CATALOG
from reports.models import LocationReportEmailConfig, ReportEmailLog
from reports.serializers import (
    LocationReportEmailConfigSerializer,
    ReportEmailLogSerializer,
    ensure_default_items,
)
from scheduler.models import Location


def _resolve_location_for_user(request, location_id=None):
    user = request.user
    if getattr(user, "is_superuser", False):
        if not location_id:
            return None, "location_id is required for superadmin"
        loc = Location.objects.filter(id=location_id, is_deleted=False).first()
        if not loc:
            return None, "Location not found"
        return loc, None

    user_loc = getattr(user, "location", None)
    if not user_loc:
        return None, "User has no organisation"
    role = (getattr(user, "role", "") or "").lower()
    if role != "admin" and not getattr(user, "is_superuser", False):
        return None, "Only organisation admins can manage report email settings"
    if location_id and str(user_loc.id) != str(location_id):
        return None, "You can only manage your own organisation"
    return user_loc, None


def _get_or_create_config(location):
    config, created = LocationReportEmailConfig.objects.get_or_create(
        location=location,
        defaults={
            "is_enabled": False,
            "recipients": "",
            "send_time": time(8, 0),
        },
    )
    ensure_default_items(config)
    return config


class ReportEmailConfigView(APIView):
    """GET/PATCH /reports/email-config/?location_id="""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        location_id = request.query_params.get("location_id")
        location, err = _resolve_location_for_user(request, location_id)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        config = _get_or_create_config(location)
        return Response(LocationReportEmailConfigSerializer(config).data)

    def patch(self, request):
        location_id = request.query_params.get("location_id") or request.data.get("location")
        location, err = _resolve_location_for_user(request, location_id)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        config = _get_or_create_config(location)
        serializer = LocationReportEmailConfigSerializer(
            config, data=request.data, partial=True
        )
        serializer.is_valid(raise_exception=True)
        # Non-superadmin cannot change location FK
        if "location" in serializer.validated_data:
            serializer.validated_data["location"] = location
        serializer.save()
        config.refresh_from_db()
        ensure_default_items(config)
        return Response(LocationReportEmailConfigSerializer(config).data)


class ReportEmailReportTypesView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        return Response({"reports": REPORT_CATALOG})


class ReportEmailLogListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        location_id = request.query_params.get("location_id")
        location, err = _resolve_location_for_user(request, location_id)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        qs = ReportEmailLog.objects.filter(location=location).select_related("site", "location")[:100]
        return Response(ReportEmailLogSerializer(qs, many=True).data)


class ReportEmailTestSendView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        location_id = request.query_params.get("location_id") or request.data.get("location_id")
        location, err = _resolve_location_for_user(request, location_id)
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        config = _get_or_create_config(location)
        if not config.recipient_list():
            return Response(
                {"error": "Configure at least one recipient before test send"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        # Run synchronously for immediate feedback (may take a while)
        from reports.tasks import _process_org_config_force_all

        result = _process_org_config_force_all(config)
        return Response(result)
