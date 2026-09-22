from rest_framework import serializers

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

from .models import VehicleOverstayWhitelist


class VehicleOverstayWhitelistSerializer(serializers.ModelSerializer):
    location_id = serializers.UUIDField(source="location.id", read_only=True)
    created_by_name = serializers.SerializerMethodField()
    created_on = serializers.SerializerMethodField()

    class Meta:
        model = VehicleOverstayWhitelist
        fields = [
            "id",
            "location_id",
            "vehicle_number",
            "notes",
            "created_by",
            "created_by_name",
            "created_on",
        ]
        read_only_fields = fields

    def get_created_by_name(self, obj):
        return getattr(obj.created_by, "name", None) if obj.created_by else None

    def get_created_on(self, obj):
        if not obj.created_on:
            return None
        request = self.context.get("request")
        loc_id = str(obj.location_id) if obj.location_id else None
        user_tz = get_user_timezone_from_request(request, location_id=loc_id)
        local = to_user_timezone(obj.created_on, user_tz)
        return local.isoformat() if local else None
