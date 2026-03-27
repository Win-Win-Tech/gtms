from rest_framework import serializers
import pytz
from datetime import datetime, timedelta
from .models import AttendanceCheckin, CheckInLog
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    to_user_timezone,
    convert_date_range_to_utc,
)

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


class AttendanceCheckinDashboardV3Serializer(serializers.ModelSerializer):
    guard_name = serializers.CharField(source="guard.name", read_only=True)
    shift_name = serializers.CharField(source="shift.name", read_only=True)
    location_name = serializers.CharField(source="org_location.name", read_only=True)
    shift_time = serializers.SerializerMethodField()
    live_state = serializers.SerializerMethodField()
    log_pairs = serializers.SerializerMethodField()

    class Meta:
        model = AttendanceCheckin
        fields = [
            "id",
            "guard",
            "guard_name",
            "checkin_time",
            "checkout_time",
            "last_checkin_time",
            "last_checkout_time",
            "status",
            "duration_minutes",
            "checkin_count",
            "checkout_count",
            "live_state",
            "log_pairs",
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

    def get_live_state(self, obj):
        # Fallback to first checkin/checkout when denormalized last_* fields are stale.
        checkin_ref = obj.last_checkin_time or obj.checkin_time
        checkout_ref = obj.last_checkout_time or obj.checkout_time
        if checkin_ref and (not checkout_ref or checkin_ref > checkout_ref):
            return "checked_in"
        return "checked_out"

    def get_log_pairs(self, obj):
        request = self.context.get('request')
        if not request:
            return []
        user_tz = get_user_timezone_from_request(request)
        source_dt = obj.checkin_time or obj.created_on
        if not source_dt:
            return []

        shift_day = to_user_timezone(source_dt, user_tz).date()
        shift_start_local = user_tz.localize(datetime.combine(shift_day, obj.shift.start_time))
        if obj.shift.end_time <= obj.shift.start_time:
            shift_end_local = user_tz.localize(datetime.combine(shift_day + timedelta(days=1), obj.shift.end_time))
        else:
            shift_end_local = user_tz.localize(datetime.combine(shift_day, obj.shift.end_time))
        # Keep small grace so near-boundary events still appear in popup.
        search_start_utc = (shift_start_local - timedelta(minutes=30)).astimezone(pytz.UTC)
        search_end_utc = (shift_end_local + timedelta(minutes=30)).astimezone(pytz.UTC)

        logs = CheckInLog.objects.filter(
            guard=obj.guard,
            assignment=obj.assignment,
            shift=obj.shift,
            org_location=obj.org_location,
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
        ).order_by("timestamp")

        pairs = []
        open_checkin = None
        for log in logs:
            if log.type == "checkin":
                open_checkin = log.timestamp
                continue
            if log.type == "checkout" and open_checkin is not None and log.timestamp > open_checkin:
                duration_minutes = int((log.timestamp - open_checkin).total_seconds() // 60)
                pairs.append({
                    "checkin_time": to_user_timezone(open_checkin, user_tz).isoformat(),
                    "checkout_time": to_user_timezone(log.timestamp, user_tz).isoformat(),
                    "duration_minutes": duration_minutes,
                })
                open_checkin = None

        if open_checkin is not None:
            pairs.append({
                "checkin_time": to_user_timezone(open_checkin, user_tz).isoformat(),
                "checkout_time": None,
                "duration_minutes": None,
            })
        return pairs

    def to_representation(self, instance):
        data = super().to_representation(instance)
        request = self.context.get('request')
        # Keep count/state meaningful even when denormalized DB summary fields are stale.
        pairs = data.get("log_pairs") or []
        if pairs:
            checkins_from_pairs = len(pairs)
            checkouts_from_pairs = sum(1 for p in pairs if p.get("checkout_time"))
            if (data.get("checkin_count") in (None, 0)):
                data["checkin_count"] = checkins_from_pairs
            if (data.get("checkout_count") in (None, 0)):
                data["checkout_count"] = checkouts_from_pairs

            has_open_session = any(not p.get("checkout_time") for p in pairs)
            data["live_state"] = "checked_in" if has_open_session else "checked_out"

            if not data.get("last_checkin_time"):
                data["last_checkin_time"] = pairs[-1].get("checkin_time")
            if not data.get("last_checkout_time"):
                closed = [p for p in pairs if p.get("checkout_time")]
                if closed:
                    data["last_checkout_time"] = closed[-1].get("checkout_time")
        else:
            eff_checkin = data.get("last_checkin_time") or data.get("checkin_time")
            eff_checkout = data.get("last_checkout_time") or data.get("checkout_time")
            data["live_state"] = "checked_in" if (eff_checkin and (not eff_checkout or eff_checkin > eff_checkout)) else "checked_out"

        if request:
            user_tz = get_user_timezone_from_request(request)
            if instance.checkin_time:
                data['checkin_time'] = to_user_timezone(instance.checkin_time, user_tz).isoformat()
            if instance.checkout_time:
                data['checkout_time'] = to_user_timezone(instance.checkout_time, user_tz).isoformat()
            if instance.last_checkin_time:
                data['last_checkin_time'] = to_user_timezone(instance.last_checkin_time, user_tz).isoformat()
            if instance.last_checkout_time:
                data['last_checkout_time'] = to_user_timezone(instance.last_checkout_time, user_tz).isoformat()
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
