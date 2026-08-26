from datetime import time as dt_time

from rest_framework import serializers

from reports.constants import REPORT_CATALOG, REPORT_CATALOG_BY_CODE
from reports.models import LocationReportEmailConfig, LocationReportEmailItem, ReportEmailLog
from reports.services.email_sender import parse_recipients
from reports.services.schedule_utils import get_location_timezone, snap_send_time

VALID_SCHEDULES = {
    LocationReportEmailItem.SCHEDULE_DAILY,
    LocationReportEmailItem.SCHEDULE_WEEKLY_SUNDAY,
    LocationReportEmailItem.SCHEDULE_MONTHLY_START,
}


class LocationReportEmailItemSerializer(serializers.ModelSerializer):
    label = serializers.SerializerMethodField()
    supports_pdf = serializers.SerializerMethodField()
    supports_excel = serializers.SerializerMethodField()
    supports_site_wise = serializers.SerializerMethodField()
    schedule_types = serializers.ListField(
        child=serializers.ChoiceField(choices=list(VALID_SCHEDULES)),
        required=False,
        allow_empty=False,
    )

    class Meta:
        model = LocationReportEmailItem
        fields = (
            "id",
            "report_code",
            "label",
            "is_enabled",
            "schedule_type",
            "schedule_types",
            "daily_period",
            "send_pdf",
            "send_excel",
            "site_wise",
            "supports_pdf",
            "supports_excel",
            "supports_site_wise",
            "last_sent_schedule_key",
        )
        read_only_fields = ("id", "last_sent_schedule_key", "schedule_type")

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["schedule_types"] = instance.get_schedule_types()
        data["schedule_type"] = data["schedule_types"][0]
        return data

    def get_label(self, obj):
        meta = REPORT_CATALOG_BY_CODE.get(obj.report_code) or {}
        return meta.get("label") or obj.report_code

    def get_supports_pdf(self, obj):
        return bool((REPORT_CATALOG_BY_CODE.get(obj.report_code) or {}).get("supports_pdf"))

    def get_supports_excel(self, obj):
        return bool((REPORT_CATALOG_BY_CODE.get(obj.report_code) or {}).get("supports_excel"))

    def get_supports_site_wise(self, obj):
        return bool((REPORT_CATALOG_BY_CODE.get(obj.report_code) or {}).get("supports_site_wise"))

    def validate_schedule_types(self, value):
        cleaned = []
        for t in value or []:
            if t in VALID_SCHEDULES and t not in cleaned:
                cleaned.append(t)
        if not cleaned:
            raise serializers.ValidationError("Select at least one schedule type.")
        return cleaned

    def validate(self, attrs):
        send_pdf = attrs.get("send_pdf", getattr(self.instance, "send_pdf", False))
        send_excel = attrs.get("send_excel", getattr(self.instance, "send_excel", False))
        report_code = attrs.get("report_code", getattr(self.instance, "report_code", None))
        meta = REPORT_CATALOG_BY_CODE.get(report_code) or {}
        if send_pdf and not meta.get("supports_pdf"):
            send_pdf = False
            attrs["send_pdf"] = False
        if send_excel and not meta.get("supports_excel"):
            send_excel = False
            attrs["send_excel"] = False
        if attrs.get("is_enabled", getattr(self.instance, "is_enabled", False)):
            if not send_pdf and not send_excel:
                raise serializers.ValidationError(
                    "Enabled reports must have PDF and/or Excel selected."
                )
        # Back-compat: if only schedule_type sent, fold into schedule_types
        if "schedule_types" not in attrs and "schedule_type" in attrs and attrs["schedule_type"]:
            attrs["schedule_types"] = [attrs["schedule_type"]]
        return attrs


