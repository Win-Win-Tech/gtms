import logging

from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.models import Role
from authapp.site_access import assert_caller_can_access_site, get_site_or_error, is_org_admin

from .models import SiteAlertRecipientConfig, TrackingAlertRecipient
from .serializers import (
    SiteAlertRecipientConfigBulkSerializer,
    SiteAlertRecipientConfigSerializer,
    TrackingAlertInboxSerializer,
)

logger = logging.getLogger(__name__)


def _assert_can_manage_site_alert_config(user, site):
    if user.is_superuser:
        return
    if not is_org_admin(user):
        raise PermissionDenied("Only organisation admins can manage alert recipient settings.")
    if not user.location_id or str(site.location_id) != str(user.location_id):
        raise PermissionDenied("Site does not belong to your organisation.")


class TrackingAlertInboxView(APIView):
    """GET /livetracking/alerts/ — inbox for the authenticated user."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = (
            TrackingAlertRecipient.objects.filter(
                user=request.user,
                channel=TrackingAlertRecipient.Channel.IN_APP,
            )
            .select_related(
                "alert",
                "alert__site",
                "alert__subject_user",
                "alert__location",
            )
            .order_by("-created_at")
        )

        unread_only = (request.query_params.get("unread") or "").lower() in ("1", "true", "yes")
        if unread_only:
            qs = qs.filter(read_at__isnull=True)

        alert_type = (request.query_params.get("alert_type") or "").strip()
        if alert_type:
            qs = qs.filter(alert__alert_type=alert_type)

        site_id = (request.query_params.get("site_id") or "").strip()
        if site_id:
            qs = qs.filter(alert__site_id=site_id)

        is_active = (request.query_params.get("is_active") or "").lower()
        if is_active in ("1", "true", "yes"):
            qs = qs.filter(alert__is_active=True)
        elif is_active in ("0", "false", "no"):
            qs = qs.filter(alert__is_active=False)

        try:
            limit = min(max(int(request.query_params.get("limit") or 50), 1), 200)
        except ValueError:
            limit = 50
        try:
            offset = max(int(request.query_params.get("offset") or 0), 0)
        except ValueError:
            offset = 0

        total = qs.count()
        rows = list(qs[offset : offset + limit])
        unread_count = TrackingAlertRecipient.objects.filter(
            user=request.user,
            channel=TrackingAlertRecipient.Channel.IN_APP,
            read_at__isnull=True,
        ).count()

        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "unread_count": unread_count,
                "results": TrackingAlertInboxSerializer(rows, many=True).data,
            }
        )


class TrackingAlertReadView(APIView):
    """POST /livetracking/alerts/<alert_id>/read/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, alert_id):
        try:
            row = TrackingAlertRecipient.objects.select_related(
                "alert",
                "alert__site",
                "alert__subject_user",
            ).get(
                alert_id=alert_id,
                user=request.user,
                channel=TrackingAlertRecipient.Channel.IN_APP,
            )
        except TrackingAlertRecipient.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)

        if row.read_at is None:
            row.read_at = timezone.now()
            row.save(update_fields=["read_at"])

        return Response(TrackingAlertInboxSerializer(row).data)


class TrackingAlertReadAllView(APIView):
    """POST /livetracking/alerts/read-all/"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        updated = TrackingAlertRecipient.objects.filter(
            user=request.user,
            channel=TrackingAlertRecipient.Channel.IN_APP,
            read_at__isnull=True,
        ).update(read_at=now)
        return Response({"marked_read": updated})


class SiteAlertRecipientConfigView(APIView):
    """
    GET  /livetracking/sites/<site_id>/alert-recipients/
    PUT  /livetracking/sites/<site_id>/alert-recipients/  — replace configs for site
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, site_id):
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(request.user, site)
        configs = SiteAlertRecipientConfig.objects.filter(site=site).select_related(
            "subject_role",
            "recipient_role",
        )
        return Response(SiteAlertRecipientConfigSerializer(configs, many=True).data)

    def put(self, request, site_id):
        site = get_site_or_error(site_id)
        _assert_can_manage_site_alert_config(request.user, site)

        serializer = SiteAlertRecipientConfigBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["configs"]

        role_ids = set()
        for item in items:
            role_ids.add(item["subject_role"].id)
            role_ids.add(item["recipient_role"].id)

        valid_roles = Role.objects.filter(
            id__in=role_ids,
            location_id=site.location_id,
        )
        valid_role_ids = set(valid_roles.values_list("id", flat=True))
        invalid = [str(rid) for rid in role_ids if rid not in valid_role_ids]
        if invalid:
            raise ValidationError({"configs": f"Invalid role(s) for this organisation: {', '.join(invalid)}"})

        SiteAlertRecipientConfig.objects.filter(site=site).delete()
        created = []
        for item in items:
            if not item.get("notify_boundary_breach") and not item.get("notify_location_missing"):
                continue
            created.append(
                SiteAlertRecipientConfig.objects.create(
                    site=site,
                    subject_role=item["subject_role"],
                    recipient_role=item["recipient_role"],
                    notify_boundary_breach=item.get("notify_boundary_breach", False),
                    notify_location_missing=item.get("notify_location_missing", False),
                )
            )

        logger.info(
            "[ALERT_CONFIG] Site %s updated with %s subject→recipient configs by %s",
            site.id,
            len(created),
            request.user.email,
        )
        return Response(SiteAlertRecipientConfigSerializer(created, many=True).data)
