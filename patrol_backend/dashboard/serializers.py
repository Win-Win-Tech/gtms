from rest_framework import serializers
from .models import AttendanceCheckin
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

class AttendanceCheckinSerializer(serializers.ModelSerializer):
    status = serializers.ReadOnlyField()

    class Meta:
        model = AttendanceCheckin
        fields = "__all__"
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            user_tz = get_user_timezone_from_request(request)
            
            # Convert datetime fields to user timezone
            if instance.checkin_time:
                data['checkin_time'] = to_user_timezone(instance.checkin_time, user_tz).isoformat()
            
            if instance.checkout_time:
                data['checkout_time'] = to_user_timezone(instance.checkout_time, user_tz).isoformat()

            if instance.last_checkin_time:
                data['last_checkin_time'] = to_user_timezone(instance.last_checkin_time, user_tz).isoformat()

            if instance.last_checkout_time:
                data['last_checkout_time'] = to_user_timezone(instance.last_checkout_time, user_tz).isoformat()

            def _abs_media_url(file_field):
                if not file_field or not getattr(file_field, "name", None):
                    return None
                try:
                    u = file_field.url
                except Exception:
                    return None
                if request:
                    return request.build_absolute_uri(u)
                return u

            data['checkin_image'] = _abs_media_url(instance.checkin_image)
            data['checkout_image'] = _abs_media_url(instance.checkout_image)
            
            # Convert audit fields if needed
            if instance.created_on:
                data['created_on'] = to_user_timezone(instance.created_on, user_tz).isoformat()
            
            if instance.modified_on:
                data['modified_on'] = to_user_timezone(instance.modified_on, user_tz).isoformat()
        
        return data

        # serializers.py

# class AttendanceCheckinDashboardSerializer(serializers.ModelSerializer):
#     guard_name = serializers.CharField(source="guard.get_full_name", read_only=True)
#     shift_time = serializers.SerializerMethodField()

#     class Meta:
#         model = AttendanceCheckin
#         fields = [
#             "id", "guard_name", "checkin_time", "checkout_time", "status",
#             "remarks", "od_remarks", "org_location", "shift", "assignment", "shift_time"
#         ]

#     def get_shift_time(self, obj):
#         return f"{obj.shift.start_time}–{obj.shift.end_time}"

class AttendanceCheckinDashboardSerializer(serializers.ModelSerializer):
    guard_name = serializers.CharField(source="guard.name", read_only=True)
    shift_name = serializers.CharField(source="shift.name", read_only=True)
    location_name = serializers.CharField(source="org_location.name", read_only=True)
    shift_time = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceCheckin
        fields = [
            "id",
            "guard_name",
            "checkin_time",
            "checkout_time",
            "status",
            "duration_minutes",
            "pa_status",
            "remarks",
            "od_remarks",
            "location_name",
            "shift_name",
            "assignment",
            "shift_time",
        ]

    def get_shift_time(self, obj):
        return f"{obj.shift.start_time}–{obj.shift.end_time}"
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            user_tz = get_user_timezone_from_request(request)
            
            # Convert datetime fields to user timezone
            if instance.checkin_time:
                data['checkin_time'] = to_user_timezone(instance.checkin_time, user_tz).isoformat()
            
            if instance.checkout_time:
                data['checkout_time'] = to_user_timezone(instance.checkout_time, user_tz).isoformat()
        
        return data

class CheckInReportSerializer(serializers.Serializer):
    date = serializers.CharField()
    guard_id = serializers.UUIDField()
    guard_name = serializers.CharField()
    location_id = serializers.CharField(allow_null=True)
    location_name = serializers.CharField()
    shift_id = serializers.CharField(allow_null=True)
    shift_name = serializers.CharField()
    checkpoint_id = serializers.UUIDField()
    checkpoint_name = serializers.CharField()
    expected_time = serializers.DateTimeField()
    actual_checkin_time = serializers.DateTimeField(allow_null=True)
    status = serializers.CharField()
    delay_minutes = serializers.IntegerField(allow_null=True)
    checkin_id = serializers.UUIDField(allow_null=True, required=False)
    has_checklist = serializers.BooleanField(required=False)
    checklist_template_name = serializers.CharField(allow_null=True, required=False)
    checklist_remarks = serializers.CharField(allow_null=True, required=False)
    checklist_checked_count = serializers.IntegerField(allow_null=True, required=False)
    checklist_total_count = serializers.IntegerField(allow_null=True, required=False)
    checklist_answers = serializers.ListField(child=serializers.DictField(), allow_null=True, required=False)