class LocationReportEmailConfigSerializer(serializers.ModelSerializer):
    items = LocationReportEmailItemSerializer(many=True, required=False)
    timezone = serializers.SerializerMethodField()
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = LocationReportEmailConfig
        fields = (
            "id",
            "location",
            "location_name",
            "is_enabled",
            "recipients",
            "send_time",
            "timezone",
            "items",
            "created_on",
            "modified_on",
        )
        read_only_fields = ("id", "created_on", "modified_on", "location_name", "timezone")

    def get_timezone(self, obj):
        try:
            return str(get_location_timezone(obj.location))
        except Exception:
            return "Asia/Kolkata"

    def validate_recipients(self, value):
        emails = parse_recipients(value or "")
        enabled = self.initial_data.get("is_enabled")
        if enabled is None and self.instance:
            enabled = self.instance.is_enabled
        if enabled and not emails:
            raise serializers.ValidationError(
                "At least one valid recipient email is required when enabled."
            )
        # Always persist as comma-space joined so multi-recipient is unambiguous
        return ", ".join(emails)

    def validate_send_time(self, value):
        if isinstance(value, str):
            parts = value.split(":")
            value = dt_time(int(parts[0]), int(parts[1]) if len(parts) > 1 else 0)
        return snap_send_time(value)

    def update(self, instance, validated_data):
        items_data = validated_data.pop("items", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            for item_data in items_data:
                report_code = item_data.get("report_code")
                if not report_code:
                    continue
                default_schedule = (REPORT_CATALOG_BY_CODE.get(report_code) or {}).get(
                    "default_schedule", LocationReportEmailItem.SCHEDULE_DAILY
                )
                item, _created = LocationReportEmailItem.objects.get_or_create(
                    config=instance,
                    report_code=report_code,
                    defaults={
                        "schedule_type": default_schedule,
                        "schedule_types": [default_schedule],
                        "daily_period": LocationReportEmailItem.PERIOD_PREVIOUS_DAY,
                        "send_pdf": (REPORT_CATALOG_BY_CODE.get(report_code) or {}).get(
                            "supports_pdf", False
                        ),
                        "send_excel": (REPORT_CATALOG_BY_CODE.get(report_code) or {}).get(
                            "supports_excel", True
                        ),
                    },
                )
                for field in (
                    "is_enabled",
                    "daily_period",
                    "send_pdf",
                    "send_excel",
                    "site_wise",
                ):
                    if field in item_data:
                        setattr(item, field, item_data[field])

                if "schedule_types" in item_data:
                    item.set_schedule_types(item_data["schedule_types"])
                elif "schedule_type" in item_data:
                    item.set_schedule_types([item_data["schedule_type"]])

                meta = REPORT_CATALOG_BY_CODE.get(report_code) or {}
                if item.send_pdf and not meta.get("supports_pdf"):
                    item.send_pdf = False
                if not meta.get("supports_site_wise"):
                    item.site_wise = False
                item.save()

        return instance


class ReportEmailLogSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    site_name = serializers.CharField(source="site.name", read_only=True, allow_null=True)

    class Meta:
        model = ReportEmailLog
        fields = (
            "id",
            "location",
            "location_name",
            "report_code",
            "site",
            "site_name",
            "schedule_key",
            "period_label",
            "formats",
            "row_count",
            "status",
            "error",
            "sent_at",
        )


def ensure_default_items(config: LocationReportEmailConfig):
    """Create one LocationReportEmailItem per catalog report if missing."""
    existing = set(config.items.values_list("report_code", flat=True))
    to_create = []
    for meta in REPORT_CATALOG:
        code = meta["code"]
        if code in existing:
            continue
        default_schedule = meta.get("default_schedule") or LocationReportEmailItem.SCHEDULE_DAILY
        to_create.append(
            LocationReportEmailItem(
                config=config,
                report_code=code,
                is_enabled=False,
                schedule_type=default_schedule,
                schedule_types=[default_schedule],
                daily_period=LocationReportEmailItem.PERIOD_PREVIOUS_DAY,
                send_pdf=bool(meta.get("supports_pdf")),
                send_excel=bool(meta.get("supports_excel")),
                site_wise=False,
            )
        )
    if to_create:
        LocationReportEmailItem.objects.bulk_create(to_create)
