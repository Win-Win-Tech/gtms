from rest_framework import serializers

from .models import (
    SiteAlertRecipientConfig,
    TrackingAlert,
    TrackingAlertRecipient,
    UserLiveLocation,
)


class SiteAlertRecipientConfigSerializer(serializers.ModelSerializer):
    role_name = serializers.CharField(source="role.name", read_only=True)

    class Meta:
        model = SiteAlertRecipientConfig
        fields = [
            "id",
            "site",
            "role",
            "role_name",
            "notify_boundary_breach",
            "notify_location_missing",
            "created_on",
            "modified_on",
        ]
        read_only_fields = ["created_on", "modified_on"]


class TrackingAlertRecipientSerializer(serializers.ModelSerializer):
    class Meta:
        model = TrackingAlertRecipient
        fields = [
            "id",
            "alert",
            "user",
            "channel",
            "read_at",
            "created_at",
        ]
        read_only_fields = fields


class TrackingAlertSerializer(serializers.ModelSerializer):
    subject_user_name = serializers.CharField(source="subject_user.name", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True)

    class Meta:
        model = TrackingAlert
        fields = [
            "id",
            "alert_type",
            "site",
            "site_name",
            "location",
            "subject_user",
            "subject_user_name",
            "subject_role",
            "attendance",
            "latitude",
            "longitude",
            "message",
            "is_active",
            "created_at",
            "resolved_at",
        ]
        read_only_fields = fields


class TrackingAlertInboxSerializer(serializers.ModelSerializer):
    """Inbox row for the authenticated recipient."""

    alert = TrackingAlertSerializer(read_only=True)

    class Meta:
        model = TrackingAlertRecipient
        fields = [
            "id",
            "read_at",
            "created_at",
            "alert",
        ]
        read_only_fields = fields


class SiteAlertRecipientConfigWriteSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteAlertRecipientConfig
        fields = [
            "role",
            "notify_boundary_breach",
            "notify_location_missing",
        ]


class SiteAlertRecipientConfigBulkSerializer(serializers.Serializer):
    configs = SiteAlertRecipientConfigWriteSerializer(many=True)

    def validate_configs(self, configs):
        if not configs:
            return configs
        role_ids = [item["role"].id for item in configs]
        if len(role_ids) != len(set(role_ids)):
            raise serializers.ValidationError("Duplicate role entries are not allowed.")
        return configs


class UserLiveLocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserLiveLocation
        fields = [
            "user",
            "location",
            "assigned_site",
            "latitude",
            "longitude",
            "is_inside_boundary",
            "boundary_state",
            "last_location_at",
            "active_breach_alert",
            "last_updated",
        ]
        read_only_fields = fields
