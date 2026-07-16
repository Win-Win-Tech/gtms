from rest_framework import serializers

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

from .models import RollCallSession


class RollCallSessionSerializer(serializers.ModelSerializer):
    """Rich payload: shift details + start/end uploader guard details + absolute photo URLs."""

    shift_id = serializers.UUIDField(source="shift.id", read_only=True)
    shift_name = serializers.CharField(source="shift.name", read_only=True)
    shift_start_time = serializers.TimeField(source="shift.start_time", read_only=True)
    shift_end_time = serializers.TimeField(source="shift.end_time", read_only=True)
    shift_time = serializers.SerializerMethodField()

    location_id = serializers.UUIDField(source="location.id", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)

    started_by_name = serializers.SerializerMethodField()
    started_by_employee_code = serializers.SerializerMethodField()
    started_by_role = serializers.SerializerMethodField()
    ended_by_name = serializers.SerializerMethodField()
    ended_by_employee_code = serializers.SerializerMethodField()
    ended_by_role = serializers.SerializerMethodField()

    start_photo = serializers.SerializerMethodField()
    end_photo = serializers.SerializerMethodField()

    class Meta:
        model = RollCallSession
        fields = [
            "id",
            "status",
            "shift_date",
            "location_id",
            "location_name",
            "shift_id",
            "shift_name",
            "shift_start_time",
            "shift_end_time",
            "shift_time",
            "started_at",
            "ended_at",
            "start_photo",
            "end_photo",
            "started_by",
            "started_by_name",
            "started_by_employee_code",
            "started_by_role",
            "ended_by",
            "ended_by_name",
            "ended_by_employee_code",
            "ended_by_role",
        ]
        read_only_fields = fields

    def get_shift_time(self, obj):
        if not obj.shift:
            return ""
        return f"{obj.shift.start_time}–{obj.shift.end_time}"

    def _user_field(self, user, attr):
        if not user:
            return None
        return getattr(user, attr, None) or None

    def get_started_by_name(self, obj):
        return self._user_field(obj.started_by, "name")

    def get_started_by_employee_code(self, obj):
        return self._user_field(obj.started_by, "employee_code")

    def get_started_by_role(self, obj):
        return self._user_field(obj.started_by, "role")

    def get_ended_by_name(self, obj):
        return self._user_field(obj.ended_by, "name")

    def get_ended_by_employee_code(self, obj):
        return self._user_field(obj.ended_by, "employee_code")

    def get_ended_by_role(self, obj):
        return self._user_field(obj.ended_by, "role")

    def _abs_media_url(self, file_field):
        if not file_field:
            return None
        try:
            url = file_field.url
        except ValueError:
            return None
        request = self.context.get("request")
        if request:
            return request.build_absolute_uri(url)
        return url

    def get_start_photo(self, obj):
        return self._abs_media_url(obj.start_photo)

    def get_end_photo(self, obj):
        return self._abs_media_url(obj.end_photo)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get("request")
        if request:
            location_id = str(instance.location_id) if instance.location_id else None
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            if instance.started_at:
                data["started_at"] = to_user_timezone(instance.started_at, user_tz).isoformat()
            if instance.ended_at:
                data["ended_at"] = to_user_timezone(instance.ended_at, user_tz).isoformat()
        return data
