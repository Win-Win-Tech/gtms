from rest_framework import serializers
from .models import AttendanceCheckin

class AttendanceCheckinSerializer(serializers.ModelSerializer):
    status = serializers.ReadOnlyField()

    class Meta:
        model = AttendanceCheckin
        fields = "__all__"

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
            "id", "guard_name", "checkin_time", "checkout_time", "status",
            "remarks", "od_remarks", "location_name", "shift_name", "assignment", "shift_time"
        ]

    def get_shift_time(self, obj):
        return f"{obj.shift.start_time}–{obj.shift.end_time}"

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
