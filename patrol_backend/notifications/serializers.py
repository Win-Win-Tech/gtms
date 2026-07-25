from rest_framework import serializers

from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    to_user_timezone,
)

from .models import DeviceToken, NotificationLog


def _fmt_dt(dt, request, location_id=None):
    """UTC DB datetime → ISO string in user/location timezone (same as visitor ETA/ETO)."""
    if not dt:
        return None
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    local = to_user_timezone(dt, user_tz)
    return local.isoformat() if local else None


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = (
            "id",
            "token",
            "device_type",
            "device_id",
            "app_version",
            "is_active",
            "created_on",
            "modified_on",
        )
        read_only_fields = ("id", "is_active", "created_on", "modified_on")


class NotificationLogSerializer(serializers.ModelSerializer):
    is_read = serializers.BooleanField(read_only=True)
    created_on = serializers.SerializerMethodField()
    sent_at = serializers.SerializerMethodField()
    read_at = serializers.SerializerMethodField()

    class Meta:
        model = NotificationLog
        fields = (
            "id",
            "type",
            "title",
            "body",
            "data",
            "related_entry",
            "channel",
            "delivery_status",
            "sent_at",
            "read_at",
            "is_read",
            "created_on",
        )
        read_only_fields = fields

    def _location_id(self, obj):
        entry = getattr(obj, "related_entry", None)
        if entry and entry.location_id:
            return str(entry.location_id)
        return None

    def get_created_on(self, obj):
        return _fmt_dt(
            obj.created_on, self.context.get("request"), self._location_id(obj)
        )

    def get_sent_at(self, obj):
        return _fmt_dt(
            obj.sent_at, self.context.get("request"), self._location_id(obj)
        )

    def get_read_at(self, obj):
        return _fmt_dt(
            obj.read_at, self.context.get("request"), self._location_id(obj)
        )
