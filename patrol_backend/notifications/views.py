from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import DeviceToken, NotificationLog
from .serializers import DeviceTokenSerializer, NotificationLogSerializer


class DeviceTokenView(APIView):
    """
    POST /notifications/device-token/  — register / refresh token
    DELETE /notifications/device-token/ — deactivate token (logout)
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        token = (request.data.get("token") or "").strip()
        device_type = (request.data.get("device_type") or "").strip().lower()
        device_id = (request.data.get("device_id") or "").strip()
        app_version = (request.data.get("app_version") or "").strip()

        if not token:
            return Response({"error": "token is required"}, status=status.HTTP_400_BAD_REQUEST)
        if device_type not in (
            DeviceToken.DEVICE_ANDROID,
            DeviceToken.DEVICE_IOS,
            DeviceToken.DEVICE_WEB,
        ):
            return Response(
                {"error": "device_type must be android, ios, or web"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        obj, created = DeviceToken.objects.update_or_create(
            token=token,
            defaults={
                "user": request.user,
                "device_type": device_type,
                "device_id": device_id,
                "app_version": app_version,
                "is_active": True,
            },
        )
        # Same physical device id: keep latest token active
        if device_id:
            DeviceToken.objects.filter(
                user=request.user,
                device_id=device_id,
                is_active=True,
            ).exclude(id=obj.id).update(is_active=False)

        return Response(
            DeviceTokenSerializer(obj).data,
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    def delete(self, request):
        token = (request.data.get("token") or request.query_params.get("token") or "").strip()
        if not token:
            return Response({"error": "token is required"}, status=status.HTTP_400_BAD_REQUEST)
        updated = DeviceToken.objects.filter(token=token, user=request.user).update(
            is_active=False
        )
        return Response({"deactivated": updated > 0})


class NotificationListView(APIView):
    """GET /notifications/?unread=true&limit=50&offset=0"""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs = NotificationLog.objects.filter(user=request.user).select_related(
            "related_entry"
        )
        unread = (request.query_params.get("unread") or "").lower() in ("1", "true", "yes")
        if unread:
            qs = qs.filter(read_at__isnull=True)

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
        unread_count = NotificationLog.objects.filter(
            user=request.user, read_at__isnull=True
        ).count()
        return Response(
            {
                "count": total,
                "limit": limit,
                "offset": offset,
                "unread_count": unread_count,
                "results": NotificationLogSerializer(
                    rows, many=True, context={"request": request}
                ).data,
            }
        )


class NotificationReadView(APIView):
    """POST /notifications/<id>/read/"""

    permission_classes = [IsAuthenticated]

    def post(self, request, notification_id):
        try:
            row = (
                NotificationLog.objects.select_related("related_entry")
                .get(id=notification_id, user=request.user)
            )
        except NotificationLog.DoesNotExist:
            return Response({"error": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        if row.read_at is None:
            row.read_at = timezone.now()
            row.save(update_fields=["read_at"])
        return Response(
            NotificationLogSerializer(row, context={"request": request}).data
        )


class NotificationReadAllView(APIView):
    """POST /notifications/read-all/"""

    permission_classes = [IsAuthenticated]

    def post(self, request):
        now = timezone.now()
        updated = NotificationLog.objects.filter(
            user=request.user, read_at__isnull=True
        ).update(read_at=now)
        return Response({"marked_read": updated})
