from rest_framework import serializers

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

from .models import SiteVehicleOverstayRecipient, VehicleOverstayWhitelist


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


class SiteVehicleOverstayRecipientSerializer(serializers.ModelSerializer):
    site_id = serializers.UUIDField(source="site.id", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)
    recipient_role_id = serializers.IntegerField(source="recipient_role.id", read_only=True)
    recipient_role_name = serializers.CharField(source="recipient_role.name", read_only=True)

    class Meta:
        model = SiteVehicleOverstayRecipient
        fields = [
            "id",
            "site_id",
            "site_name",
            "recipient_role_id",
            "recipient_role_name",
        ]
        read_only_fields = fields
