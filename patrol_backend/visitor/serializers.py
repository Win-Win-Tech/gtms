from rest_framework import serializers

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

from .models import Visitor, VisitorAsset, VisitorEntry


def _abs_media_url(file_field, request):
    if not file_field:
        return None
    try:
        url = file_field.url
    except ValueError:
        return None
    if request:
        return request.build_absolute_uri(url)
    return url


def _fmt_dt(dt, request, location_id=None):
    if not dt:
        return None
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    local = to_user_timezone(dt, user_tz)
    return local.isoformat() if local else None


class VisitorSerializer(serializers.ModelSerializer):
    location_id = serializers.UUIDField(source="location.id", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = Visitor
        fields = [
            "id",
            "location_id",
            "location_name",
            "ic_passport_number",
            "visitor_name",
            "phone_number",
            "created_on",
        ]
        read_only_fields = fields


class VisitorAssetSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()

    class Meta:
        model = VisitorAsset
        fields = ["id", "asset_type", "file_url", "created_on"]
        read_only_fields = fields

    def get_file_url(self, obj):
        return _abs_media_url(obj.file, self.context.get("request"))


class VisitorEntrySerializer(serializers.ModelSerializer):
    location_id = serializers.UUIDField(source="location.id", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    visitor_id = serializers.UUIDField(source="visitor.id", read_only=True)
    visitor_name = serializers.CharField(source="visitor.visitor_name", read_only=True)
    ic_passport_number = serializers.CharField(source="visitor.ic_passport_number", read_only=True)
    phone_number = serializers.CharField(source="visitor.phone_number", read_only=True)

    host_id = serializers.UUIDField(source="host.id", read_only=True, allow_null=True)
    host_name = serializers.SerializerMethodField()
    host_employee_code = serializers.SerializerMethodField()

    approved_by_id = serializers.UUIDField(source="approved_by.id", read_only=True, allow_null=True)
    approved_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    qr_image_url = serializers.SerializerMethodField()
    pass_image_url = serializers.SerializerMethodField()
    qr_usable = serializers.SerializerMethodField()
    assets = VisitorAssetSerializer(many=True, read_only=True)

    check_in_time = serializers.SerializerMethodField()
    check_out_time = serializers.SerializerMethodField()
    expected_arrival_time = serializers.SerializerMethodField()
    expected_out_time = serializers.SerializerMethodField()
    visit_date_time = serializers.SerializerMethodField()
    approved_on = serializers.SerializerMethodField()
    created_on = serializers.SerializerMethodField()

    class Meta:
        model = VisitorEntry
        fields = [
            "id",
            "status",
            "entry_source",
            "visitor_type",
            "purpose_of_visit",
            "vehicle_number",
            "remarks",
            "revert_reason",
            "location_id",
            "location_name",
            "visitor_id",
            "visitor_name",
            "ic_passport_number",
            "phone_number",
            "host_id",
            "host_name",
            "host_employee_code",
            "expected_arrival_time",
            "expected_out_time",
            "visit_date",
            "visit_date_time",
            "check_in_time",
            "check_out_time",
            "qr_token",
            "qr_image_url",
            "pass_image_url",
            "qr_expired",
            "qr_usable",
            "approved_by_id",
            "approved_by_name",
            "approved_on",
            "created_by",
            "created_by_name",
            "created_on",
            "assets",
        ]
        read_only_fields = fields

    def _loc_id(self, obj):
        return str(obj.location_id) if obj.location_id else None

    def get_host_name(self, obj):
        return getattr(obj.host, "name", None) if obj.host else None

    def get_host_employee_code(self, obj):
        return getattr(obj.host, "employee_code", None) if obj.host else None

    def get_approved_by_name(self, obj):
        return getattr(obj.approved_by, "name", None) if obj.approved_by else None

    def get_created_by_name(self, obj):
        return getattr(obj.created_by, "name", None) if obj.created_by else None

    def get_qr_image_url(self, obj):
        return _abs_media_url(obj.qr_image, self.context.get("request"))

    def get_pass_image_url(self, obj):
        return _abs_media_url(obj.pass_image, self.context.get("request"))

    def get_qr_usable(self, obj):
        return obj.is_qr_usable

    def get_check_in_time(self, obj):
        return _fmt_dt(obj.check_in_time, self.context.get("request"), self._loc_id(obj))

    def get_check_out_time(self, obj):
        return _fmt_dt(obj.check_out_time, self.context.get("request"), self._loc_id(obj))

    def get_expected_arrival_time(self, obj):
        return _fmt_dt(obj.expected_arrival_time, self.context.get("request"), self._loc_id(obj))

    def get_expected_out_time(self, obj):
        return _fmt_dt(obj.expected_out_time, self.context.get("request"), self._loc_id(obj))

    def get_visit_date_time(self, obj):
        return _fmt_dt(obj.visit_date_time, self.context.get("request"), self._loc_id(obj))

    def get_approved_on(self, obj):
        return _fmt_dt(obj.approved_on, self.context.get("request"), self._loc_id(obj))

    def get_created_on(self, obj):
        return _fmt_dt(obj.created_on, self.context.get("request"), self._loc_id(obj))
