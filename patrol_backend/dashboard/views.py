# Standard library imports
import csv
import json
import logging
import os
import uuid
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

from django.core.files.base import ContentFile

# Third-party imports
import pytz
from geopy.distance import geodesic
from openpyxl import Workbook, load_workbook

# Django imports
from django.conf import settings
from django.core.cache import cache
from django.core.serializers import serialize
from django.db import transaction
from django.db.models import Avg, Count, F, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import (
    get_current_timezone,
    is_aware,
    is_naive,
    localtime,
    make_aware,
    now,
)

# Django REST Framework imports
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action, parser_classes
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

# Local app imports
from authapp.models import User
from checkin.models import CheckIn, CheckInChecklistAnswer
from scheduler.models import ChecklistTemplate
from scheduler.models import Assignment, Checkpoint, Location, Shift, CheckpointTemplate, SiteSetting, LocationSite
from tourlog.models import TourLog
from patrol_backend.utils.proximity_utils import get_site_within_proximity
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    get_user_now,
    to_user_timezone,
    convert_date_range_to_utc,
    combine_date_time_in_user_tz
)

from .models import AttendanceCheckin, AttendanceWeekOff, CheckInLog
from .serializers import (
    AttendanceBoundaryEditSerializer,
    AttendanceCheckinDashboardSerializer,
    AttendanceCheckinDashboardV3Serializer,
    AttendanceCheckinDashboardV4Serializer,
    AttendanceCheckinSerializer,
    CheckInReportSerializer,
)

# Setup logger
logger = logging.getLogger(__name__)


def _get_site_setting_int(key, location_id=None, default_value=None):
    """
    Read integer values from SiteSetting (stored as strings).
    Returns default_value when missing/invalid.
    """
    try:
        raw = SiteSetting.get_setting(
            key=key,
            location_id=location_id,
            default_value=default_value,
        )
        if raw is None:
            return default_value
        return int(raw)
    except Exception:
        return default_value

class GuardPerformanceView(APIView):
    def get(self, request):
        data = []
        for guard in User.objects.all():
            logs = TourLog.objects.filter(guard=guard)
            completed = logs.filter(completed=True).count()
            total = logs.count()
            avg_duration = logs.aggregate(avg=Avg('end_time'))['avg']
            data.append({
#               "guard": guard.username,
                "guard": guard.name if hasattr(guard, "name") else guard.email,
                "completed_tours": completed,
                "total_tours": total,
                "completion_rate": round((completed / total) * 100, 2) if total else 0,
                "avg_duration": avg_duration
            })
        return Response(data)

class TourStatsView(APIView):
    def get(self, request):
        from django.utils.timezone import now
        today = now().date()
        logs_today = TourLog.objects.filter(start_time__date=today)
        completed = logs_today.filter(completed=True).count()
        total = logs_today.count()
        return Response({
            "date": str(today),
            "completed": completed,
            "total": total,
            "completion_rate": round((completed / total) * 100, 2) if total else 0
        })


class ExportTourLogsCSV(APIView):
    def get(self, request):
        logs = TourLog.objects.select_related('guard', 'shift').all()
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="tour_logs.csv"'
        writer = csv.writer(response)
        writer.writerow(['Guard', 'Shift', 'Start', 'End', 'Completed'])
        for log in logs:
            writer.writerow([
                log.guard.username,
                log.shift.name,
                log.start_time,
                log.end_time,
                log.completed
            ])
        return response


class PatrolRouteView(APIView):
    def get(self, request, guard_id):
        checkins = CheckIn.objects.filter(guard_id=guard_id).order_by('timestamp')
        route = [
            {"lat": c.latitude, "lng": c.longitude, "time": c.timestamp}
            for c in checkins
        ]
        return Response(route)


def _attendance_v3_compute_from_logs(user, assignment, shift, org_location, search_start_utc, search_end_utc):
    """
    Derive last check-in/checkout and total worked minutes from CheckInLog within the v2 search window.
    Pairs checkin→checkout in order; duplicate checkin before checkout replaces open checkin.
    Open checkin adds provisional time up to min(now, window end).
    """
    logs = list(
        CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
        ).order_by("timestamp")
    )

    if not logs:
        return {
            "last_checkin_time": None,
            "last_checkout_time": None,
            "duration_minutes": None,
            "checkin_count": 0,
            "checkout_count": 0,
            "live_state": "not_checked_in",
        }

    last_checkin_time = None
    last_checkout_time = None
    for log in logs:
        if log.type == "checkin":
            last_checkin_time = log.timestamp
        elif log.type == "checkout":
            last_checkout_time = log.timestamp

    checkin_count = sum(1 for log in logs if log.type == "checkin")
    checkout_count = sum(1 for log in logs if log.type == "checkout")

    open_ts = None
    total_seconds = 0.0
    now_utc = timezone.now()
    # search_end_utc is exclusive in queries; cap inside window
    window_cap = search_end_utc - timedelta(microseconds=1)

    for log in logs:
        if log.type == "checkin":
            open_ts = log.timestamp
        elif log.type == "checkout":
            if open_ts is not None and log.timestamp > open_ts:
                total_seconds += (log.timestamp - open_ts).total_seconds()
            open_ts = None

    if open_ts is not None:
        cap = min(now_utc, window_cap)
        if cap > open_ts:
            total_seconds += (cap - open_ts).total_seconds()

    duration_minutes = int(total_seconds // 60) if total_seconds > 0 else 0
    if last_checkin_time is None:
        duration_minutes = None

    last_site = None
    for log in reversed(logs):
        if log.site_id:
            last_site = log.site
            break

    return {
        "last_checkin_time": last_checkin_time,
        "last_checkout_time": last_checkout_time,
        "duration_minutes": duration_minutes,
        "checkin_count": checkin_count,
        "checkout_count": checkout_count,
        "live_state": "checked_in" if (
            last_checkin_time and (not last_checkout_time or last_checkin_time > last_checkout_time)
        ) else "checked_out",
        "site": last_site,
    }


def _attendance_v3_shift_window_utc(shift_start_date, shift, user_tz, location_id=None):
    """
    Strict shift-instance window for v3 (supports overnight + consecutive overnight):
    [shift_start - grace, shift_end + grace]
    """
    grace_minutes = _get_site_setting_int(
        key="shift_grace_time",
        location_id=location_id,
        default_value=30,
    )
    shift_start_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.start_time, user_tz).astimezone(user_tz)
    if shift.end_time <= shift.start_time:
        shift_end_dt_user = combine_date_time_in_user_tz(
            shift_start_date + timedelta(days=1), shift.end_time, user_tz
        ).astimezone(user_tz)
    else:
        shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.end_time, user_tz).astimezone(user_tz)

    search_start_utc = (shift_start_dt_user - timedelta(minutes=grace_minutes)).astimezone(pytz.UTC)
    search_end_utc = (shift_end_dt_user + timedelta(minutes=grace_minutes)).astimezone(pytz.UTC)
    return search_start_utc, search_end_utc, shift_start_dt_user, shift_end_dt_user, grace_minutes


def _pick_closest_assignment_for_calendar_day(assignments, today, user_tz, user_now):
    """
    Same selection as shift_today_v3 when shift is scheduled today but not in active window.
    Picks assignment whose shift start today is closest to now (handles day + overnight).
    """
    best = None
    best_distance = None
    for assgn in assignments:
        shift = assgn.shift
        if not shift:
            continue
        shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
        shift_start_local = to_user_timezone(shift_start_dt, user_tz)
        distance_seconds = abs((shift_start_local - user_now).total_seconds())
        if best_distance is None or distance_seconds < best_distance:
            best_distance = distance_seconds
            best = assgn
    return best


def _kiosk_shift_gate_response(user, org_location, greeting_name, user_tz):
    """
    Kiosk face_attendance: explain why punch is blocked when get_today_assignment_v2
    returned nothing for this location. Aligned with shift_today_v3 (end_date >= today).
    Overnight shifts use _attendance_v3_shift_window_utc for window boundaries.
    Returns a DRF Response or None (caller should treat None as no_shift_today).
    """
    today = get_user_today(user_tz)
    user_now = get_user_now(user_tz)
    loc_id = getattr(org_location, "id", None)

    candidates = list(
        Assignment.objects.filter(
            guard_id=user.id,
            location_id=loc_id,
            start_date__lte=today,
            end_date__gte=today,
        ).select_related("shift", "location")
    )
    if not candidates:
        return None

    scheduled = _pick_closest_assignment_for_calendar_day(candidates, today, user_tz, user_now)
    if not scheduled or not scheduled.shift:
        return None

    shift = scheduled.shift
    shift_start_date = today
    _, _, shift_start_dt_user, shift_end_dt_user, grace_minutes = _attendance_v3_shift_window_utc(
        shift_start_date,
        shift,
        user_tz,
        location_id=loc_id,
    )
    window_start = shift_start_dt_user - timedelta(minutes=grace_minutes)
    window_end = shift_end_dt_user + timedelta(minutes=grace_minutes)

    if user_now < window_start:
        return Response(
            {
                "success": False,
                "code": "shift_not_started_yet",
                "message": (
                    f"Hi {greeting_name}, your shift starts at {shift_start_dt_user.strftime('%I:%M %p')}. "
                    f"You can check-in from {window_start.strftime('%I:%M %p')} (grace {grace_minutes} minutes)."
                ),
                "user_id": str(user.id),
                "has_shift": True,
                "shift_start_time": shift_start_dt_user.strftime("%I:%M %p"),
                "allowed_from": window_start.strftime("%I:%M %p"),
                "grace_minutes": grace_minutes,
            },
            status=status.HTTP_200_OK,
        )

    if user_now > window_end:
        return Response(
            {
                "success": False,
                "code": "shift_window_closed",
                "message": (
                    f"Hi {greeting_name}, your shift time is {shift_start_dt_user.strftime('%I:%M %p')} "
                    f"to {shift_end_dt_user.strftime('%I:%M %p')}. "
                    f"You cannot punch now. Please contact admin."
                ),
                "user_id": str(user.id),
                "has_shift": True,
                "shift_start_time": shift_start_dt_user.strftime("%I:%M %p"),
                "shift_end_time": shift_end_dt_user.strftime("%I:%M %p"),
                "grace_minutes": grace_minutes,
            },
            status=status.HTTP_200_OK,
        )

    return None


def _shift_required_duration_minutes(shift):
    """Scheduled shift length in minutes (supports overnight shifts)."""
    if not shift:
        return 0
    base = date(2000, 1, 1)
    start_dt = datetime.combine(base, shift.start_time)
    if shift.end_time <= shift.start_time:
        end_dt = datetime.combine(base + timedelta(days=1), shift.end_time)
    else:
        end_dt = datetime.combine(base, shift.end_time)
    return max(0, int((end_dt - start_dt).total_seconds() // 60))


def _attendance_v3_compute_pa_status_from_duration(duration_minutes, shift, _location_id=None):
    """
    After checkout: compare total worked minutes to scheduled shift length.
    - P : duration >= shift length
    - LD: duration < shift length
    """
    if duration_minutes is None:
        return None
    try:
        worked = int(duration_minutes)
    except (TypeError, ValueError):
        return None
    required = _shift_required_duration_minutes(shift)
    if required <= 0:
        return "LD"
    return "P" if worked >= required else "LD"


def _attendance_v3_compute_pa_status_from_summary(summary, shift, _location_id=None, window_end_utc=None):
    """
    - OW: open session (still checked in / no closing checkout in window).
    - P/LD: after checkout — worked duration vs scheduled shift length.
    """
    if not summary:
        return None
    if summary.get("live_state") == "checked_in" or not summary.get("last_checkout_time"):
        # Still open.
        if window_end_utc and timezone.now() >= window_end_utc:
            return "M"
        return "OW"
    return _attendance_v3_compute_pa_status_from_duration(
        summary.get("duration_minutes"), shift
    )


def _attendance_v3_refresh_saved_fields(attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc):
    """Persist v3 metrics on AttendanceCheckin from logs (does not clear images)."""
    summary = _attendance_v3_compute_from_logs(
        user, assignment, shift, org_location, search_start_utc, search_end_utc
    )
    attendance.last_checkin_time = summary["last_checkin_time"]
    attendance.last_checkout_time = summary["last_checkout_time"]
    attendance.duration_minutes = summary["duration_minutes"]
    attendance.checkin_count = summary.get("checkin_count") or 0
    attendance.checkout_count = summary.get("checkout_count") or 0
    if summary.get("site"):
        attendance.site = summary["site"]
    attendance.pa_status = _attendance_v3_compute_pa_status_from_summary(
        summary,
        shift,
        _location_id=None,
        window_end_utc=search_end_utc,
    )
    attendance.save()


def _attendance_v3_response_extras(attendance, summary, user_tz, request, shift=None, window_end_utc=None):
    """Build flat dict for shift_today_v3 / nested under attendance in checkin_v3 responses."""
    req = request

    def _iso(dt):
        if not dt:
            return None
        return to_user_timezone(dt, user_tz).isoformat()

    def _img_url(field):
        if not field or not getattr(field, "name", None):
            return None
        try:
            url = field.url
        except Exception:
            return None
        if req:
            return req.build_absolute_uri(url)
        return url

    return {
        "last_checkin": _iso(summary.get("last_checkin_time")),
        "last_checkout": _iso(summary.get("last_checkout_time")),
        "duration_minutes": summary.get("duration_minutes"),
        "checkin_count": summary.get("checkin_count") or 0,
        "checkout_count": summary.get("checkout_count") or 0,
        "live_state": summary.get("live_state"),
        "pa_status": _attendance_v3_compute_pa_status_from_summary(
            summary,
            (attendance.shift if attendance else shift),
            window_end_utc=window_end_utc,
        ),
        "checkin_image": _img_url(attendance.checkin_image) if attendance else None,
        "checkout_image": _img_url(attendance.checkout_image) if attendance else None,
    }


def _apply_attendance_v3_status_filter(queryset, status_value):
    """
    Map UI status filter values to v3 fields.
    """
    if not status_value:
        return queryset
    v = str(status_value).strip().lower()
    if v == "present":
        return queryset.filter(pa_status="P")
    if v in ("on_work", "onwork", "ow"):
        return queryset.filter(pa_status="OW")
    if v in ("missed", "m", "missed_checkout"):
        return queryset.filter(pa_status="M")
    if v in ("less_duration", "ld"):
        return queryset.filter(pa_status="LD")
    if v == "absent":
        # Backward-compatible alias after removing active "A" usage.
        return queryset.filter(pa_status__in=["LD", "A"])
    if v == "checked_out":
        return queryset.filter(
            Q(last_checkin_time__isnull=False) &
            Q(last_checkout_time__isnull=False) &
            Q(last_checkout_time__gte=F("last_checkin_time"))
        )
    if v == "checked_in":
        return queryset.filter(
            Q(last_checkin_time__isnull=False) & (
                Q(last_checkout_time__isnull=True) | Q(last_checkin_time__gt=F("last_checkout_time"))
            )
        )
    # Backward compatibility for legacy status field values.
    return queryset.filter(status=status_value)


def _attendance_v3_compute_shift_date(local_dt, shift):
    """
    Return logical shift date (shift start day) from a localized datetime.
    For overnight shifts, times after midnight and before shift end belong to previous date.
    """
    if not local_dt or not shift:
        return None
    shift_day = local_dt.date()
    if shift.end_time <= shift.start_time and local_dt.time() < shift.end_time:
        return shift_day - timedelta(days=1)
    return shift_day


def _attendance_v3_build_session_lines_for_export(attendance_obj, user_tz):
    """
    Build compact multiline session text:
    1) HH:MM -> HH:MM (Xh Ym)
    """
    source_dt = attendance_obj.checkin_time or attendance_obj.created_on
    if not source_dt:
        return ""
    shift_day = attendance_obj.shift_date or _attendance_v3_compute_shift_date(
        to_user_timezone(source_dt, user_tz), attendance_obj.shift
    )
    if not shift_day:
        return ""
    search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
        shift_day,
        attendance_obj.shift,
        user_tz,
        location_id=getattr(attendance_obj, "org_location_id", None),
    )

    logs = CheckInLog.objects.filter(
        guard=attendance_obj.guard,
        assignment=attendance_obj.assignment,
        shift=attendance_obj.shift,
        org_location=attendance_obj.org_location,
        timestamp__gte=search_start_utc,
        timestamp__lt=search_end_utc,
    ).order_by("timestamp")

    lines = []
    open_checkin = None
    idx = 1
    for log in logs:
        if log.type == "checkin":
            open_checkin = log.timestamp
            continue
        if log.type == "checkout" and open_checkin is not None and log.timestamp > open_checkin:
            in_local = to_user_timezone(open_checkin, user_tz)
            out_local = to_user_timezone(log.timestamp, user_tz)
            mins = int((log.timestamp - open_checkin).total_seconds() // 60)
            h = mins // 60
            m = mins % 60
            lines.append(f"{idx}) {in_local.strftime('%H:%M')} -> {out_local.strftime('%H:%M')} ({h}h {m}m)")
            idx += 1
            open_checkin = None

    if open_checkin is not None:
        in_local = to_user_timezone(open_checkin, user_tz)
        lines.append(f"{idx}) {in_local.strftime('%H:%M')} -> Open")

    return "\n".join(lines)


class AttendanceCheckinViewSet(viewsets.ModelViewSet):
    queryset = AttendanceCheckin.objects.all()
    serializer_class = AttendanceCheckinSerializer

    # ===== OLD APIs (for current mobile app - no overnight enhancements) =====
    
    def get_today_assignment(self, user, request=None):
        """OLD: Simple assignment lookup - today only, no overnight handling"""
        if request:
            user_tz = get_user_timezone_from_request(request)
        else:
            from patrol_backend.utils.timezone_utils import get_user_timezone
            user_tz = get_user_timezone(user)
        
        today = get_user_today(user_tz)
        return Assignment.objects.filter(
            guard_id=user.id,
            start_date__lte=today,
            end_date__gte=today
        ).first()

    # ===== V2 APIs (enhanced with overnight shift + auto-assignment support) =====

    def get_today_assignment_v2(self, user, request=None, location_id=None):
        """V2: Enhanced assignment lookup with overnight shift support.
           Returns a tuple: (assignment, logical_start_date)

           Optional location_id scopes to one org (kiosk / site-specific punch).
        """
        if request:
            user_tz = get_user_timezone_from_request(request)
        else:
            from patrol_backend.utils.timezone_utils import get_user_timezone
            user_tz = get_user_timezone(user)
        
        today = get_user_today(user_tz)
        yesterday = today - timedelta(days=1)
        user_now = get_user_now(user_tz)
        
        # Check assignments that overlap with today OR yesterday (for overnight shifts)
        assignments = Assignment.objects.filter(
            guard_id=user.id,
            start_date__lte=today,
            end_date__gte=yesterday
        ).select_related('shift', 'location')
        if location_id:
            assignments = assignments.filter(location_id=location_id)
        
        # Find the active shift instance
        for assignment in assignments:
            shift = assignment.shift
            if not shift:
                continue
                
            is_overnight = shift.end_time <= shift.start_time
            grace_minutes = _get_site_setting_int(
                key="shift_grace_time",
                location_id=getattr(assignment, "location_id", None) or getattr(user, "location_id", None),
                default_value=30,
            )
            
            # Check if this is yesterday's overnight shift still active today
            # Only valid if assignment actually includes yesterday.
            if (
                is_overnight
                and assignment.start_date <= yesterday <= assignment.end_date
            ):
                # Add 30 min grace period for late checkout
                shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz) + timedelta(minutes=grace_minutes)
                if user_now <= shift_end_dt:
                    return assignment, yesterday  # Still active from yesterday
            
            # Check if this is today's shift (regular or overnight starting today).
            # Do not evaluate today's time window unless assignment includes today.
            if assignment.start_date <= today <= assignment.end_date:
                # Add 30 min early checkin grace period
                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz) - timedelta(minutes=grace_minutes)
                
                if is_overnight:
                    # For overnight shifts, calculate logical start and end boundaries including grace periods
                    actual_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                    actual_end_dt = combine_date_time_in_user_tz(today + timedelta(days=1), shift.end_time, user_tz)
                    
                    window_start = actual_start_dt - timedelta(minutes=grace_minutes)
                    window_end = actual_end_dt + timedelta(minutes=grace_minutes)
                    
                    # Also check if it's past midnight and we are in yesterday's shift window
                    yesterday_start_dt = combine_date_time_in_user_tz(yesterday, shift.start_time, user_tz)
                    yesterday_end_dt = combine_date_time_in_user_tz(yesterday + timedelta(days=1), shift.end_time, user_tz)
                    
                    yesterday_window_start = yesterday_start_dt - timedelta(minutes=grace_minutes)
                    yesterday_window_end = yesterday_end_dt + timedelta(minutes=grace_minutes)

                    if (
                        assignment.start_date <= yesterday <= assignment.end_date
                        and yesterday_window_start <= user_now <= yesterday_window_end
                    ):
                        return assignment, yesterday
                    elif window_start <= user_now <= window_end:
                         return assignment, today
                else:
                    # Regular shift boundaries
                    shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz) + timedelta(minutes=grace_minutes)
                    if shift_start_dt <= user_now <= shift_end_dt:
                        return assignment, today
        
        return None, None

    def _resolve_scope_location_id(self, request):
        """
        Resolve location scope based on actor role.
        - superuser: can pass location_id, fallback to actor.location_id
        - admin/so/fo: locked to actor.location_id
        """
        actor = request.user
        requested_location_id = request.query_params.get("location_id") or request.data.get("location_id")
        actor_role = (getattr(actor, "role", "") or "").strip().lower()

        if getattr(actor, "is_superuser", False):
            scoped_location_id = requested_location_id or getattr(actor, "location_id", None)
            if not scoped_location_id:
                return None, Response(
                    {"error": "location_id is required for superuser"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            return str(scoped_location_id), None

        if actor_role not in {"admin", "so", "fo"}:
            return None, Response(
                {"error": "Only admin/SO/FO/superuser can access this API"},
                status=status.HTTP_403_FORBIDDEN,
            )

        actor_location_id = getattr(actor, "location_id", None)
        if not actor_location_id:
            return None, Response(
                {"error": "User is not mapped to a location"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if requested_location_id and str(requested_location_id) != str(actor_location_id):
            return None, Response(
                {"error": "You can access only your own location"},
                status=status.HTTP_403_FORBIDDEN,
            )

        return str(actor_location_id), None

    def _parse_bulk_datetime_to_utc(self, raw_value, user_tz, label):
        if raw_value in (None, ""):
            raise ValueError(f"{label} is required")
        try:
            parsed = datetime.fromisoformat(str(raw_value).strip().replace("Z", "+00:00"))
        except Exception:
            raise ValueError(f"Invalid {label}. Use ISO datetime format.")
        if parsed.tzinfo is None:
            parsed = user_tz.localize(parsed)
        return parsed.astimezone(pytz.UTC)

    def find_and_create_default_assignment(self, user, checkin_time_user, user_tz, location_lat, location_lon):
        """
        Find default shift matching check-in time and create assignment.
        Returns assignment if created, None otherwise.
        """
        checkin_time = checkin_time_user.time()
        checkin_date = checkin_time_user.date()
        
        # Get all default shifts for the user's location (or all locations if location not specified)
        # First, try to find location based on check-in coordinates
        # Note: Some locations in DB may not have lat/lon, so we only check locations that have coordinates
        location = None
        if location_lat and location_lon:
            # Find nearest location (within reasonable distance, e.g., 1km)
            # Only check locations that have both latitude and longitude
            locations = Location.objects.filter(
                is_deleted=False,
                latitude__isnull=False,
                longitude__isnull=False
            )
            min_distance = float('inf')
            for loc in locations:
                dist = geodesic((location_lat, location_lon), (loc.latitude, loc.longitude)).meters
                if dist < min_distance and dist < 1000:  # Within 1km
                    min_distance = dist
                    location = loc
        
        # Fallback: If location not found via coordinates (or coordinates not provided, or locations don't have coordinates),
        # use user's location from token
        if not location and user.location:
            location = user.location
        
        # Get default shifts
        if location:
            default_shifts = Shift.objects.filter(
                is_default=True,
                is_deleted=False,
                location=location
            ).select_related('checkpoint_template')
        else:
            # If no location found, get all default shifts (user will need to select location)
            default_shifts = Shift.objects.filter(
                is_default=True,
                is_deleted=False
            ).select_related('checkpoint_template')
        
        if not default_shifts.exists():
            return None
        
        # Find shift that matches check-in time
        # Priority 1: Nearest shift based on start time (±2 hours)
        # Priority 2: If user is in active shift ending within 2 hours, and upcoming shift starts within 2 hours, assign upcoming shift
        matching_shift = None
        best_match_score = -1
        WINDOW_MINUTES = 120  # ±2 hours = 120 minutes
        
        checkin_minutes = checkin_time.hour * 60 + checkin_time.minute
        
        # First pass: Find all shifts within ±2 hours of start time (nearest shift logic)
        candidate_shifts = []
        active_shift_ending_soon = None
        
        for shift in default_shifts:
            is_overnight = shift.end_time <= shift.start_time
            start_minutes = shift.start_time.hour * 60 + shift.start_time.minute
            end_minutes = shift.end_time.hour * 60 + shift.end_time.minute
            matches = False
            minutes_from_start = None
            is_active = False
            is_upcoming = False
            
            # Check if check-in is within active shift window
            if is_overnight:
                # Overnight shift: Check if in active window
                if checkin_time >= shift.start_time or checkin_time < shift.end_time:
                    is_active = True
                    # Calculate minutes until end
                    if checkin_time >= shift.start_time:
                        # Check-in is after start, end is next day
                        minutes_until_end = (24 * 60 - checkin_minutes) + end_minutes
                    else:
                        # Check-in is before end (after midnight part)
                        minutes_until_end = end_minutes - checkin_minutes
                    
                    # Check if ending within 2 hours
                    if minutes_until_end <= WINDOW_MINUTES:
                        active_shift_ending_soon = shift
            else:
                # Regular shift: Check if in active window
                if shift.start_time <= checkin_time <= shift.end_time:
                    is_active = True
                    minutes_until_end = end_minutes - checkin_minutes
                    if minutes_until_end <= WINDOW_MINUTES:
                        active_shift_ending_soon = shift
            
            # Check if within ±2 hours of start time
            if is_overnight:
                if checkin_time >= shift.start_time:
                    minutes_from_start = checkin_minutes - start_minutes
                    if minutes_from_start <= WINDOW_MINUTES:
                        matches = True
                else:
                    minutes_until_start = start_minutes - checkin_minutes
                    if minutes_until_start <= WINDOW_MINUTES:
                        matches = True
                        minutes_from_start = minutes_until_start
                        is_upcoming = True
                    else:
                        # Check yesterday's start
                        minutes_since_yesterday_start = (24 * 60 - start_minutes) + checkin_minutes
                        if minutes_since_yesterday_start <= WINDOW_MINUTES:
                            matches = True
                            minutes_from_start = minutes_since_yesterday_start
            else:
                if checkin_time >= shift.start_time:
                    minutes_from_start = checkin_minutes - start_minutes
                    if minutes_from_start <= WINDOW_MINUTES:
                        matches = True
                else:
                    minutes_until_start = start_minutes - checkin_minutes
                    if minutes_until_start <= WINDOW_MINUTES:
                        matches = True
                        minutes_from_start = minutes_until_start
                        is_upcoming = True
            
            if matches and minutes_from_start is not None:
                candidate_shifts.append({
                    'shift': shift,
                    'minutes_from_start': minutes_from_start,
                    'is_upcoming': is_upcoming,
                    'is_active': is_active
                })
        
        # Priority logic:
        # 1. If active shift ending soon → find next upcoming shift (any shift starting after current time)
        # 2. Otherwise → assign nearest shift (closest to start time, within ±2 hours)
        if active_shift_ending_soon:
            # Find next upcoming shift (any shift that starts after current time, no ±2 hours restriction)
            upcoming_shift = None
            min_minutes_until_start = float('inf')
            
            for shift in default_shifts:
                if shift == active_shift_ending_soon:
                    continue  # Skip the active shift
                
                is_overnight = shift.end_time <= shift.start_time
                start_minutes = shift.start_time.hour * 60 + shift.start_time.minute
                minutes_until_start = None
                
                if is_overnight:
                    # For overnight shift, check if start time is today or tomorrow
                    if checkin_time >= shift.start_time:
                        # Start time already passed today, next occurrence is tomorrow
                        minutes_until_start = (24 * 60 - checkin_minutes) + start_minutes
                    else:
                        # Start time is later today
                        minutes_until_start = start_minutes - checkin_minutes
                else:
                    # Regular shift
                    if checkin_time >= shift.start_time:
                        # Start time already passed today, next occurrence is tomorrow
                        minutes_until_start = (24 * 60 - checkin_minutes) + start_minutes
                    else:
                        # Start time is later today
                        minutes_until_start = start_minutes - checkin_minutes
                
                # Find the shift that starts soonest (next upcoming)
                if minutes_until_start < min_minutes_until_start:
                    min_minutes_until_start = minutes_until_start
                    upcoming_shift = shift
            
            if upcoming_shift:
                # Assign next upcoming shift
                matching_shift = upcoming_shift
            else:
                # No upcoming shift found, assign nearest shift from candidates
                if candidate_shifts:
                    best_candidate = min(candidate_shifts, key=lambda x: x['minutes_from_start'])
                    matching_shift = best_candidate['shift']
        else:
            # No active shift ending soon, assign nearest shift (within ±2 hours)
            if candidate_shifts:
                best_candidate = min(candidate_shifts, key=lambda x: x['minutes_from_start'])
                matching_shift = best_candidate['shift']
        
        if not matching_shift:
            return None
        
        # Get checkpoints from template
        checkpoints = []
        if matching_shift.checkpoint_template:
            template = matching_shift.checkpoint_template
            if template.checkpoints:
                checkpoints = template.checkpoints  # Already in format [{"checkpoint_id": "uuid", "time": "HH:MM"}]
        
        # Determine assignment date range
        start_date = checkin_date
        if matching_shift.end_time <= matching_shift.start_time:
            # Overnight shift
            # If check-in time is before end_time (e.g., 1 AM), shift started yesterday
            if checkin_time < matching_shift.end_time:
                start_date = checkin_date - timedelta(days=1)  # Shift started yesterday
            # End date is the day the shift ends (today for overnight shifts)
            end_date = checkin_date
        else:
            # Regular shift - same day
            end_date = checkin_date
        
        # Create assignment
        assignment = Assignment.objects.create(
            guard=user,
            location=matching_shift.location,
            shift=matching_shift,
            start_date=start_date,
            end_date=end_date,
            checkpoints=checkpoints
        )
        
        return assignment

    @action(detail=False, methods=["get"])
    def shift_today(self, request):
        """OLD: Simple shift_today for current mobile app"""
        user = request.user
        user_id = user.id

        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        today = user_now.date()

        assignment = Assignment.objects.filter(
            guard_id=user_id,
            start_date__lte=today,
            end_date__gte=today
        ).first()

        if not assignment:
            return Response({
                "has_shift": False,
                "show_checkin": False,
                "show_checkout": False,
                "message": "No shifts today"
            }, status=status.HTTP_200_OK)

        shift = assignment.shift
        location = assignment.location

        start_utc, end_utc = convert_date_range_to_utc(today, today, user_tz)

        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__gte=start_utc,
            checkin_time__lt=end_utc + timedelta(days=1)
        ).first()

        show_checkin = False
        show_checkout = False
        message = ""

        shift_start_dt_user = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
        shift_start_dt_user = shift_start_dt_user.astimezone(user_tz)
  
        if shift.end_time <= shift.start_time:
            shift_end_dt_user = combine_date_time_in_user_tz(today + timedelta(days=1), shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        else:
            shift_end_dt_user = combine_date_time_in_user_tz(today, shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        
        grace_minutes = _get_site_setting_int(
            key="shift_grace_time",
            location_id=getattr(location, "id", None),
            default_value=30,
        )
        earliest_checkin = shift_start_dt_user - timedelta(minutes=grace_minutes)
        latest_checkin = shift_end_dt_user
        earliest_checkout = shift_start_dt_user
        latest_checkout = shift_end_dt_user + timedelta(minutes=grace_minutes)

        if not attendance:
            if earliest_checkin <= user_now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif user_now < earliest_checkin:
                message = "Too early to check in"
            else:
                message = "Shift ended"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                checkin_time_user = to_user_timezone(attendance.checkin_time, user_tz)
                if user_now <= shift_end_dt_user:
                    show_checkout = True
                    message = "You are checked in, checkout when done"
                else:
                    message = "Shift ended"
            elif attendance.checkin_time and attendance.checkout_time:
                checkout_time_user = to_user_timezone(attendance.checkout_time, user_tz)
                if user_now <= shift_end_dt_user:
                    start_utc, end_utc = convert_date_range_to_utc(today, today, user_tz)
                    latest_checkin = CheckInLog.objects.filter(
                        guard=user,
                        assignment=assignment,
                        shift=shift,
                        org_location=location,
                        type="checkin",
                        timestamp__gte=start_utc,
                        timestamp__lt=end_utc + timedelta(days=1),
                        timestamp__gt=attendance.checkout_time
                    ).order_by("-timestamp").first()

                    if latest_checkin:
                        show_checkin = False
                        show_checkout = True
                        message = "You have checked-in. You can check-out"
                    else:
                        show_checkin = True
                        show_checkout = False
                        message = "You are checked out already but can check in again"
   
                else:
                    message = "Shift ended"

        return Response({
                "has_shift": True,
                "shift_id": str(shift.id),
                "shift_start": shift.start_time,
                "shift_end": shift.end_time,
                "location_name": location.name,
                "show_checkin": show_checkin,
                "show_checkout": show_checkout,
                "message": message
            }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="shift_today_v2")
    def shift_today_v2(self, request):
        """V2: Enhanced shift_today with overnight shift support"""
        user = request.user
        user_id = user.id

        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        today = user_now.date()
        yesterday = today - timedelta(days=1)

        # 1) Try to find currently active shift via the exact same v2 helper used by checkin/checkout
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        # 2) If nothing is currently "active" inside the grace period, find the closest upcoming scheduled shift for TODAY
        if not assignment:
            today = get_user_today(user_tz)
            user_now = get_user_now(user_tz)
            assignments = Assignment.objects.filter(
                guard_id=user.id,
                start_date__lte=today,
                end_date__gte=today
            ).select_related('shift', 'location')

            best_scheduled = None
            best_distance = None
            for assgn in assignments:
                shift = assgn.shift
                if not shift:
                    continue
                
                # Check distance to start time
                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                distance_seconds = abs((shift_start_dt - user_now).total_seconds())

                if best_distance is None or distance_seconds < best_distance:
                    best_distance = distance_seconds
                    best_scheduled = assgn

            if best_scheduled:
                assignment = best_scheduled
                shift_start_date = today

        if not assignment:
            return Response({
                "has_shift": False,
                "show_checkin": False,
                "show_checkout": False,
                "message": "No shifts today"
            }, status=status.HTTP_200_OK)

        shift = assignment.shift
        location = assignment.location

        # Use shift_start_date (could be yesterday for overnight) for UTC conversion
        start_utc, end_utc = convert_date_range_to_utc(shift_start_date, shift_start_date, user_tz)
        
        # Exact logical boundary search window (D 00:00:00 to D+1 23:59:59)
        search_start_utc = start_utc
        search_end_utc = end_utc + timedelta(days=1)

        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__gte=search_start_utc,
            checkin_time__lt=search_end_utc
        ).first()

        show_checkin = False
        show_checkout = False
        message = ""

        # Calculate shift window using shift_start_date
        shift_start_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.start_time, user_tz)
        shift_start_dt_user = shift_start_dt_user.astimezone(user_tz)
  
        if shift.end_time <= shift.start_time:
            # Overnight shift - end is next day
            shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date + timedelta(days=1), shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        else:
            shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        
        # Define check-in and check-out windows (in user timezone)
        grace_minutes = _get_site_setting_int(
            key="shift_grace_time",
            location_id=getattr(location, "id", None),
            default_value=30,
        )
        earliest_checkin = shift_start_dt_user - timedelta(minutes=grace_minutes)
        latest_checkin = shift_end_dt_user
        earliest_checkout = shift_start_dt_user
        latest_checkout = shift_end_dt_user + timedelta(minutes=grace_minutes)

        # Scenario logic (all comparisons in user timezone)
        if not attendance:
            # Scenario 1: No check-in yet
            if earliest_checkin <= user_now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif user_now < earliest_checkin:
                message = "Too early to check in"
            else:
                # Scenario 5: Shift ended without check-out
                message = "Shift ended"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                # Convert stored UTC time to user timezone for comparison
                checkin_time_user = to_user_timezone(attendance.checkin_time, user_tz)
                if user_now <= latest_checkout:
                    # Scenario 2: Checked in, not yet checked out
                    show_checkout = True
                    message = "You are checked in, checkout when done"
                else:
                    # Scenario 5: Shift ended, no checkout
                    message = "Shift ended"
            elif attendance.checkin_time and attendance.checkout_time:
                checkout_time_user = to_user_timezone(attendance.checkout_time, user_tz)
                if user_now <= latest_checkout:
                    # Check if user has checked in again after checkout
                    # Use expanded date range for query
                    latest_checkin = CheckInLog.objects.filter(
                        guard=user,
                        assignment=assignment,
                        shift=shift,
                        org_location=location,
                        type="checkin",
                        timestamp__gte=search_start_utc,
                        timestamp__lt=search_end_utc,
                        timestamp__gt=attendance.checkout_time
                    ).order_by("-timestamp").first()

                    if latest_checkin:
                        show_checkin = False
                        show_checkout = True
                        message = "You have checked-in. You can check-out"
                    else:
                        show_checkin = True
                        show_checkout = False
                        message = "You are checked out already but can check in again"
   
                else:
                    # Scenario 5: Shift ended
                    message = "Shift ended"

        return Response({
                "has_shift": True,
                "shift_id": str(shift.id),
                "shift_start": shift.start_time,
                "shift_end": shift.end_time,
                "location_name": location.name,
                "show_checkin": show_checkin,
                "show_checkout": show_checkout,
                "message": message
            }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="shift_today_v3")
    def shift_today_v3(self, request):
        """Same as shift_today_v2 plus last_checkin, last_checkout, duration_minutes, image URLs."""
        user = request.user

        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        today = user_now.date()

        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            today = get_user_today(user_tz)
            user_now = get_user_now(user_tz)
            assignments = Assignment.objects.filter(
                guard_id=user.id,
                start_date__lte=today,
                end_date__gte=today
            ).select_related('shift', 'location')

            best_scheduled = None
            best_distance = None
            for assgn in assignments:
                shift = assgn.shift
                if not shift:
                    continue
                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                distance_seconds = abs((shift_start_dt - user_now).total_seconds())
                if best_distance is None or distance_seconds < best_distance:
                    best_distance = distance_seconds
                    best_scheduled = assgn

            if best_scheduled:
                assignment = best_scheduled
                shift_start_date = today

        if not assignment:
            return Response({
                "has_shift": False,
                "show_checkin": False,
                "show_checkout": False,
                "message": "No shifts today",
                "shift_name":None,
                "shift_start": None,
                "shift_end": None,
                "location_name": None,
                "last_checkin": None,
                "last_checkout": None,
                "duration_minutes": None,
                "checkin_image": None,
                "checkout_image": None,
            }, status=status.HTTP_200_OK)

        shift = assignment.shift
        location = assignment.location

        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(location, "id", None)
        )

        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__gte=search_start_utc,
            checkin_time__lt=search_end_utc
        ).first()

        show_checkin = False
        show_checkout = False
        message = ""

        shift_start_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.start_time, user_tz)
        shift_start_dt_user = shift_start_dt_user.astimezone(user_tz)

        if shift.end_time <= shift.start_time:
            shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date + timedelta(days=1), shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        else:
            shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)

        grace_minutes = _get_site_setting_int(
            key="shift_grace_time",
            location_id=getattr(location, "id", None),
            default_value=30,
        )

        earliest_checkin = shift_start_dt_user - timedelta(minutes=grace_minutes)
        latest_checkin = shift_end_dt_user
        earliest_checkout = shift_start_dt_user
        latest_checkout = shift_end_dt_user + timedelta(minutes=grace_minutes)

        if not attendance:
            if earliest_checkin <= user_now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif user_now < earliest_checkin:
                message = "Too early to check in"
            else:
                message = "Shift ended"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                if user_now <= latest_checkout:
                    show_checkout = True
                    message = "You are checked in, checkout when done"
                else:
                    message = "Shift ended"
            elif attendance.checkin_time and attendance.checkout_time:
                if user_now <= latest_checkout:
                    latest_checkin_log = CheckInLog.objects.filter(
                        guard=user,
                        assignment=assignment,
                        shift=shift,
                        org_location=location,
                        type="checkin",
                        timestamp__gte=search_start_utc,
                        timestamp__lt=search_end_utc,
                        timestamp__gt=attendance.checkout_time
                    ).order_by("-timestamp").first()

                    if latest_checkin_log:
                        show_checkin = False
                        show_checkout = True
                        message = "You have checked-in. You can check-out"
                    else:
                        show_checkin = True
                        show_checkout = False
                        message = "You are checked out already but can check in again"
                else:
                    message = "Shift ended"

        summary = _attendance_v3_compute_from_logs(
            user, assignment, shift, location, search_start_utc, search_end_utc
        )
        extras = _attendance_v3_response_extras(
            attendance,
            summary,
            user_tz,
            request,
            shift=shift,
            window_end_utc=search_end_utc,
        )

        return Response({
            "has_shift": True,
            "shift_id": str(shift.id),
            "shift_start": shift.start_time,
            "shift_end": shift.end_time,
            "location_name": location.name,
            "show_checkin": show_checkin,
            "show_checkout": show_checkout,
            "message": message,
            "shift_name":shift.name,
            **extras,
        }, status=status.HTTP_200_OK)

  
    @action(detail=False, methods=["post"])
    def checkin(self, request):
        """OLD: Simple checkin for current mobile app"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment = self.get_today_assignment(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))
        distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters

        allowed_distance = _get_site_setting_int(
            key="attendance_distance",
            location_id=getattr(org_location, "id", None),
            default_value=100,
        )
        if distance > allowed_distance:
            return Response({"error": f"Not within >{allowed_distance}m of assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        user_today = get_user_today(user_tz)
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)

        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon
        )

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={'created_on': timezone.now()}
        )
        attendance_list = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=start_utc,
            created_on__lt=end_utc + timedelta(days=1)
        )
        if attendance_list.exists():
            attendance = attendance_list.first()

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=start_utc,
            timestamp__lt=end_utc + timedelta(days=1)
        ).order_by("timestamp").first()

        if earliest_checkin:
            attendance.checkin_time = earliest_checkin.timestamp
        attendance.latitude = earliest_checkin.latitude
        attendance.longitude = earliest_checkin.longitude
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """OLD: Simple checkout for current mobile app"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment = self.get_today_assignment(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        user_today = get_user_today(user_tz)
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=start_utc,
            created_on__lt=end_utc + timedelta(days=1)
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            latitude=lat,
            longitude=lon
        )

        latest_checkout = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__gte=start_utc,
            timestamp__lt=end_utc + timedelta(days=1)
        ).order_by("-timestamp").first()

        if latest_checkout:
            attendance.checkout_time = latest_checkout.timestamp
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_200_OK)

    # ===== V2 CHECKIN/CHECKOUT (enhanced with overnight + auto-assignment) =====

    @action(detail=False, methods=["post"], url_path="checkin_v2")
    def checkin_v2(self, request):
        """V2: Enhanced checkin with overnight shift + auto-assignment support"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        # If no assignment, try to create one from default shift
        if not assignment:
            # lat = float(request.data.get("latitude", 0))
            # lon = float(request.data.get("longitude", 0))
            # assignment = self.find_and_create_default_assignment(user, user_now, user_tz, lat, lon)
            
            # if not assignment:
            #     return Response({"message": "No shifts today and no default shift found"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))
        
        # Multi-site proximity validation (optional for v2)
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)

        # Validate location if location has coordinates
        if org_location.latitude and org_location.longitude:
            distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters
            allowed_distance = _get_site_setting_int(
                key="attendance_distance",
                location_id=getattr(org_location, "id", None),
                default_value=100,
            )
            if distance > allowed_distance:
                return Response(
                    {"error": f"Not within >{allowed_distance}m of assigned location"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        # Strict shift-instance search window (supports consecutive overnight correctly).
        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )

        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
            defaults={"created_on": timezone.now()},
        )
        
        # Update AttendanceCheckin.site if it matched a site
        if matched_site:
            attendance.site = matched_site
            attendance.save()

        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        attendance_list = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=search_start_utc,
            created_on__lt=search_end_utc
        )
        if attendance_list.exists():
            attendance = attendance_list.first()

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("timestamp").first()

        if earliest_checkin:
            attendance.checkin_time = earliest_checkin.timestamp
            attendance.latitude = earliest_checkin.latitude
            attendance.longitude = earliest_checkin.longitude
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="checkout_v2")
    def checkout_v2(self, request):
        """V2: Enhanced checkout with overnight shift support"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        # Multi-site proximity validation
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)

        # Strict shift-instance search window (supports consecutive overnight correctly).
        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )

        latest_checkout = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("-timestamp").first()

        if latest_checkout:
            attendance.checkout_time = latest_checkout.timestamp
        attendance.save()

        # Populate v3 summary fields and working-duration mark for web P/HA/A.
        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="checkin_v3")
    @parser_classes([MultiPartParser, FormParser])
    def checkin_v3(self, request):
        """V3: checkin_v2 + optional image (multipart). Updates v3 summary fields on AttendanceCheckin."""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location

        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        # Multi-site proximity validation
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)

        if org_location.latitude and org_location.longitude:
            distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters
            allowed_distance = _get_site_setting_int(
                key="attendance_distance",
                location_id=getattr(org_location, "id", None),
                default_value=100,
            )
            if distance > allowed_distance:
                return Response({"error": f"Not within >{allowed_distance}m of assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )

        image_file = request.FILES.get("image") or request.FILES.get("checkin_image")
        raw_bytes = None
        img_name = "checkin.jpg"
        if image_file:
            img_name = getattr(image_file, "name", img_name) or img_name
            raw_bytes = image_file.read()

        log = CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )
        if raw_bytes is not None:
            log.image.save(img_name, ContentFile(raw_bytes), save=True)

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={
                "created_on": timezone.now(),
                "shift_date": shift_start_date,
            }
        )

        attendance_list = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
        )
        if attendance_list.exists():
            attendance = attendance_list.first()

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("timestamp").first()

        if earliest_checkin:
            attendance.checkin_time = earliest_checkin.timestamp
            attendance.latitude = earliest_checkin.latitude
            attendance.longitude = earliest_checkin.longitude

        attendance.shift_date = shift_start_date

        if raw_bytes is not None:
            attendance.checkin_image.save(img_name, ContentFile(raw_bytes), save=False)

        attendance.save()
        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        return Response(
            AttendanceCheckinSerializer(attendance, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )

    @action(detail=False, methods=["post"], url_path="checkout_v3")
    @parser_classes([MultiPartParser, FormParser])
    def checkout_v3(self, request):
        """V3: checkout_v2 + optional image (multipart). Updates v3 summary fields on AttendanceCheckin."""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        # Multi-site proximity validation
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)

        # Enforce same 100m distance rule as checkin_v3.
        if org_location.latitude and org_location.longitude:
            distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters
            allowed_distance = _get_site_setting_int(
                key="attendance_distance",
                location_id=getattr(org_location, "id", None),
                default_value=100,
            )
            if distance > allowed_distance:
                return Response(
                    {"error": f"Not within >{allowed_distance}m of assigned location"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        image_file = request.FILES.get("image") or request.FILES.get("checkout_image")
        raw_bytes = None
        img_name = "checkout.jpg"
        if image_file:
            img_name = getattr(image_file, "name", img_name) or img_name
            raw_bytes = image_file.read()

        log = CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )
        if raw_bytes is not None:
            log.image.save(img_name, ContentFile(raw_bytes), save=True)

        if raw_bytes is not None:
            attendance.checkout_image.save(img_name, ContentFile(raw_bytes), save=False)

        latest_checkout = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("-timestamp").first()

        if latest_checkout:
            attendance.checkout_time = latest_checkout.timestamp
        attendance.shift_date = shift_start_date
        attendance.site = matched_site
        attendance.save()

        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        return Response(
            AttendanceCheckinSerializer(attendance, context={'request': request}).data,
            status=status.HTTP_200_OK
        )

    @action(detail=False, methods=["post"], url_path="checkin_v4")
    @parser_classes([MultiPartParser, FormParser])
    def checkin_v4(self, request):
        """
        Same as checkin_v3, plus if location.is_face_attendance_enabled:
        require live image and match to user's enrolled face_encoding / face_photo.
        """
        from patrol_backend.utils.face_utils import (
            is_face_attendance_available,
            verify_user_face,
        )

        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location

        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        # Multi-site proximity validation
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)
        if not matched_site:
            return Response(
                {"error": "You are not within the authorized boundary of any site for this location."},
                status=status.HTTP_400_BAD_REQUEST
            )

        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )

        image_file = request.FILES.get("image") or request.FILES.get("checkin_image")
        raw_bytes = image_file.read() if image_file else None

        if getattr(org_location, "is_face_attendance_enabled", False):
            if not is_face_attendance_available():
                return Response(
                    {
                        "error": "Face attendance is enabled for this location but face_recognition is not installed on the server.",
                        "hint": "See docs/FACE_ATTENDANCE_INSTALL.md",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            if not raw_bytes:
                return Response({"error": "Face attendance requires an image"}, status=status.HTTP_400_BAD_REQUEST)
            ok, msg, dist_face = verify_user_face(user, raw_bytes)
            if not ok:
                return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)

        img_name = "checkin.jpg"
        if image_file:
            img_name = getattr(image_file, "name", img_name) or img_name

        log = CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )
        if raw_bytes is not None:
            log.image.save(img_name, ContentFile(raw_bytes), save=True)

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={
                "created_on": timezone.now(),
                "shift_date": shift_start_date,
            }
        )

        attendance_list = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
        )
        if attendance_list.exists():
            attendance = attendance_list.first()

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("timestamp").first()

        if earliest_checkin:
            attendance.checkin_time = earliest_checkin.timestamp
            attendance.latitude = earliest_checkin.latitude
            attendance.longitude = earliest_checkin.longitude

        attendance.shift_date = shift_start_date
        attendance.site = matched_site

        if raw_bytes is not None:
            attendance.checkin_image.save(img_name, ContentFile(raw_bytes), save=False)

        attendance.save()
        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        data = AttendanceCheckinSerializer(attendance, context={'request': request}).data
        if isinstance(data, dict):
            data["face_attendance"] = bool(getattr(org_location, "is_face_attendance_enabled", False))
            data["face_verified"] = bool(getattr(org_location, "is_face_attendance_enabled", False))
        return Response(data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="checkout_v4")
    @parser_classes([MultiPartParser, FormParser])
    def checkout_v4(self, request):
        """
        Same as checkout_v3, plus optional face match when location.is_face_attendance_enabled.
        """
        from patrol_backend.utils.face_utils import (
            is_face_attendance_available,
            verify_user_face,
        )

        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        # Multi-site proximity validation
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)
        if not matched_site:
            return Response(
                {"error": "You are not within the authorized boundary of any site for this location."},
                status=status.HTTP_400_BAD_REQUEST
            )

        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        image_file = request.FILES.get("image") or request.FILES.get("checkout_image")
        raw_bytes = image_file.read() if image_file else None

        if getattr(org_location, "is_face_attendance_enabled", False):
            if not is_face_attendance_available():
                return Response(
                    {
                        "error": "Face attendance is enabled for this location but face_recognition is not installed on the server.",
                        "hint": "See docs/FACE_ATTENDANCE_INSTALL.md",
                    },
                    status=status.HTTP_503_SERVICE_UNAVAILABLE,
                )
            if not raw_bytes:
                return Response({"error": "Face attendance requires an image"}, status=status.HTTP_400_BAD_REQUEST)
            ok, msg, dist_face = verify_user_face(user, raw_bytes)
            if not ok:
                return Response({"error": msg}, status=status.HTTP_400_BAD_REQUEST)

        img_name = "checkout.jpg"
        if image_file:
            img_name = getattr(image_file, "name", img_name) or img_name

        log = CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )
        if raw_bytes is not None:
            log.image.save(img_name, ContentFile(raw_bytes), save=True)

        if raw_bytes is not None:
            attendance.checkout_image.save(img_name, ContentFile(raw_bytes), save=False)

        latest_checkout = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("-timestamp").first()

        if latest_checkout:
            attendance.checkout_time = latest_checkout.timestamp
        attendance.shift_date = shift_start_date
        attendance.site = matched_site
        attendance.save()

        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        data = AttendanceCheckinSerializer(attendance, context={'request': request}).data
        if isinstance(data, dict):
            data["face_attendance"] = bool(getattr(org_location, "is_face_attendance_enabled", False))
            data["face_verified"] = bool(getattr(org_location, "is_face_attendance_enabled", False))
        return Response(data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="face_attendance")
    @parser_classes([MultiPartParser, FormParser])
    def face_attendance(self, request):
        """
        Kiosk punch API (admin-token driven, face identifies target guard).
        - Mandatory geofence validation
        - One endpoint auto-handles checkin / checkout
        """
        from patrol_backend.utils.face_utils import is_face_attendance_available
        from patrol_backend.utils.face_index import identify_user_in_location
        from patrol_backend.utils.timezone_utils import get_user_timezone

        def _kiosk_error(code, message, http_status=status.HTTP_400_BAD_REQUEST, extra=None):
            body = {"success": False, "code": code, "message": message}
            if extra:
                body.update(extra)
            return Response(body, status=http_status)

        actor = request.user
        actor_role = (getattr(actor, "role", "") or "").strip().lower()
        if not (getattr(actor, "is_superuser", False) or actor_role == "admin"):
            return _kiosk_error(
                "forbidden",
                "Only admin can use kiosk face attendance",
                status.HTTP_403_FORBIDDEN,
            )

        if not is_face_attendance_available():
            return _kiosk_error(
                "face_library_missing",
                "Face recognition is not installed on server",
                status.HTTP_503_SERVICE_UNAVAILABLE,
                extra={"hint": "Install requirements-face-attendance.txt and faiss-cpu"},
            )

        requested_location_id = request.data.get("location_id")
        if getattr(actor, "is_superuser", False):
            scoped_location_id = requested_location_id or getattr(actor, "location_id", None)
            if not scoped_location_id:
                return _kiosk_error("location_required", "location_id is required for superuser kiosk punch")
        else:
            if not getattr(actor, "location_id", None):
                return _kiosk_error("location_missing", "Admin must be mapped to a location")
            scoped_location_id = str(actor.location_id)
            if requested_location_id and str(requested_location_id) != str(scoped_location_id):
                return _kiosk_error(
                    "location_forbidden",
                    "Admin can only use kiosk for own location",
                    status.HTTP_403_FORBIDDEN,
                )

        org_location = Location.objects.filter(id=scoped_location_id, is_deleted=False).first()
        if not org_location:
            return _kiosk_error("location_not_found", "Location not found", status.HTTP_404_NOT_FOUND)
        if not getattr(org_location, "is_face_attendance_enabled", False):
            return _kiosk_error("face_attendance_disabled", "Face attendance is disabled for this location")

        # Kiosk rule: geofence is mandatory.
        try:
            lat = float(request.data.get("latitude"))
            lon = float(request.data.get("longitude"))
        except (TypeError, ValueError):
            return _kiosk_error("geofence_required", "latitude and longitude are required")

        # Multi-site proximity validation
        matched_site, dist = get_site_within_proximity(lat, lon, org_location.id)
        if not matched_site:
            return _kiosk_error(
                "outside_geofence",
                "You are not within the authorized boundary of any site for this location.",
            )

        image_file = (
            request.FILES.get("image")
            or request.FILES.get("checkin_image")
            or request.FILES.get("checkout_image")
        )
        raw_bytes = image_file.read() if image_file else None
        if not raw_bytes:
            return _kiosk_error("face_image_required", "Face image is required")

        matched_user_id, face_code, _face_distance = identify_user_in_location(
            str(org_location.id),
            raw_bytes,
        )
        if not matched_user_id:
            if face_code == "face_not_detected":
                return _kiosk_error("face_not_detected", "No face found in uploaded image")
            if face_code == "no_enrolled_faces":
                return _kiosk_error("no_enrolled_faces", "No enrolled face users found for this location")
            return _kiosk_error("face_not_matched", "Face does not match any enrolled user")

        user = User.objects.filter(id=matched_user_id, is_deleted=False, is_active=True).first()
        if not user:
            return _kiosk_error("matched_user_not_found", "Matched user not found", status.HTTP_404_NOT_FOUND)
        if str(getattr(user, "location_id", "")) != str(org_location.id):
            return _kiosk_error(
                "matched_user_location_mismatch",
                "Matched user does not belong to kiosk location",
                status.HTTP_403_FORBIDDEN,
            )
        greeting_name = (
            (getattr(user, "name", "") or "").strip()
            or (getattr(user, "email", "") or "").strip()
            or "User"
        )
        def _fmt_time_user(dt):
            if not dt:
                return None
            return to_user_timezone(dt, user_tz).strftime("%I:%M %p")
        def _fmt_duration(total_minutes):
            if total_minutes is None:
                return None
            m = int(total_minutes)
            h = m // 60
            rem = m % 60
            if h <= 0:
                return f"{rem} {'minute' if rem == 1 else 'minutes'}"
            return f"{h} {'hour' if h == 1 else 'hours'} {rem} {'minute' if rem == 1 else 'minutes'}"
        def _fmt_offset_minutes(total_minutes):
            if total_minutes is None:
                return None
            m = max(0, int(total_minutes))
            return _fmt_duration(m)

        user_tz = get_user_timezone(user)
        kiosk_loc_id = getattr(org_location, "id", None)
        assignment, shift_start_date = self.get_today_assignment_v2(
            user, request=None, location_id=kiosk_loc_id
        )
        if not assignment:
            gate_response = _kiosk_shift_gate_response(
                user, org_location, greeting_name, user_tz
            )
            if gate_response is not None:
                return gate_response
            return Response(
                {
                    "success": False,
                    "code": "no_shift_today",
                    "message": f"Hi {greeting_name}, no shifts are assigned for today.",
                    "user_id": str(user.id),
                    "has_shift": False,
                },
                status=status.HTTP_200_OK,
            )

        shift = assignment.shift
        search_start_utc, search_end_utc, shift_start_dt_user, shift_end_dt_user, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
        )
        shift_window_text = (
            f"Your shift time is {shift_start_dt_user.strftime('%I:%M %p')} to {shift_end_dt_user.strftime('%I:%M %p')}."
        )

        img_name = "kiosk.jpg"
        if image_file:
            img_name = getattr(image_file, "name", img_name) or img_name

        if getattr(settings, "FACE_KIOSK_FAST_PATH", True):
            from patrol_backend.utils.kiosk_attendance_fast import (
                CheckoutTooEarly,
                build_kiosk_light_payload,
                defer_attendance_v3_refresh,
                kiosk_apply_punch,
            )

            defer_refresh = getattr(settings, "FACE_KIOSK_DEFER_METRICS_REFRESH", False)
            try:
                action_mode, attendance, log, status_code = kiosk_apply_punch(
                    user=user,
                    assignment=assignment,
                    shift=shift,
                    org_location=org_location,
                    shift_start_date=shift_start_date,
                    matched_site=matched_site,
                    lat=lat,
                    lon=lon,
                    raw_bytes=raw_bytes,
                    img_name=img_name,
                    search_start_utc=search_start_utc,
                    search_end_utc=search_end_utc,
                    user_tz=user_tz,
                    refresh_fn=None if defer_refresh else _attendance_v3_refresh_saved_fields,
                )
            except CheckoutTooEarly as exc:
                return _kiosk_error(
                    "checkout_too_early",
                    f"Checkout allowed after {exc.min_checkout_minutes} minutes from checkin",
                    extra={
                        "user_id": str(user.id),
                        "has_shift": True,
                        "min_checkout_minutes": exc.min_checkout_minutes,
                        "remaining_seconds": exc.remaining,
                    },
                )

            if defer_refresh:
                defer_attendance_v3_refresh(
                    _attendance_v3_refresh_saved_fields,
                    attendance,
                    user,
                    assignment,
                    shift,
                    org_location,
                    search_start_utc,
                    search_end_utc,
                )

            event_user = to_user_timezone(log.timestamp, user_tz)
            if action_mode == "checkin":
                checkin_count_in_shift = int(attendance.checkin_count or 0)
                if checkin_count_in_shift > 1:
                    voice_text = f"Hi {greeting_name}. Your check-in is marked successfully."
                else:
                    late_min = int((event_user - shift_start_dt_user).total_seconds() // 60)
                    if late_min > 0:
                        late_text = _fmt_offset_minutes(late_min)
                        voice_text = (
                            f"Hi {greeting_name}. Check-in marked at {event_user.strftime('%I:%M %p')}. "
                            f"{shift_window_text} You are late by {late_text}."
                        )
                    else:
                        voice_text = (
                            f"Hi {greeting_name}. Check-in marked at {event_user.strftime('%I:%M %p')}. "
                            f"{shift_window_text}"
                        )
            else:
                early_min = int((shift_end_dt_user - event_user).total_seconds() // 60)
                if early_min > 0:
                    early_text = _fmt_offset_minutes(early_min)
                    voice_text = (
                        f"Hi {greeting_name}. Checkout marked at {event_user.strftime('%I:%M %p')}. "
                        f"{shift_window_text} You checked out {early_text} early."
                    )
                else:
                    voice_text = (
                        f"Hi {greeting_name}. Checkout marked at {event_user.strftime('%I:%M %p')}. "
                        f"{shift_window_text}"
                    )

            if getattr(settings, "FACE_KIOSK_LIGHT_RESPONSE", True):
                payload = build_kiosk_light_payload(
                    attendance,
                    action_mode=action_mode,
                    user_id=str(user.id),
                    request=request,
                )
            else:
                payload = AttendanceCheckinSerializer(attendance, context={"request": request}).data
                if isinstance(payload, dict):
                    payload["kiosk_mode"] = True
                    payload["mode"] = action_mode
                    payload["face_attendance"] = True
                    payload["face_verified"] = True
                    payload["has_shift"] = True
                    payload["user_id"] = str(user.id)

            return Response(
                {
                    "success": True,
                    "code": f"{action_mode}_success",
                    "message": voice_text,
                    "user_id": str(user.id),
                    "has_shift": True,
                    "data": payload,
                },
                status=status_code,
            )

        # Legacy slow path (kept for rollback via FACE_KIOSK_FAST_PATH=False)
        latest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
        ).order_by("-timestamp").first()
        latest_checkout = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
        ).order_by("-timestamp").first()
        has_open_session = bool(
            latest_checkin and (not latest_checkout or latest_checkin.timestamp > latest_checkout.timestamp)
        )

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={"created_on": timezone.now(), "shift_date": shift_start_date},
        )
        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_date=shift_start_date,
        ).first() or attendance

        action_mode = "checkin"
        if has_open_session:
            action_mode = "checkout"
            min_checkout_minutes = 5
            elapsed_seconds = int((timezone.now() - latest_checkin.timestamp).total_seconds())
            if elapsed_seconds < (min_checkout_minutes * 60):
                remaining = max(0, (min_checkout_minutes * 60) - elapsed_seconds)
                return _kiosk_error(
                    "checkout_too_early",
                    f"Checkout allowed after {min_checkout_minutes} minutes from checkin",
                    extra={
                        "user_id": str(user.id),
                        "has_shift": True,
                        "min_checkout_minutes": min_checkout_minutes,
                        "remaining_seconds": remaining,
                    },
                )

        log = CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type=action_mode,
            latitude=lat,
            longitude=lon,
            site=matched_site,
        )
        log.image.save(img_name, ContentFile(raw_bytes), save=True)

        if action_mode == "checkin":
            earliest_checkin = CheckInLog.objects.filter(
                guard=user,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkin",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("timestamp").first()
            if earliest_checkin:
                attendance.checkin_time = earliest_checkin.timestamp
                attendance.latitude = earliest_checkin.latitude
                attendance.longitude = earliest_checkin.longitude
            attendance.shift_date = shift_start_date
            attendance.site = matched_site
            attendance.checkin_image.save(img_name, ContentFile(raw_bytes), save=False)
            attendance.save()
            status_code = status.HTTP_201_CREATED
        else:
            latest_checkout = CheckInLog.objects.filter(
                guard=user,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkout",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("-timestamp").first()
            if latest_checkout:
                attendance.checkout_time = latest_checkout.timestamp
            attendance.shift_date = shift_start_date
            attendance.site = matched_site
            attendance.checkout_image.save(img_name, ContentFile(raw_bytes), save=False)
            attendance.save()
            status_code = status.HTTP_200_OK

        _attendance_v3_refresh_saved_fields(
            attendance, user, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        payload = AttendanceCheckinSerializer(attendance, context={"request": request}).data
        voice_text = ""
        if action_mode == "checkin":
            first_checkin = CheckInLog.objects.filter(
                guard=user,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkin",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("timestamp").first()
            checkin_count_in_shift = int(getattr(attendance, "checkin_count", 0) or 0)
            if checkin_count_in_shift > 1:
                voice_text = f"Hi {greeting_name}. Your check-in is marked successfully."
            elif first_checkin:
                first_checkin_user = to_user_timezone(first_checkin.timestamp, user_tz)
                late_min = int((first_checkin_user - shift_start_dt_user).total_seconds() // 60)
                if late_min > 0:
                    late_text = _fmt_offset_minutes(late_min)
                    voice_text = (
                        f"Hi {greeting_name}. Check-in marked at {first_checkin_user.strftime('%I:%M %p')}. "
                        f"{shift_window_text} You are late by {late_text}."
                    )
                else:
                    voice_text = (
                        f"Hi {greeting_name}. Check-in marked at {first_checkin_user.strftime('%I:%M %p')}. "
                        f"{shift_window_text}"
                    )
        else:
            last_checkout = CheckInLog.objects.filter(
                guard=user,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkout",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("-timestamp").first()
            duration_text = _fmt_duration(getattr(attendance, "duration_minutes", None))
            if last_checkout:
                checkout_user = to_user_timezone(last_checkout.timestamp, user_tz)
                early_min = int((shift_end_dt_user - checkout_user).total_seconds() // 60)
                if early_min > 0:
                    early_text = _fmt_offset_minutes(early_min)
                    voice_text = (
                        f"Hi {greeting_name}. Checkout marked at {checkout_user.strftime('%I:%M %p')}. "
                        f"{shift_window_text} You checked out {early_text} early."
                    )
                else:
                    voice_text = (
                        f"Hi {greeting_name}. Checkout marked at {checkout_user.strftime('%I:%M %p')}. "
                        f"{shift_window_text}"
                    )
                if duration_text:
                    voice_text += f" Total worked duration is {duration_text}."
            else:
                voice_text = f"Hi {greeting_name}. Checkout marked successfully. {shift_window_text}"

        if not voice_text:
            voice_text = (
                f"Hi {greeting_name}, your check-in is marked successfully."
                if action_mode == "checkin"
                else f"Hi {greeting_name}, your checkout is marked successfully."
            )
        if isinstance(payload, dict):
            payload["kiosk_mode"] = True
            payload["mode"] = action_mode
            payload["face_attendance"] = True
            payload["face_verified"] = True
            payload["has_shift"] = True
            payload["user_id"] = str(user.id)

        return Response(
            {
                "success": True,
                "code": f"{action_mode}_success",
                "message": voice_text,
                "user_id": str(user.id),
                "has_shift": True,
                "data": payload,
            },
            status=status_code,
        )

    @action(detail=False, methods=["post"], url_path="force-checkout_v3")
    @parser_classes([MultiPartParser, FormParser])
    def force_checkout_v3(self, request):
        """
        Manual checkout by org admin/SO/FO when guard forgot to checkout.

        Rules:
        1) If checkout_time provided -> validate and use it.
        2) Else use shift end + grace for that shift instance.
        3) Else fallback to now only when it still passes validations.
        """
        actor = request.user
        actor_role = getattr(actor, "role", None)
        allowed_roles = {"admin", "so", "fo"}
        if not (getattr(actor, "is_superuser", False) or actor_role in allowed_roles):
            return Response(
                {"error": "Only admin/SO/FO can force checkout"},
                status=status.HTTP_403_FORBIDDEN,
            )

        guard_id = request.data.get("guard_id")
        attendance_id = request.data.get("attendance_id")
        attendance = None
        guard = None
        assignment = None
        shift = None
        org_location = None

        if attendance_id:
            attendance = AttendanceCheckin.objects.select_related(
                "guard", "assignment", "shift", "org_location"
            ).filter(id=attendance_id).first()
            if not attendance:
                return Response({"error": "Attendance record not found"}, status=status.HTTP_404_NOT_FOUND)
            guard = attendance.guard
            assignment = attendance.assignment
            shift = attendance.shift
            org_location = attendance.org_location
            if guard_id and str(guard.id) != str(guard_id):
                return Response({"error": "guard_id does not match attendance_id"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            if not guard_id:
                return Response({"error": "guard_id or attendance_id is required"}, status=status.HTTP_400_BAD_REQUEST)
            try:
                guard = User.objects.get(id=guard_id)
            except User.DoesNotExist:
                return Response({"error": "Guard not found"}, status=status.HTTP_404_NOT_FOUND)
            assignment, shift_start_date = self.get_today_assignment_v2(guard, request)
            if not assignment:
                return Response(
                    {"error": "No active assignment found for this guard"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            shift = assignment.shift
            org_location = assignment.location
            attendance = AttendanceCheckin.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
            ).order_by("-shift_date", "-created_on").first()

        if not getattr(actor, "is_superuser", False):
            if str(getattr(actor, "location_id", "")) != str(getattr(guard, "location_id", "")):
                return Response(
                    {"error": "You can only force checkout guards in your organization"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        guard_tz = get_user_timezone_from_request(
            request,
            location_id=getattr(guard, "location_id", None),
        )

        # latitude/longitude are optional for manual checkout, fallback to assignment location coords.
        lat_raw = request.data.get("latitude", org_location.latitude if org_location else None)
        lon_raw = request.data.get("longitude", org_location.longitude if org_location else None)
        if lat_raw is None or lon_raw is None:
            return Response(
                {"error": "latitude and longitude are required (or location must have coordinates)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            lat = float(lat_raw)
            lon = float(lon_raw)
        except (TypeError, ValueError):
            return Response({"error": "Invalid latitude/longitude"}, status=status.HTTP_400_BAD_REQUEST)

        if attendance and attendance.shift_date:
            shift_start_date = attendance.shift_date
        elif attendance and attendance.checkin_time:
            shift_start_date = _attendance_v3_compute_shift_date(
                to_user_timezone(attendance.checkin_time, guard_tz), shift
            )
        elif attendance:
            shift_start_date = to_user_timezone(attendance.created_on, guard_tz).date()
        else:
            # Fallback if attendance is unexpectedly missing.
            _, shift_start_date = self.get_today_assignment_v2(guard, request)

        search_start_utc, search_end_utc, _, shift_end_dt_user, grace_minutes = _attendance_v3_shift_window_utc(
            shift_start_date, shift, guard_tz, location_id=getattr(org_location, "id", None)
        )

        if not attendance or not attendance.checkin_time:
            return Response({"error": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        latest_checkin_log = CheckInLog.objects.filter(
            guard=guard,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
        ).order_by("-timestamp").first()
        latest_checkout_log = CheckInLog.objects.filter(
            guard=guard,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
        ).order_by("-timestamp").first()
        if not latest_checkin_log:
            return Response({"error": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)
        if latest_checkout_log and latest_checkout_log.timestamp >= latest_checkin_log.timestamp:
            return Response({"error": "Guard is already checked out"}, status=status.HTTP_400_BAD_REQUEST)

        latest_allowed_utc = (shift_end_dt_user + timedelta(minutes=grace_minutes)).astimezone(pytz.UTC)

        checkout_time_str = request.data.get("checkout_time")
        manual_time_provided = bool(checkout_time_str)
        selected_checkout_utc = None

        if manual_time_provided:
            try:
                s = str(checkout_time_str).strip().replace("Z", "+00:00")
                parsed = datetime.fromisoformat(s)
                if parsed.tzinfo is None:
                    parsed = guard_tz.localize(parsed)
                selected_checkout_utc = parsed.astimezone(pytz.UTC)
            except Exception:
                return Response(
                    {"error": "Invalid checkout_time. Use ISO format (e.g. 2026-03-24T06:55:00+05:30)"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            # Auto mode: use shift end + grace first.
            selected_checkout_utc = latest_allowed_utc

        open_checkin_time = latest_checkin_log.timestamp
        if selected_checkout_utc < open_checkin_time:
            if manual_time_provided:
                return Response(
                    {"error": "checkout_time cannot be earlier than last checkin_time"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            # Last fallback to now (only if still valid).
            now_utc = timezone.now()
            if now_utc >= open_checkin_time and now_utc <= latest_allowed_utc:
                selected_checkout_utc = now_utc
            else:
                return Response(
                    {"error": "Unable to derive a valid checkout time"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if selected_checkout_utc > latest_allowed_utc:
            return Response(
                {"error": "checkout_time is outside the allowed shift window"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        image_file = request.FILES.get("image") or request.FILES.get("checkout_image")
        raw_bytes = None
        img_name = "checkout.jpg"
        if image_file:
            img_name = getattr(image_file, "name", img_name) or img_name
            raw_bytes = image_file.read()

        log = CheckInLog.objects.create(
            guard=guard,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            latitude=lat,
            longitude=lon,
        )
        # Force log timestamp to selected manual/auto checkout time.
        CheckInLog.objects.filter(id=log.id).update(timestamp=selected_checkout_utc)

        if raw_bytes is not None:
            log.image.save(img_name, ContentFile(raw_bytes), save=True)

        attendance.checkout_time = selected_checkout_utc
        attendance.shift_date = shift_start_date
        if raw_bytes is not None:
            attendance.checkout_image.save(img_name, ContentFile(raw_bytes), save=False)
        attendance.save()

        _attendance_v3_refresh_saved_fields(
            attendance, guard, assignment, shift, org_location, search_start_utc, search_end_utc
        )

        response_data = AttendanceCheckinSerializer(attendance, context={"request": request}).data
        response_data.update({
            "manual_checkout": True,
            "checked_out_by": str(actor.id),
            "checked_out_by_role": actor_role,
            "auto_checkout_time_used": not manual_time_provided,
        })
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="edit-boundary_v3")
    def edit_boundary_v3(self, request):
        """
        Edit first check-in and/or last checkout boundary for a closed v3 session.
        Requires reason and admin/SO/FO role.
        """
        actor = request.user
        actor_role = getattr(actor, "role", None)
        allowed_roles = {"admin", "so", "fo"}
        if not (getattr(actor, "is_superuser", False) or actor_role in allowed_roles):
            return Response({"error": "Only admin/SO/FO can edit boundaries"}, status=status.HTTP_403_FORBIDDEN)

        serializer = AttendanceBoundaryEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        attendance = AttendanceCheckin.objects.select_related(
            "guard", "assignment", "shift", "org_location"
        ).filter(id=data["attendance_id"]).first()
        if not attendance:
            return Response({"error": "Attendance record not found"}, status=status.HTTP_404_NOT_FOUND)

        if not getattr(actor, "is_superuser", False):
            if str(getattr(actor, "location_id", "")) != str(getattr(attendance.guard, "location_id", "")):
                return Response(
                    {"error": "You can only edit guards in your organization"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        guard = attendance.guard
        assignment = attendance.assignment
        shift = attendance.shift
        org_location = attendance.org_location

        guard_tz = get_user_timezone_from_request(request, location_id=getattr(guard, "location_id", None))
        if attendance.shift_date:
            shift_start_date = attendance.shift_date
        elif attendance.checkin_time:
            shift_start_date = _attendance_v3_compute_shift_date(to_user_timezone(attendance.checkin_time, guard_tz), shift)
        else:
            shift_start_date = to_user_timezone(attendance.created_on, guard_tz).date()

        search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
            shift_start_date, shift, guard_tz, location_id=getattr(org_location, "id", None)
        )

        logs = list(
            CheckInLog.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("timestamp")
        )
        if not logs:
            return Response({"error": "No checkin/checkout logs found in shift window"}, status=status.HTTP_400_BAD_REQUEST)

        checkins = [l for l in logs if l.type == "checkin"]
        checkouts = [l for l in logs if l.type == "checkout"]
        if not checkins or not checkouts:
            return Response({"error": "Cannot edit boundaries for incomplete session"}, status=status.HTTP_400_BAD_REQUEST)
        if len(checkins) != len(checkouts):
            return Response({"error": "Cannot edit while session is open (checkin/checkout count mismatch)"}, status=status.HTTP_400_BAD_REQUEST)
        if checkouts[-1].timestamp < checkins[-1].timestamp:
            return Response({"error": "Cannot edit while latest session is still open"}, status=status.HTTP_400_BAD_REQUEST)

        def _parse_to_utc(raw_value, label):
            if raw_value in (None, ""):
                return None
            try:
                s = str(raw_value).strip().replace("Z", "+00:00")
                parsed = datetime.fromisoformat(s)
                if parsed.tzinfo is None:
                    parsed = guard_tz.localize(parsed)
                out = parsed.astimezone(pytz.UTC)
            except Exception:
                raise ValueError(f"Invalid {label}. Use ISO datetime format.")
            if out < search_start_utc or out >= search_end_utc:
                raise ValueError(f"{label} must be inside the shift window.")
            return out

        try:
            new_first_checkin_utc = _parse_to_utc(data.get("first_checkin_time"), "first_checkin_time")
            new_last_checkout_utc = _parse_to_utc(data.get("last_checkout_time"), "last_checkout_time")
        except ValueError as ex:
            return Response({"error": str(ex)}, status=status.HTTP_400_BAD_REQUEST)

        first_checkin_log = checkins[0]
        last_checkout_log = checkouts[-1]
        final_first = new_first_checkin_utc or first_checkin_log.timestamp
        final_last = new_last_checkout_utc or last_checkout_log.timestamp
        if final_last < final_first:
            return Response(
                {"error": "last_checkout_time cannot be earlier than first_checkin_time"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            if new_first_checkin_utc:
                CheckInLog.objects.filter(id=first_checkin_log.id).update(timestamp=new_first_checkin_utc)
            if new_last_checkout_utc:
                CheckInLog.objects.filter(id=last_checkout_log.id).update(timestamp=new_last_checkout_utc)

            refreshed_first = CheckInLog.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkin",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("timestamp").first()
            refreshed_last = CheckInLog.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkout",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).order_by("-timestamp").first()

            attendance.checkin_time = refreshed_first.timestamp if refreshed_first else attendance.checkin_time
            attendance.checkout_time = refreshed_last.timestamp if refreshed_last else attendance.checkout_time
            attendance.shift_date = shift_start_date
            attendance.edited_by = actor
            attendance.edited_on = timezone.now()
            attendance.edit_reason = data["reason"]
            attendance.save()

            _attendance_v3_refresh_saved_fields(
                attendance, guard, assignment, shift, org_location, search_start_utc, search_end_utc
            )

        response_data = AttendanceCheckinSerializer(attendance, context={"request": request}).data
        response_data.update({
            "edited": True,
            "edited_by": str(actor.id),
            "edited_by_role": actor_role,
            "edit_reason": data["reason"],
        })
        return Response(response_data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def list_default_shifts(self, request):
        """
        List default shifts for guard's location.
        Only returns shifts if guard has no shift for today.
        Query params: guard_id
        """
        guard_id = request.query_params.get("guard_id")
        
        if not guard_id:
            return Response(
                {"error": "guard_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            guard = User.objects.get(id=guard_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Guard not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Use same helper as shift_today_v2/checkin_v2/checkout_v2 for consistency.
        active_assignment, _ = self.get_today_assignment_v2(guard, request)
        if active_assignment:
            return Response(
                {"message": "Guard already has a shift for today", "has_shift": True},
                status=status.HTTP_200_OK
            )
        
        # Get guard's location
        if not guard.location:
            return Response(
                {"error": "Guard does not have an assigned location"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get default shifts for guard's location
        default_shifts = Shift.objects.filter(
            is_default=True,
            is_deleted=False,
            location=guard.location
        ).select_related('checkpoint_template', 'location')
        
        # Serialize shifts
        shifts_data = []
        for shift in default_shifts:
            shifts_data.append({
                "id": str(shift.id),
                "name": shift.name,
                "start_time": shift.start_time.strftime("%H:%M:%S"),
                "end_time": shift.end_time.strftime("%H:%M:%S"),
                "location_id": str(shift.location.id) if shift.location else None,
                "location_name": shift.location.name if shift.location else None,
                "checkpoint_template_id": str(shift.checkpoint_template.id) if shift.checkpoint_template else None,
                "checkpoint_template_name": shift.checkpoint_template.template_name if shift.checkpoint_template else None,
                "is_overnight": shift.end_time <= shift.start_time
            })
        
        return Response({
            "has_shift": False,
            "default_shifts": shifts_data,
            "location_id": str(guard.location.id),
            "location_name": guard.location.name
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def create_assignment(self, request):
        """
        Create assignment for guard with specified shift.
        Only creates if guard has no shift for today.
        Body: guard_id, shift_id, checkpoint_template_id (optional)
        """
        guard_id = request.data.get("guard_id")
        shift_id = request.data.get("shift_id")
        checkpoint_template_id = request.data.get("checkpoint_template_id")
        
        if not guard_id or not shift_id:
            return Response(
                {"error": "guard_id and shift_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            guard = User.objects.get(id=guard_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Guard not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            shift = Shift.objects.get(id=shift_id, is_deleted=False)
        except Shift.DoesNotExist:
            return Response(
                {"error": "Shift not found or deleted"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        user_tz = get_user_timezone_from_request(request)
        today = get_user_today(user_tz)

        # Use same helper as shift_today_v2/checkin_v2/checkout_v2 for consistency.
        active_assignment, _ = self.get_today_assignment_v2(guard, request)
        if active_assignment:
            return Response(
                {"error": "Guard already has a shift for today", "has_shift": True},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get checkpoints only from selected checkpoint template (if provided).
        # If no checkpoint_template_id is sent, create assignment without checkpoints.
        checkpoints = []
        selected_template = None
        if checkpoint_template_id:
            try:
                selected_template = CheckpointTemplate.objects.get(
                    id=checkpoint_template_id,
                    is_deleted=False
                )
            except CheckpointTemplate.DoesNotExist:
                return Response(
                    {"error": "Checkpoint template not found"},
                    status=status.HTTP_404_NOT_FOUND
                )

            if selected_template.location_id != shift.location_id:
                return Response(
                    {"error": "Checkpoint template location does not match shift location"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            if selected_template.shift_id != shift.id:
                return Response(
                    {"error": "Checkpoint template shift does not match selected shift"},
                    status=status.HTTP_400_BAD_REQUEST
                )

        template_to_use = selected_template
        if template_to_use:
            template_checkpoints = template_to_use.checkpoints or []
            if not isinstance(template_checkpoints, list):
                return Response(
                    {"error": "Checkpoint template checkpoints must be a list"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            checkpoints = template_checkpoints  # Format: [{"checkpoint_id": "uuid", "time": "HH:MM"}]
        
        # Determine assignment date range
        # Even for overnight shifts, the assignment is conceptually bound to a single logical day.
        # The get_today_assignment_v2 API will still fetch it correctly tomorrow morning
        # because it specifically queries `end_date__gte=yesterday`.
        start_date = today
        end_date = today
        
        # Create assignment
        assignment = Assignment.objects.create(
            guard=guard,
            location=shift.location,
            shift=shift,
            start_date=start_date,
            end_date=end_date,
            checkpoints=checkpoints
        )
        
        return Response({
            "message": "Assignment created successfully",
            "assignment_id": str(assignment.id),
            "guard_id": str(guard.id),
            "guard_name": guard.name if hasattr(guard, 'name') else guard.username,
            "shift_id": str(shift.id),
            "shift_name": shift.name,
            "checkpoint_template_id": str(template_to_use.id) if template_to_use else None,
            "checkpoint_template_name": template_to_use.template_name if template_to_use else None,
            "location_id": str(shift.location.id) if shift.location else None,
            "location_name": shift.location.name if shift.location else None,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "checkpoints_count": len(checkpoints)
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["get"], url_path="bulk-entry-candidates")
    def bulk_entry_candidates(self, request):
        """
        List attendance exception candidates for selected date and scoped location:
        1) no_shift
        2) shift_no_checkin
        """
        scoped_location_id, error_response = self._resolve_scope_location_id(request)
        if error_response:
            return error_response

        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "date is required (YYYY-MM-DD)"}, status=status.HTTP_400_BAD_REQUEST)

        target_date = parse_date(date_str)
        if not target_date:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        search = (request.query_params.get("search") or "").strip()
        role_filter = (request.query_params.get("role") or "").strip().lower()
        user_tz = get_user_timezone_from_request(request, location_id=scoped_location_id)

        users_qs = User.objects.filter(
            is_deleted=False,
            is_active=True,
            location_id=scoped_location_id,
        ).exclude(
            Q(role__iexact="admin") | Q(is_superuser=True)
        )
        if role_filter and role_filter not in ("all",):
            users_qs = users_qs.filter(role__iexact=role_filter)
        if search:
            users_qs = users_qs.filter(
                Q(name__icontains=search) |
                Q(employee_code__icontains=search) |
                Q(email__icontains=search)
            )
        users = list(users_qs.order_by("name", "employee_code"))
        user_ids = [u.id for u in users]

        assignments = list(
            Assignment.objects.select_related("shift", "location").filter(
                guard_id__in=user_ids,
                start_date__lte=target_date,
                end_date__gte=target_date,
                is_deleted=False,
            ).order_by("-modified_on")
        )
        assignment_by_guard = {}
        for assgn in assignments:
            assignment_by_guard.setdefault(str(assgn.guard_id), assgn)

        no_shift = []
        shift_no_checkin = []
        weekoff = []
        weekoff_user_ids = set(
            str(uid)
            for uid in AttendanceWeekOff.objects.filter(
                location_id=scoped_location_id,
                weekoff_date=target_date,
                user_id__in=user_ids,
            ).values_list("user_id", flat=True)
        )
        for guard in users:
            assgn = assignment_by_guard.get(str(guard.id))
            base = {
                "user_id": str(guard.id),
                "name": guard.name,
                "employee_code": guard.employee_code or "",
                "role": (guard.role or "").strip(),
                "location_id": str(getattr(guard, "location_id", "") or ""),
            }
            if str(guard.id) in weekoff_user_ids:
                weekoff.append(base)
                continue
            if not assgn:
                no_shift.append(base)
                continue

            if not assgn.shift:
                no_shift.append(base)
                continue

            search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                target_date,
                assgn.shift,
                user_tz,
                location_id=getattr(assgn.location, "id", None),
            )
            has_checkin = CheckInLog.objects.filter(
                guard_id=guard.id,
                assignment_id=assgn.id,
                shift_id=assgn.shift_id,
                org_location_id=assgn.location_id,
                type="checkin",
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).exists()
            if not has_checkin:
                shift_no_checkin.append({
                    **base,
                    "assignment_id": str(assgn.id),
                    "shift_id": str(assgn.shift_id),
                    "shift_name": assgn.shift.name if assgn.shift else "",
                    "shift_end": assgn.shift.end_time.strftime("%H:%M:%S") if assgn.shift else None,
                })

        location_obj = Location.objects.filter(id=scoped_location_id, is_deleted=False).first()
        return Response(
            {
                "date": str(target_date),
                "location_id": str(scoped_location_id),
                "location_name": location_obj.name if location_obj else None,
                "summary": {
                    "total_users": len(users),
                    "weekoff_count": len(weekoff),
                    "no_shift_count": len(no_shift),
                    "shift_no_checkin_count": len(shift_no_checkin),
                },
                "weekoff": weekoff,
                "no_shift": no_shift,
                "shift_no_checkin": shift_no_checkin,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="v2/bulk-entry-candidates")
    def bulk_entry_candidates_v2(self, request):
        """
        MODERN V2: List attendance exception candidates for selected date and scoped location.
        Uses single-query pre-fetching for CheckInLogs to support 10,000+ users.
        """
        scoped_location_id, error_response = self._resolve_scope_location_id(request)
        if error_response:
            return error_response

        date_str = request.query_params.get("date")
        if not date_str:
            return Response({"error": "date is required (YYYY-MM-DD)"}, status=status.HTTP_400_BAD_REQUEST)

        target_date = parse_date(date_str)
        if not target_date:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        search = (request.query_params.get("search") or "").strip()
        role_filter = (request.query_params.get("role") or "").strip().lower()
        user_tz = get_user_timezone_from_request(request, location_id=scoped_location_id)

        users_qs = User.objects.filter(
            is_deleted=False,
            is_active=True,
            location_id=scoped_location_id,
        ).exclude(
            Q(role__iexact="admin") | Q(is_superuser=True)
        )
        if role_filter and role_filter not in ("all",):
            users_qs = users_qs.filter(role__iexact=role_filter)
        if search:
            users_qs = users_qs.filter(
                Q(name__icontains=search) |
                Q(employee_code__icontains=search) |
                Q(email__icontains=search)
            )
        users = list(users_qs.order_by("name", "employee_code"))
        user_ids = [u.id for u in users]
        if not user_ids:
            return Response({"no_shift": [], "shift_no_checkin": [], "weekoff": [], "summary": {"total_users": 0, "weekoff_count": 0, "no_shift_count": 0, "shift_no_checkin_count": 0}})

        # Pre-fetch assignments
        assignments_list = list(
            Assignment.objects.select_related("shift", "location").filter(
                guard_id__in=user_ids,
                start_date__lte=target_date,
                end_date__gte=target_date,
                is_deleted=False,
            ).order_by("-modified_on")
        )
        assignment_by_guard = {}
        for assgn in assignments_list:
            assignment_by_guard.setdefault(str(assgn.guard_id), assgn)

        # Pre-fetch weekoffs
        weekoff_user_ids = set(
            str(uid)
            for uid in AttendanceWeekOff.objects.filter(
                location_id=scoped_location_id,
                weekoff_date=target_date,
                user_id__in=user_ids,
            ).values_list("user_id", flat=True)
        )

        # Performance Optimization: Single broad query for CheckInLogs
        broad_start_utc = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=user_tz).astimezone(pytz.UTC) - timedelta(hours=6)
        broad_end_utc = datetime.combine(target_date, datetime.max.time()).replace(tzinfo=user_tz).astimezone(pytz.UTC) + timedelta(hours=6)
        
        logs_qs = CheckInLog.objects.filter(
            guard_id__in=user_ids,
            type="checkin",
            timestamp__gte=broad_start_utc,
            timestamp__lt=broad_end_utc
        ).values("guard_id", "assignment_id", "shift_id", "timestamp")
        
        logs_by_guard = defaultdict(list)
        for l in logs_qs:
            logs_by_guard[str(l["guard_id"])].append(l)

        no_shift = []
        shift_no_checkin = []
        weekoff = []

        for guard in users:
            uid_str = str(guard.id)
            base = {
                "user_id": uid_str,
                "name": guard.name,
                "employee_code": guard.employee_code or "",
                "role": (guard.role or "").strip().upper(),
                "location_id": str(getattr(guard, "location_id", "") or ""),
            }

            if uid_str in weekoff_user_ids:
                weekoff.append(base)
                continue

            assgn = assignment_by_guard.get(uid_str)
            if not assgn or not assgn.shift:
                no_shift.append(base)
                continue

            search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                target_date,
                assgn.shift,
                user_tz,
                location_id=getattr(assgn.location, "id", None),
            )

            guard_logs = logs_by_guard.get(uid_str, [])
            has_checkin = any(
                search_start_utc <= l["timestamp"] < search_end_utc and str(l["assignment_id"]) == str(assgn.id)
                for l in guard_logs
            )

            if not has_checkin:
                shift_no_checkin.append({
                    **base,
                    "assignment_id": str(assgn.id),
                    "shift_id": str(assgn.shift_id),
                    "shift_name": assgn.shift.name,
                    "shift_start": assgn.shift.start_time.strftime("%H:%M:%S") if assgn.shift else None,
                    "shift_end": assgn.shift.end_time.strftime("%H:%M:%S") if assgn.shift else None,
                })

        location_obj = Location.objects.filter(id=scoped_location_id, is_deleted=False).first()
        return Response({
            "date": str(target_date),
            "location_id": str(scoped_location_id),
            "location_name": location_obj.name if location_obj else None,
            "summary": {
                "total_users": len(users),
                "weekoff_count": len(weekoff),
                "no_shift_count": len(no_shift),
                "shift_no_checkin_count": len(shift_no_checkin),
            },
            "no_shift": no_shift,
            "shift_no_checkin": shift_no_checkin,
            "weekoff": weekoff,
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"], url_path="bulk-entry")
    def bulk_entry(self, request):
        """
        Bulk attendance entry in one flow:
        - ensure assignment (using selected shift)
        - create checkin log
        - create checkout log
        - refresh master attendance summary
        """
        scoped_location_id, error_response = self._resolve_scope_location_id(request)
        if error_response:
            return error_response

        date_str = request.data.get("date")
        user_ids = request.data.get("user_ids")
        shift_id = request.data.get("shift_id")
        checkin_time_raw = request.data.get("checkin_time")
        checkout_time_raw = request.data.get("checkout_time")
        reason = (request.data.get("reason") or "").strip()
        latitude = request.data.get("latitude")
        longitude = request.data.get("longitude")
        site_id = request.data.get("site_id")

        if not date_str:
            return Response({"error": "date is required (YYYY-MM-DD)"}, status=status.HTTP_400_BAD_REQUEST)
        target_date = parse_date(str(date_str))
        if not target_date:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(user_ids, list) or not user_ids:
            return Response({"error": "user_ids must be a non-empty list"}, status=status.HTTP_400_BAD_REQUEST)
        if not shift_id:
            return Response({"error": "shift_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not reason:
            return Response({"error": "reason is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            shift = Shift.objects.select_related("location", "checkpoint_template").get(
                id=shift_id,
                is_deleted=False,
            )
        except Shift.DoesNotExist:
            return Response({"error": "Shift not found"}, status=status.HTTP_404_NOT_FOUND)

        if str(getattr(shift, "location_id", "")) != str(scoped_location_id):
            return Response(
                {"error": "Selected shift does not belong to the scoped location"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_tz = get_user_timezone_from_request(request, location_id=scoped_location_id)
        try:
            checkin_utc = self._parse_bulk_datetime_to_utc(checkin_time_raw, user_tz, "checkin_time")
            checkout_utc = self._parse_bulk_datetime_to_utc(checkout_time_raw, user_tz, "checkout_time")
        except ValueError as ex:
            return Response({"error": str(ex)}, status=status.HTTP_400_BAD_REQUEST)

        if checkout_utc <= checkin_utc:
            return Response(
                {"error": "checkout_time must be later than checkin_time"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        checkin_local = to_user_timezone(checkin_utc, user_tz)
        checkout_local = to_user_timezone(checkout_utc, user_tz)
        is_overnight_shift = bool(shift.end_time <= shift.start_time)

        # Strict date guardrails for bulk entry:
        # - normal shift: checkin + checkout must be on selected date
        # - overnight shift: checkin on selected date, checkout on selected/next date
        if checkin_local.date() != target_date:
            return Response(
                {"error": "For bulk entry, checkin_time date must match selected date"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not is_overnight_shift:
            if checkout_local.date() != target_date:
                return Response(
                    {"error": "For normal shift, checkout_time date must match selected date"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
        else:
            allowed_checkout_dates = {target_date, target_date + timedelta(days=1)}
            if checkout_local.date() not in allowed_checkout_dates:
                return Response(
                    {"error": "For overnight shift, checkout_time must be on selected date or next date"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        if latitude is None or longitude is None:
            if shift.location and shift.location.latitude is not None and shift.location.longitude is not None:
                latitude = shift.location.latitude
                longitude = shift.location.longitude
            else:
                latitude, longitude = 0, 0

        allowed_users = {
            str(u.id): u for u in User.objects.filter(
                id__in=user_ids,
                is_deleted=False,
                is_active=True,
                location_id=scoped_location_id,
            ).exclude(Q(role__iexact="admin") | Q(is_superuser=True))
        }

        success = []
        failed = []
        actor = request.user
        for user_id in user_ids:
            guard = allowed_users.get(str(user_id))
            if not guard:
                failed.append({
                    "user_id": str(user_id),
                    "error": "User not found in scoped location or not eligible",
                })
                continue

            try:
                with transaction.atomic():
                    assignment = Assignment.objects.filter(
                        guard_id=guard.id,
                        start_date__lte=target_date,
                        end_date__gte=target_date,
                        is_deleted=False,
                    ).order_by("-modified_on").first()
                    if assignment:
                        assignment.shift = shift
                        assignment.location = shift.location
                        # Bulk attendance entry is shift-only overwrite. Always clear
                        # existing assignment checkpoints to avoid carrying old shift checkpoints.
                        assignment.checkpoints = []
                        assignment.modified_by = actor
                        assignment.save()
                    else:
                        assignment = Assignment.objects.create(
                            guard=guard,
                            location=shift.location,
                            shift=shift,
                            start_date=target_date,
                            end_date=target_date,
                            created_by=actor,
                            modified_by=actor,
                        )

                    # When bulk-assigning shift/checkin for a weekoff guard, clear that day's weekoff.
                    AttendanceWeekOff.objects.filter(
                        user_id=guard.id,
                        location_id=scoped_location_id,
                        weekoff_date=target_date,
                    ).delete()

                    search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                        target_date, shift, user_tz, location_id=getattr(shift.location, "id", None)
                    )
                    existing_checkin = CheckInLog.objects.filter(
                        guard=guard,
                        assignment=assignment,
                        shift=shift,
                        org_location=shift.location,
                        type="checkin",
                        timestamp__gte=search_start_utc,
                        timestamp__lt=search_end_utc,
                    ).exists()
                    if existing_checkin:
                        raise ValueError("Check-in already exists in selected shift window")

                    if checkin_utc < search_start_utc or checkin_utc >= search_end_utc:
                        raise ValueError("checkin_time is outside selected shift window")
                    if checkout_utc < search_start_utc or checkout_utc >= search_end_utc:
                        raise ValueError("checkout_time is outside selected shift window")

                    checkin_log = CheckInLog.objects.create(
                        guard=guard,
                        assignment=assignment,
                        shift=shift,
                        org_location=shift.location,
                        type="checkin",
                        latitude=latitude,
                        longitude=longitude,
                        site_id=site_id,
                    )
                    CheckInLog.objects.filter(id=checkin_log.id).update(timestamp=checkin_utc)

                    checkout_log = CheckInLog.objects.create(
                        guard=guard,
                        assignment=assignment,
                        shift=shift,
                        org_location=shift.location,
                        type="checkout",
                        latitude=latitude,
                        longitude=longitude,
                        site_id=site_id,
                    )
                    CheckInLog.objects.filter(id=checkout_log.id).update(timestamp=checkout_utc)

                    attendance, _ = AttendanceCheckin.objects.get_or_create(
                        guard=guard,
                        assignment=assignment,
                        shift=shift,
                        org_location=shift.location,
                        shift_date=target_date,
                        defaults={"created_on": timezone.now()},
                    )
                    attendance.checkin_time = checkin_utc
                    attendance.checkout_time = checkout_utc
                    attendance.shift_date = target_date
                    if not attendance.site:
                        attendance.site_id = site_id
                    attendance.edited_by = actor
                    attendance.edited_on = timezone.now()
                    attendance.edit_reason = reason
                    attendance.save()

                    _attendance_v3_refresh_saved_fields(
                        attendance,
                        guard,
                        assignment,
                        shift,
                        shift.location,
                        search_start_utc,
                        search_end_utc,
                    )
                    success.append({
                        "user_id": str(guard.id),
                        "name": guard.name,
                        "employee_code": guard.employee_code or "",
                        "assignment_id": str(assignment.id),
                        "attendance_id": str(attendance.id),
                        "pa_status": attendance.pa_status,
                    })
            except Exception as ex:
                failed.append({
                    "user_id": str(user_id),
                    "error": str(ex),
                })

        return Response(
            {
                "date": str(target_date),
                "location_id": str(scoped_location_id),
                "shift_id": str(shift.id),
                "shift_name": shift.name,
                "reason": reason,
                "summary": {
                    "requested_count": len(user_ids),
                    "success_count": len(success),
                    "failed_count": len(failed),
                },
                "success": success,
                "failed": failed,
            },
            status=status.HTTP_200_OK if not failed else status.HTTP_207_MULTI_STATUS,
        )

    @action(detail=False, methods=["post"], url_path="bulk-weekoff")
    def bulk_weekoff(self, request):
        """
        Mark selected users as weekoff (W) for selected date + scoped location.
        """
        scoped_location_id, error_response = self._resolve_scope_location_id(request)
        if error_response:
            return error_response

        date_str = request.data.get("date")
        user_ids = request.data.get("user_ids")
        reason = (request.data.get("reason") or "").strip()
        site_id = request.data.get("site_id")

        if not date_str:
            return Response({"error": "date is required (YYYY-MM-DD)"}, status=status.HTTP_400_BAD_REQUEST)
        target_date = parse_date(str(date_str))
        if not target_date:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD"}, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(user_ids, list) or not user_ids:
            return Response({"error": "user_ids must be a non-empty list"}, status=status.HTTP_400_BAD_REQUEST)

        allowed_users = {
            str(u.id): u for u in User.objects.filter(
                id__in=user_ids,
                is_deleted=False,
                is_active=True,
                location_id=scoped_location_id,
            ).exclude(Q(role__iexact="admin") | Q(is_superuser=True))
        }

        success = []
        failed = []
        actor = request.user
        batch_id = uuid.uuid4()

        for user_id in user_ids:
            guard = allowed_users.get(str(user_id))
            if not guard:
                failed.append({
                    "user_id": str(user_id),
                    "error": "User not found in scoped location or not eligible",
                })
                continue
            try:
                with transaction.atomic():
                    obj, _created = AttendanceWeekOff.objects.update_or_create(
                        user_id=guard.id,
                        location_id=scoped_location_id,
                        weekoff_date=target_date,
                        defaults={
                            "mark": "W",
                            "source": "api",
                            "upload_batch_id": batch_id,
                            "created_by": actor,
                        },
                    )
                    success.append({
                        "user_id": str(guard.id),
                        "name": guard.name,
                        "employee_code": guard.employee_code or "",
                        "weekoff_id": str(obj.id),
                    })
            except Exception as ex:
                failed.append({
                    "user_id": str(user_id),
                    "error": str(ex),
                })

        return Response(
            {
                "date": str(target_date),
                "location_id": str(scoped_location_id),
                "reason": reason,
                "summary": {
                    "requested_count": len(user_ids),
                    "success_count": len(success),
                    "failed_count": len(failed),
                },
                "success": success,
                "failed": failed,
            },
            status=status.HTTP_200_OK if not failed else status.HTTP_207_MULTI_STATUS,
        )

    def _monthly_resolve_assignment_for_date(
        self,
        guard,
        target_date,
        location_id,
        actor=None,
        shift_id=None,
    ):
        assignment = (
            Assignment.objects.filter(
                guard_id=guard.id,
                location_id=location_id,
                start_date__lte=target_date,
                end_date__gte=target_date,
                is_deleted=False,
            )
            .select_related("shift", "location")
            .order_by("-modified_on")
            .first()
        )
        if not shift_id:
            return assignment

        try:
            shift = Shift.objects.select_related("location").get(
                id=shift_id,
                is_deleted=False,
            )
        except Shift.DoesNotExist:
            raise ValueError("Shift not found")

        if str(getattr(shift, "location_id", "")) != str(location_id):
            raise ValueError("Selected shift does not belong to the scoped location")

        if assignment:
            assignment.shift = shift
            assignment.location = shift.location
            assignment.checkpoints = []
            assignment.modified_by = actor
            assignment.save()
            return assignment

        return Assignment.objects.create(
            guard=guard,
            location=shift.location,
            shift=shift,
            start_date=target_date,
            end_date=target_date,
            checkpoints=[],
            created_by=actor,
            modified_by=actor,
        )

    def _monthly_cell_mark_present(
        self,
        request,
        guard,
        scoped_location_id,
        target_date,
        reason,
        shift_id=None,
        from_status="",
        site_id=None,
    ):
        user_tz = get_user_timezone_from_request(request, location_id=scoped_location_id)
        user_today = get_user_today(user_tz)
        if target_date > user_today:
            raise ValueError("Cannot mark present for a future date")

        actor = request.user
        if str(from_status or "").upper() == "W" and not shift_id:
            raise ValueError("Shift is required when converting week off to present")

        effective_shift_id = shift_id if str(from_status or "").upper() == "W" else None
        assignment = self._monthly_resolve_assignment_for_date(
            guard,
            target_date,
            scoped_location_id,
            actor=actor,
            shift_id=effective_shift_id,
        )
        if not assignment or not assignment.shift or not assignment.location:
            raise ValueError("No active shift assignment for this date")

        shift = assignment.shift
        org_location = shift.location
        search_start_utc, search_end_utc, shift_start_dt_user, shift_end_dt_user, _ = (
            _attendance_v3_shift_window_utc(
                target_date,
                shift,
                user_tz,
                location_id=getattr(org_location, "id", None),
            )
        )
        checkin_utc = shift_start_dt_user.astimezone(pytz.UTC)
        checkout_utc = shift_end_dt_user.astimezone(pytz.UTC)

        if str(getattr(shift, "location_id", "")) != str(scoped_location_id):
            raise ValueError("Shift location does not match selected scope")

        latitude = longitude = 0
        if org_location and org_location.latitude is not None and org_location.longitude is not None:
            latitude = org_location.latitude
            longitude = org_location.longitude

        with transaction.atomic():
            AttendanceWeekOff.objects.filter(
                user_id=guard.id,
                location_id=scoped_location_id,
                weekoff_date=target_date,
            ).delete()

            CheckInLog.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                timestamp__gte=search_start_utc,
                timestamp__lt=search_end_utc,
            ).delete()

            existing_site_id = AttendanceCheckin.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                shift_date=target_date,
            ).values_list("site_id", flat=True).first()

            AttendanceCheckin.objects.filter(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                shift_date=target_date,
            ).delete()

            final_site_id = existing_site_id if existing_site_id else site_id

            checkin_log = CheckInLog.objects.create(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkin",
                latitude=latitude,
                longitude=longitude,
                site_id=final_site_id,
            )
            CheckInLog.objects.filter(id=checkin_log.id).update(timestamp=checkin_utc)

            checkout_log = CheckInLog.objects.create(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                type="checkout",
                latitude=latitude,
                longitude=longitude,
                site_id=final_site_id,
            )
            CheckInLog.objects.filter(id=checkout_log.id).update(timestamp=checkout_utc)

            attendance, _ = AttendanceCheckin.objects.get_or_create(
                guard=guard,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                shift_date=target_date,
                defaults={"created_on": timezone.now(), "site_id": final_site_id},
            )
            if not attendance.site:
                attendance.site_id = final_site_id
            attendance.checkin_time = checkin_utc
            attendance.checkout_time = checkout_utc
            attendance.shift_date = target_date
            attendance.edited_by = actor
            attendance.edited_on = timezone.now()
            attendance.edit_reason = reason
            attendance.save()

            _attendance_v3_refresh_saved_fields(
                attendance,
                guard,
                assignment,
                shift,
                org_location,
                search_start_utc,
                search_end_utc,
            )

        attendance.refresh_from_db()
        return {
            "user_id": str(guard.id),
            "date": str(target_date),
            "pa_status": attendance.pa_status,
            "attendance_id": str(attendance.id),
        }

    def _monthly_cell_mark_weekoff(self, request, guard, scoped_location_id, target_date, reason, site_id=None):
        user_tz = get_user_timezone_from_request(request, location_id=scoped_location_id)
        user_today = get_user_today(user_tz)
        if target_date > user_today:
            raise ValueError("Cannot mark week off for a future date")

        actor = request.user
        batch_id = uuid.uuid4()
        with transaction.atomic():
            AttendanceWeekOff.objects.update_or_create(
                user_id=guard.id,
                location_id=scoped_location_id,
                weekoff_date=target_date,
                defaults={
                    "mark": "W",
                    "source": "api",
                    "upload_batch_id": batch_id,
                    "created_by": actor,
                },
            )

            assignment = self._monthly_resolve_assignment_for_date(guard, target_date, scoped_location_id)
            if assignment and assignment.shift and assignment.shift.location:
                shift = assignment.shift
                org_loc = shift.location
                search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                    target_date,
                    shift,
                    user_tz,
                    location_id=getattr(org_loc, "id", None),
                )
                CheckInLog.objects.filter(
                    guard=guard,
                    assignment=assignment,
                    shift=shift,
                    org_location=org_loc,
                    timestamp__gte=search_start_utc,
                    timestamp__lt=search_end_utc,
                ).delete()
                AttendanceCheckin.objects.filter(
                    guard=guard,
                    assignment=assignment,
                    shift=shift,
                    org_location=org_loc,
                    shift_date=target_date,
                ).delete()

        return {"user_id": str(guard.id), "date": str(target_date), "reason": reason}

    def _monthly_cell_mark_empty(self, request, guard, scoped_location_id, target_date, reason):
        user_tz = get_user_timezone_from_request(request, location_id=scoped_location_id)
        user_today = get_user_today(user_tz)
        if target_date > user_today:
            raise ValueError("Cannot clear attendance for a future date")

        with transaction.atomic():
            AttendanceWeekOff.objects.filter(
                user_id=guard.id,
                location_id=scoped_location_id,
                weekoff_date=target_date,
            ).delete()

            attendance_qs = AttendanceCheckin.objects.filter(
                guard=guard,
                org_location_id=scoped_location_id,
                shift_date=target_date,
            ).select_related("assignment", "shift", "org_location")

            log_deleted_count = 0
            for attendance in attendance_qs:
                assignment = getattr(attendance, "assignment", None)
                shift = getattr(attendance, "shift", None)
                org_location = getattr(attendance, "org_location", None)
                if not assignment or not shift or not org_location:
                    continue
                search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                    target_date,
                    shift,
                    user_tz,
                    location_id=getattr(org_location, "id", None),
                )
                n, _ = CheckInLog.objects.filter(
                    guard=guard,
                    assignment=assignment,
                    shift=shift,
                    org_location=org_location,
                    timestamp__gte=search_start_utc,
                    timestamp__lt=search_end_utc,
                ).delete()
                log_deleted_count += n

            attendance_deleted_count, _ = attendance_qs.delete()
            if attendance_deleted_count == 0:
                # Fallback: clear same-day logs in scoped location for this guard.
                day_start_user = datetime.combine(target_date, datetime.min.time())
                day_start_user = user_tz.localize(day_start_user)
                day_end_user = day_start_user + timedelta(days=1)
                n, _ = CheckInLog.objects.filter(
                    guard=guard,
                    org_location_id=scoped_location_id,
                    timestamp__gte=day_start_user.astimezone(pytz.UTC),
                    timestamp__lt=day_end_user.astimezone(pytz.UTC),
                ).delete()
                log_deleted_count += n

        return {
            "user_id": str(guard.id),
            "date": str(target_date),
            "reason": reason,
            "attendance_deleted_count": int(attendance_deleted_count or 0),
            "log_deleted_count": int(log_deleted_count or 0),
        }

    @action(detail=False, methods=["post"], url_path="monthly-cell-action")
    def monthly_cell_action(self, request):
        """
        Bulk correct monthly grid cells from the attendance summary UI.

        Body:
          - action: "mark_present" | "mark_weekoff" | "mark_empty"
          - reason: required (audit)
          - shift_id: optional (required for weekoff->present rows)
          - cells: [ { "user_id": "<uuid>", "date": "YYYY-MM-DD", "from_status": "W|OW|LD|M|P|''" }, ... ]
        """
        scoped_location_id, error_response = self._resolve_scope_location_id(request)
        if error_response:
            return error_response

        action_type = (request.data.get("action") or "").strip().lower()
        reason = (request.data.get("reason") or "").strip()
        shift_id = request.data.get("shift_id")
        cells = request.data.get("cells")
        site_id = request.data.get("site_id")

        if action_type not in ("mark_present", "mark_weekoff", "mark_empty"):
            return Response(
                {"error": 'action must be "mark_present", "mark_weekoff", or "mark_empty"'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not reason:
            return Response({"error": "reason is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(cells, list) or not cells:
            return Response({"error": "cells must be a non-empty list"}, status=status.HTTP_400_BAD_REQUEST)

        success = []
        failed = []

        for cell in cells:
            if not isinstance(cell, dict):
                failed.append({"cell": cell, "error": "Invalid cell payload"})
                continue
            user_id = cell.get("user_id")
            date_str = cell.get("date")
            from_status = (cell.get("from_status") or "").strip().upper()
            if not user_id or not date_str:
                failed.append({"cell": cell, "error": "user_id and date are required"})
                continue
            target_date = parse_date(str(date_str))
            if not target_date:
                failed.append({"user_id": user_id, "error": "Invalid date; use YYYY-MM-DD"})
                continue

            guard = User.objects.filter(
                id=user_id,
                is_deleted=False,
                is_active=True,
                location_id=scoped_location_id,
            ).exclude(Q(role__iexact="admin") | Q(is_superuser=True)).first()
            if not guard:
                failed.append({"user_id": str(user_id), "error": "User not found or not eligible for this location"})
                continue

            try:
                if action_type == "mark_present":
                    data = self._monthly_cell_mark_present(
                        request,
                        guard,
                        scoped_location_id,
                        target_date,
                        reason,
                        shift_id=shift_id,
                        from_status=from_status,
                        site_id=site_id,
                    )
                elif action_type == "mark_weekoff":
                    data = self._monthly_cell_mark_weekoff(
                        request, guard, scoped_location_id, target_date, reason, site_id=site_id
                    )
                else:
                    data = self._monthly_cell_mark_empty(
                        request, guard, scoped_location_id, target_date, reason
                    )
                success.append(data)
            except Exception as ex:
                failed.append({"user_id": str(user_id), "date": str(target_date), "error": str(ex)})

        return Response(
            {
                "action": action_type,
                "location_id": str(scoped_location_id),
                "reason": reason,
                "summary": {
                    "requested_count": len(cells),
                    "success_count": len(success),
                    "failed_count": len(failed),
                },
                "success": success,
                "failed": failed,
            },
            status=status.HTTP_200_OK if not failed else status.HTTP_207_MULTI_STATUS,
        )

    @action(detail=False, methods=["post"], url_path="assign-checkpoint-template")
    def assign_checkpoint_template(self, request):
        """
        Assign checkpoint template for an existing active assignment only when
        assignment has no checkpoints yet.
        Useful for flow where assignment is created first (without checkpoints),
        and checkpoints are attached later from mobile.

        Body:
          - guard_id (required)
          - checkpoint_template_id (required)
          - shift_id (optional, used to verify target shift)
        """
        guard_id = request.data.get("guard_id")
        checkpoint_template_id = request.data.get("checkpoint_template_id")
        shift_id = request.data.get("shift_id")

        if not guard_id or not checkpoint_template_id:
            return Response(
                {"error": "guard_id and checkpoint_template_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            guard = User.objects.get(id=guard_id)
        except User.DoesNotExist:
            return Response({"error": "Guard not found"}, status=status.HTTP_404_NOT_FOUND)

        # Find current active assignment using same v2 logic as shift/checkin APIs
        assignment, _ = self.get_today_assignment_v2(guard, request)
        if not assignment:
            return Response(
                {"error": "No active assignment found for this guard today"},
                status=status.HTTP_404_NOT_FOUND
            )

        if shift_id and str(assignment.shift_id) != str(shift_id):
            return Response(
                {"error": "Active assignment does not match provided shift_id"},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            template = CheckpointTemplate.objects.get(
                id=checkpoint_template_id,
                is_deleted=False
            )
        except CheckpointTemplate.DoesNotExist:
            return Response(
                {"error": "Checkpoint template not found"},
                status=status.HTTP_404_NOT_FOUND
            )

        # Validate template vs assignment shift/location
        if assignment.location_id and template.location_id != assignment.location_id:
            return Response(
                {"error": "Checkpoint template location does not match assignment location"},
                status=status.HTTP_400_BAD_REQUEST
            )

        if template.shift_id != assignment.shift_id:
            return Response(
                {"error": "Checkpoint template shift does not match assignment shift"},
                status=status.HTTP_400_BAD_REQUEST
            )

        existing_checkpoints = assignment.checkpoints or []
        if isinstance(existing_checkpoints, list) and len(existing_checkpoints) > 0:
            return Response(
                {"error": "Assignment already has checkpoints. Update is not allowed from this API."},
                status=status.HTTP_400_BAD_REQUEST
            )

        checkpoints = template.checkpoints or []
        if not isinstance(checkpoints, list):
            return Response(
                {"error": "Checkpoint template checkpoints must be a list"},
                status=status.HTTP_400_BAD_REQUEST
            )

        assignment.checkpoints = checkpoints
        assignment.modified_by = request.user
        assignment.save(update_fields=["checkpoints", "modified_by", "modified_on"])

        return Response({
            "message": "Checkpoint template assigned successfully",
            "assignment_id": str(assignment.id),
            "guard_id": str(guard.id),
            "shift_id": str(assignment.shift_id),
            "checkpoint_template_id": str(template.id),
            "checkpoint_template_name": template.template_name,
            "checkpoints_count": len(checkpoints),
        }, status=status.HTTP_200_OK)

    # ----------------------
    # DASHBOARD (with date range filter)
    # ----------------------
    # @action(detail=False, methods=["get"])
    # def dashboard(self, request):
    #     start_date = request.query_params.get("start_date")
    #     end_date = request.query_params.get("end_date")

    #     # Default: today
    #     if not start_date:
    #         start_date = date.today().strftime("%Y-%m-%d")
    #     if not end_date:
    #         end_date = date.today().strftime("%Y-%m-%d")

    #     try:
    #         start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    #         end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    #     except ValueError:
    #         return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

    #     records = AttendanceCheckin.objects.filter(
    #         checkin_time_date_gte=start_date,
    #         checkin_time_date_lte=end_date
    #     ).select_related("guard", "shift", "assignment")

    #     data = []
    #     for record in records:
    #         data.append({
    #             "name": record.guard.get_full_name() or record.guard.username,
    #             "date": record.checkin_time.date() if record.checkin_time else record.assignment.start_date,
    #             "shift_time": f"{record.shift.start_time} - {record.shift.end_time}",
    #             "checkin_time": record.checkin_time,
    #             "checkout_time": record.checkout_time,
    #             "status": record.status,
    #             "remarks": record.remarks,
    #             "od_remarks": record.od_remarks
    #         })

    #     return Response(data, status=status.HTTP_200_OK)
    

    # views.py

# ============================================================================
# CORE FUNCTION: Shared business logic for check-in reports
# ============================================================================

def _get_checkin_report_data(
    filter_type='today',
    start_date_str=None,
    end_date_str=None,
    user_id=None,
    location_id=None,
    shift_id=None,
    request=None,
    search=None,
    role=None,
):
    """
    Internal helper function that contains ALL business logic for check-in reports.
    This ensures consistency across JSON API, Excel exports, and Celery tasks.
    
    Args:
        filter_type: 'today', 'this_week', 'this_month', or 'custom'
        start_date_str: For custom filter (YYYY-MM-DD string)
        end_date_str: For custom filter (YYYY-MM-DD string)
        user_id: Optional UUID string to filter by guard (if set, search/role are ignored)
        location_id: Optional UUID string to filter by location
        shift_id: Optional UUID string to filter by shift
        request: Optional request object for timezone detection (for API calls)
        search: Optional name or employee code substring (icontains)
        role: Optional guard role / designation (iexact, or 'all' to skip)
    
    Returns:
        List of dicts with report data:
        [{
            'date': '2025-11-05',
            'guard_id': UUID,
            'guard_name': str,
            'location_id': str,
            'location_name': str,
            'shift_id': str,
            'shift_name': str,
            'checkpoint_id': str,
            'checkpoint_name': str,
            'expected_time': datetime,
            'actual_checkin_time': datetime or None,
            'status': 'On Time' | 'Delayed' | 'Missed',
            'delay_minutes': int or None
        }, ...]
    """
    from django.utils.timezone import now as django_now
    
    # Get user timezone from request (authenticated user making the request)
    # For superadmin viewing location-specific reports, uses location admin's timezone
    # This ensures filters use the correct timezone for the location being viewed
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        today = get_user_today(user_tz)
        # Log timezone being used for debugging
        # logger.info(f"[CHECKIN_REPORT] Using timezone: {user_tz.zone} for location_id: {location_id}")
    else:
        # For Celery tasks or non-request contexts, use default timezone
        # Note: In Celery tasks, we should ideally get timezone from the user_id if provided
        user_tz = pytz.timezone('Asia/Kolkata')
        today = get_user_today(user_tz)  # Use get_user_today instead of timezone.now().date()
    
    # Parse filter and determine date range in user timezone
    if filter_type == 'today':
        start_dt_user = datetime.combine(today, datetime.min.time())
        end_dt_user = datetime.combine(today, datetime.max.time())
    elif filter_type == 'this_week':
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        start_dt_user = datetime.combine(start_week, datetime.min.time())
        end_dt_user = datetime.combine(end_week, datetime.max.time())
    elif filter_type == 'this_month':
        start_dt_user = datetime(today.year, today.month, 1)
        next_month = start_dt_user.replace(day=28) + timedelta(days=4)
        end_dt_user = datetime(next_month.year, next_month.month, 1) - timedelta(seconds=1)
    elif filter_type == 'custom' and start_date_str and end_date_str:
        try:
            start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            start_dt_user = datetime.combine(start_date_obj, datetime.min.time())
            end_dt_user = datetime.combine(end_date_obj, datetime.max.time())
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
    else:
        raise ValueError("Invalid filter or missing dates")
    
    # Get assignments that overlap with the date range
    # Extend 1 day before filter start to capture overnight shifts from previous day
    # whose after-midnight checkpoints fall within the filter range
    query_start_date = start_dt_user.date() - timedelta(days=1)
    query_end_date = end_dt_user.date()
    assignments = Assignment.objects.filter(
        start_date__lte=query_end_date,
        end_date__gte=query_start_date
    ).select_related('guard', 'location', 'shift')
    
    if location_id:
        assignments = assignments.filter(location_id=location_id)
    if shift_id:
        assignments = assignments.filter(shift_id=shift_id)
    if user_id:
        assignments = assignments.filter(guard_id=user_id)
    elif search and str(search).strip():
        s = str(search).strip()
        assignments = assignments.filter(
            Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s)
        )
    if not user_id and role and str(role).strip().lower() not in ('', 'all'):
        assignments = assignments.filter(guard__role__iexact=str(role).strip().lower())
    
    report = []
    
    # Generate date range for the filter period (in user timezone)
    date_range = []
    current_date = start_dt_user.date()
    end_date = end_dt_user.date()
    
    # For "this_week", "this_month", and "custom" filters, limit to only include dates up to today
    # This prevents showing checkpoints from future shifts
    if filter_type in ['this_week', 'this_month', 'custom']:
        end_date = min(end_date, today)
    
    while current_date <= end_date:
        date_range.append(current_date)
        current_date += timedelta(days=1)
    
    for assignment in assignments:
        guard = assignment.guard
        shift = assignment.shift
        location = assignment.location
        
        # For each checkpoint in the assignment
        for cp in (assignment.checkpoints or []):
            checkpoint_id = cp.get('checkpoint_id')
            expected_time_str = cp.get('time')
            
            # Validate checkpoint data
            if not checkpoint_id or not expected_time_str:
                continue  # Skip invalid checkpoint entries
            
            try:
                expected_time_obj = datetime.strptime(expected_time_str, "%H:%M").time()
            except ValueError:
                continue  # Skip invalid time format
            
            # Get checkpoint name (with error handling)
            try:
                checkpoint = Checkpoint.objects.get(id=checkpoint_id)
                checkpoint_name = checkpoint.label
            except Checkpoint.DoesNotExist:
                checkpoint_name = "Unknown Checkpoint"
                continue  # Skip if checkpoint doesn't exist
            
            # Check if shift is overnight
            is_overnight = shift.end_time <= shift.start_time
            
            # For each date in the range where the assignment is active
            # For ALL filters (not just today), check the day before filter start
            # to catch overnight shift checkpoints that spill into the filter range
            # Example: Feb 2 filter → also check Feb 1's overnight shift for 3 AM Feb 2 checkpoints
            dates_to_check = list(date_range)
            if is_overnight:
                prev_day = start_dt_user.date() - timedelta(days=1)
                if assignment.start_date <= prev_day <= assignment.end_date:
                    if prev_day not in dates_to_check:
                        dates_to_check.insert(0, prev_day)
            
            for check_date in dates_to_check:
                # Skip future dates - don't process shifts that haven't started yet
                if check_date > today:
                    continue
                
                # Only process if assignment is active on this date
                if not (assignment.start_date <= check_date <= assignment.end_date):
                    continue
                
                # Determine which calendar date this checkpoint occurs on
                # For overnight shifts: checkpoints after midnight occur on the NEXT calendar day
                # For normal shifts: checkpoints occur on the same day
                if is_overnight:
                    # For overnight shifts, check if checkpoint time is before or after midnight
                    if expected_time_obj >= shift.start_time:
                        # Checkpoint is before midnight - occurs on the same calendar day as shift start
                        checkpoint_date = check_date
                    else:
                        # Checkpoint is after midnight - occurs on the NEXT calendar day
                        checkpoint_date = check_date + timedelta(days=1)
                else:
                    # Normal shift - checkpoint is on the same day
                    checkpoint_date = check_date
                
                # UNIFIED FILTER: Include checkpoint only if it actually occurs within the filter date range
                # This works consistently for ALL filter types (today, custom, this_week, this_month)
                if not (start_dt_user.date() <= checkpoint_date <= end_date):
                    continue
                
                # Calculate expected time for this specific date in user timezone
                expected_datetime_user = combine_date_time_in_user_tz(checkpoint_date, expected_time_obj, user_tz)
                
                # Define ±30 min search window around expected time (in UTC) to find matching CheckIn
                search_start_utc = (expected_datetime_user - timedelta(minutes=30)).astimezone(pytz.UTC)
                search_end_utc = (expected_datetime_user + timedelta(minutes=30)).astimezone(pytz.UTC)

                checkin = CheckIn.objects.filter(
                    guard=guard,
                    shift=shift,
                    checkpoint_id=checkpoint_id,
                    timestamp__gte=search_start_utc,
                    timestamp__lt=search_end_utc
                ).order_by('timestamp').first()
                
                actual_time = None
                delay = None
                
                # Determine default status based on whether scheduled time has passed
                # Convert expected time to user timezone for comparison
                if expected_datetime_user:
                    expected_time_user = expected_datetime_user.astimezone(user_tz)
                    user_now = get_user_now(user_tz)
                    # Check if scheduled time + 15 minutes grace period has passed
                    if user_now > expected_time_user + timedelta(minutes=15):
                        status = "Missed"  # Time has passed, no check-in = Missed
                    else:
                        status = "Pending"  # Time hasn't passed yet = Pending
                else:
                    status = "Missed"  # Fallback if no expected time
                
                # Process check-in if found
                if checkin:
                    # Check if it's synced
                    if not checkin.synced:
                        # Unsynced check-in (offline mode) - treat as missed
                        status = "Missed"
                        actual_time = None
                        delay = None
                    else:
                        # Valid synced check-in
                        actual_time = checkin.timestamp
                        
                        # Log UTC time before conversion
                        # logger.info(f"[CHECKIN_REPORT] UTC time (before conversion): {actual_time}, timezone: {actual_time.tzinfo if actual_time else None}")
                        
                        # Convert both to user timezone for delay calculation
                        actual_time_user = to_user_timezone(actual_time, user_tz)
                        expected_time_user = expected_datetime_user.astimezone(user_tz)
                        
                        # Log converted time
                        # logger.info(f"[CHECKIN_REPORT] Converted time (after conversion): {actual_time_user}, target timezone: {user_tz.zone}")
                        # logger.info(f"[CHECKIN_REPORT] Expected time: {expected_time_user}, timezone: {expected_time_user.tzinfo if expected_time_user else None}")
                        
                        # Calculate delay in minutes (in user timezone)
                        delay = int((actual_time_user - expected_time_user).total_seconds() / 60)
                        
                        # Determine status based on delay
                        if delay <= 15:
                            status = "On Time"
                        elif 15 < delay <= 30:
                            status = "Delayed"
                        else:
                            status = "Missed"
                
                # Checklist fields (only meaningful when a CheckIn exists)
                checkin_id = str(checkin.id) if checkin else None
                has_checklist = bool(checkin.has_checklist) if checkin else False
                checklist_template_name = None
                checklist_remarks = None
                checklist_checked_count = None
                checklist_total_count = None

                checklist_answers = None
                if checkin and has_checklist:
                    answer = CheckInChecklistAnswer.objects.filter(
                        checkin=checkin,
                        is_deleted=False
                    ).select_related('checklist_template').first()

                    if answer:
                        checklist_template_name = answer.checklist_template.name if answer.checklist_template else None
                        checklist_remarks = answer.remarks
                        checklist_answers = answer.answers or []
                        try:
                            checklist_total_count = len(checklist_answers)
                            checklist_checked_count = sum(1 for a in checklist_answers if a.get("checked") is True)
                        except Exception:
                            checklist_total_count = None
                            checklist_checked_count = None

                # Add to report (convert times to user timezone for display)
                expected_time_display = expected_datetime_user.astimezone(user_tz) if expected_datetime_user else None
                if actual_time:
                    # Log before final conversion for display
                    # logger.info(f"[CHECKIN_REPORT] Display conversion - UTC: {actual_time}, Converting to: {user_tz.zone}")
                    actual_time_display = to_user_timezone(actual_time, user_tz)
                    # logger.info(f"[CHECKIN_REPORT] Display conversion - Result: {actual_time_display}, timezone: {actual_time_display.tzinfo if actual_time_display else None}")
                else:
                    actual_time_display = None
                
                # Use checkpoint_date as the report date
                # This is the actual calendar date the checkpoint occurs on
                # For overnight shifts: after-midnight checkpoints show on the next day
                # For normal shifts: same as check_date
                report_date = checkpoint_date
                
                report.append({
                    'date': report_date.strftime('%Y-%m-%d'),
                    'guard_id': str(guard.id),
                    'guard_name': guard.name,
                    'employee_code': getattr(guard, 'employee_code', None) or '',
                    'designation': (getattr(guard, 'role', None) or '').strip(),
                    'location_id': str(location.id) if location else None,
                    'location_name': location.name if location else "",
                    'shift_id': str(shift.id) if shift else None,
                    'shift_name': shift.name if shift else "",
                    'checkpoint_id': str(checkpoint_id),
                    'checkpoint_name': checkpoint_name,
                    'expected_time': expected_time_display,  # In user timezone
                    'actual_checkin_time': actual_time_display,  # In user timezone
                    'status': status,
                    'delay_minutes': delay,
                    'checkin_id': checkin_id,
                    'has_checklist': has_checklist,
                    'checklist_template_name': checklist_template_name,
                    'checklist_remarks': checklist_remarks,
                    'checklist_checked_count': checklist_checked_count,
                    'checklist_total_count': checklist_total_count,
                    'checklist_answers': checklist_answers,
                })
    
    # Sort report by date, then by guard_name, then by checkpoint_name, then by expected_time
    # This ensures all checkpoints for a date are grouped together
    report.sort(key=lambda x: (
        x['date'],
        x['expected_time'] if x['expected_time'] else '',
        x['guard_name'],
        x['checkpoint_name'],
    ))
    
    logger.info(f"[CHECKIN_REPORT] Generated {len(report)} records for filter: {filter_type}")
    return report


# def _get_checkin_report_data(filter_type='today', start_date_str=None, end_date_str=None, user_id=None, location_id=None, shift_id=None, request=None):
#     from django.utils.timezone import now as django_now
    
#     # 1. IMPROVED TIMEZONE HANDLING
#     # Prioritize: Request Location -> Admin's Location -> Request User Profile -> Default
#     user_tz = None
#     if request:
#         # This utility already handles Superadmin vs Admin logic if location_id is passed
#         user_tz = get_user_timezone_from_request(request, location_id=location_id)
    
#     if not user_tz:
#         user_tz = pytz.timezone('Asia/Kolkata') # Final fallback
        
#     today = get_user_today(user_tz)
#     logger.info(f"[CHECKIN_REPORT] Generating report using timezone: {user_tz.zone}")
    
#     # 2. PARSE DATE RANGE (FIXED: Added missing logic)
#     start_date = today
#     end_date = today

#     if filter_type == 'custom' and start_date_str and end_date_str:
#         try:
#             start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
#             end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
#         except (ValueError, TypeError):
#             pass
#     elif filter_type == 'week':
#         start_date = today - timedelta(days=today.weekday())
#         end_date = today
#     elif filter_type == 'month':
#         start_date = today.replace(day=1)
#         end_date = today

#     # Convert local dates to UTC range for database query
#     start_utc, end_utc = convert_date_range_to_utc(start_date, end_date, user_tz)
    
#     # 3. COLLECT ACTUAL SCANS
#     checkins = CheckIn.objects.filter(
#         timestamp__gte=start_utc,
#         timestamp__lte=end_utc
#     ).select_related('guard', 'checkpoint', 'shift', 'shift__location')

#     if user_id:
#         checkins = checkins.filter(guard_id=user_id)
#     if location_id:
#         checkins = checkins.filter(shift__location_id=location_id)
#     if shift_id:
#         checkins = checkins.filter(shift_id=shift_id)

#     report = []

#     print(f"DEBUG: Found {checkins.count()} checkins between {start_utc} and {end_utc}")
#     for checkin in checkins:
#         print(f"DEBUG: CheckIn - Guard: {checkin.guard.name}, Time: {checkin.timestamp}, Checkpoint: {checkin.checkpoint.label}")

#         expected_time_display = None
#         status = "On Time"
#         delay = 0

#         # FIND CLOSEST MATCH FOR REPEATING CHECKPOINTS
#         assignment = Assignment.objects.filter(
#             guard=checkin.guard,
#             shift=checkin.shift,
#             start_date__lte=checkin.timestamp.date(),
#             end_date__gte=checkin.timestamp.date()
#         ).first()

#         if assignment:
#             best_match_dt = None
#             min_seconds_diff = float('inf')

#             # Loop through ALL assigned times for this checkpoint
#             for cp in assignment.checkpoints:
#                 if str(cp.get('checkpoint_id')) == str(checkin.checkpoint.id):
#                     try:
#                         slot_time = datetime.strptime(cp.get('time'), "%H:%M").time()
#                         # Create a full datetime for this slot in user local time
#                         slot_dt = datetime.combine(checkin.timestamp.astimezone(user_tz).date(), slot_time)
#                         slot_dt = user_tz.localize(slot_dt)
                        
#                         # Compare scan time vs this specific slot
#                         diff = abs((checkin.timestamp - slot_dt.astimezone(pytz.UTC)).total_seconds())
                        
#                         if diff < min_seconds_diff:
#                             min_seconds_diff = diff
#                             best_match_dt = slot_dt
#                     except:
#                         continue

#             if best_match_dt:
#                 expected_time_display = best_match_dt
#                 delay_seconds = (checkin.timestamp - best_match_dt.astimezone(pytz.UTC)).total_seconds()
#                 delay = int(delay_seconds / 60)
                
#                 # Use standard 15-minute window for "On Time"
#                 if abs(delay) <= 15:
#                     status = "On Time"
#                 elif delay > 0:
#                     status = "Delayed"
#                 else:
#                     status = "Early"

#         report.append({
#             'date': checkin.timestamp.astimezone(user_tz).date().isoformat(),
#             'guard_id': str(checkin.guard.id),
#             'guard_name': checkin.guard.name,
#             'location_id': str(checkin.shift.location.id) if checkin.shift and checkin.shift.location else None,
#             'location_name': checkin.shift.location.name if checkin.shift and checkin.shift.location else "",
#             'shift_id': str(checkin.shift.id) if checkin.shift else None,
#             'shift_name': checkin.shift.name if checkin.shift else "",
#             'checkpoint_id': str(checkin.checkpoint.id),
#             'checkpoint_name': checkin.checkpoint.label,
#             'expected_time': expected_time_display,
#             'actual_checkin_time': checkin.timestamp.astimezone(user_tz),
#             'status': status,
#             'delay_minutes': delay
#         })

#     # SORT BY EXPECTED TIME
#     report.sort(key=lambda x: (x['expected_time'].isoformat() if x['expected_time'] else '9999', x['actual_checkin_time']))

#     return report

# ============================================================================
# API ENDPOINTS
# ============================================================================

class DashboardCheckInReportView(APIView):
    """
    API endpoint that returns check-in report data as JSON.
    Used by the frontend dashboard for real-time display.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Parse filters from request
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        shift_id = request.query_params.get('shift_id')
        search = (request.query_params.get('search') or '').strip()
        role = (request.query_params.get('role') or '').strip()
        
        # Log request details (both logger and print for visibility)
        
        try:
            # Get report data using shared core function
            report_data = _get_checkin_report_data(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id,
                shift_id=shift_id,
                request=request,  # Pass request for timezone detection
                search=search or None,
                role=role or None,
            )
            
            # Format datetime fields for JSON response (already in user timezone from _get_checkin_report_data)
            for idx, item in enumerate(report_data):
                if item['expected_time']:
                    # Log before formatting
                    # logger.info(f"[CHECKIN_REPORT] Item {idx} - Expected time before format: {item['expected_time']}, timezone: {item['expected_time'].tzinfo if hasattr(item['expected_time'], 'tzinfo') else 'N/A'}")
                    # Convert to ISO format string (timezone-aware)
                    if hasattr(item['expected_time'], 'isoformat'):
                        item['expected_time'] = item['expected_time'].isoformat()
                    else:
                        item['expected_time'] = item['expected_time'].strftime('%Y-%m-%d %H:%M:%S')
                    # logger.info(f"[CHECKIN_REPORT] Item {idx} - Expected time after format: {item['expected_time']}")
                if item['actual_checkin_time']:
                    # Log before formatting
                    # logger.info(f"[CHECKIN_REPORT] Item {idx} - Actual time before format: {item['actual_checkin_time']}, timezone: {item['actual_checkin_time'].tzinfo if hasattr(item['actual_checkin_time'], 'tzinfo') else 'N/A'}")
                    # Convert to ISO format string (timezone-aware)
                    if hasattr(item['actual_checkin_time'], 'isoformat'):
                        item['actual_checkin_time'] = item['actual_checkin_time'].isoformat()
                    else:
                        item['actual_checkin_time'] = item['actual_checkin_time'].strftime('%Y-%m-%d %H:%M:%S')
                    # logger.info(f"[CHECKIN_REPORT] Item {idx} - Actual time after format: {item['actual_checkin_time']}")
            
            # Serialize and return
            serializer = CheckInReportSerializer(report_data, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except ValueError as e:
            logger.error(f"[CHECKIN_REPORT_API] ValueError: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"[CHECKIN_REPORT_API] Exception: {str(e)}", exc_info=True)
            return Response(
                {"error": f"An error occurred while generating the report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class AttendanceCheckinListView(generics.ListAPIView):
    serializer_class = AttendanceCheckinDashboardSerializer

    def get_queryset(self):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        # Get location_id for timezone (for superadmin viewing location-specific reports)
        location_id = self.request.query_params.get("location")
        
        # Get user timezone - for superadmin viewing location-specific reports, uses location admin's timezone
        user_tz = get_user_timezone_from_request(self.request, location_id=location_id)
        date_filter = self.request.query_params.get("date_filter", "today")

        user_today = get_user_today(user_tz)

        if date_filter == "today":
            # Convert today to UTC range for query
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        elif date_filter == "week":
            start_week = user_today - timedelta(days=user_today.weekday())
            # Limit to user_today to prevent showing future dates
            end_week = min(start_week + timedelta(days=6), user_today)
            start_utc, end_utc = convert_date_range_to_utc(start_week, end_week, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        elif date_filter == "month":
            # Get month boundaries in user timezone
            start_of_month = user_today.replace(day=1)
            # Limit to user_today to prevent showing future dates
            end_of_month = min(
                user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
                else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
                user_today
            )
            start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        elif date_filter == "custom":
            start_date = self.request.query_params.get("start_date")
            end_date = self.request.query_params.get("end_date")
            if start_date and end_date:
                try:
                    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                    # Limit to user_today to prevent showing future dates
                    end_date_obj = min(end_date_obj, user_today)
                    # Convert to UTC range for query
                    start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
                    queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
                except ValueError:
                    pass  # Invalid date format, skip custom filter

        # Additional filters
        guard_id = self.request.query_params.get("guard")
        if guard_id:
            queryset = queryset.filter(guard_id=guard_id)

        location_id = self.request.query_params.get("location")
        if location_id:
            queryset = queryset.filter(org_location_id=location_id)

        shift_id = self.request.query_params.get("shift")
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)

        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)

        defaulters = self.request.query_params.get("defaulters")
        if defaulters == "true":
            # Use UTC date range for today
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(
                checkin_time__gte=start_utc,
                checkin_time__lt=end_utc + timedelta(days=1),
                checkout_time__isnull=True
            )

        return queryset


class AttendanceCheckinV3ListView(generics.ListAPIView):
    serializer_class = AttendanceCheckinDashboardV3Serializer

    def get_queryset(self):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        location_id = self.request.query_params.get("location")
        user_tz = get_user_timezone_from_request(self.request, location_id=location_id)
        date_filter = self.request.query_params.get("date_filter", "today")
        user_today = get_user_today(user_tz)

        if date_filter == "today":
            queryset = queryset.filter(shift_date=user_today)
        elif date_filter == "week":
            start_week = user_today - timedelta(days=user_today.weekday())
            end_week = min(start_week + timedelta(days=6), user_today)
            queryset = queryset.filter(shift_date__gte=start_week, shift_date__lte=end_week)
        elif date_filter == "month":
            start_of_month = user_today.replace(day=1)
            end_of_month = min(
                user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
                else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
                user_today
            )
            queryset = queryset.filter(shift_date__gte=start_of_month, shift_date__lte=end_of_month)
        elif date_filter == "custom":
            start_date = self.request.query_params.get("start_date")
            end_date = self.request.query_params.get("end_date")
            if start_date and end_date:
                try:
                    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                    end_date_obj = min(end_date_obj, user_today)
                    queryset = queryset.filter(shift_date__gte=start_date_obj, shift_date__lte=end_date_obj)
                except ValueError:
                    pass

        guard_id = self.request.query_params.get("guard")
        if guard_id:
            queryset = queryset.filter(guard_id=guard_id)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(guard__name__icontains=search) | Q(guard__employee_code__icontains=search)
            )

        role_param = (self.request.query_params.get("role") or "").strip().lower()
        if role_param and role_param != "all":
            queryset = queryset.filter(guard__role__iexact=role_param)

        location_id = self.request.query_params.get("location")
        if location_id:
            queryset = queryset.filter(org_location_id=location_id)

        shift_id = self.request.query_params.get("shift")
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)

        status = self.request.query_params.get("status")
        if status:
            queryset = _apply_attendance_v3_status_filter(queryset, status)

        defaulters = self.request.query_params.get("defaulters")
        if defaulters == "true":
            queryset = queryset.filter(
                Q(checkin_time__isnull=False) & (
                    # Normal v3 open session based on denormalized latest fields
                    (Q(last_checkin_time__isnull=False) & Q(last_checkout_time__isnull=True)) |
                    Q(last_checkin_time__gt=F("last_checkout_time")) |
                    # Fallback when latest summary fields are stale/missing
                    (Q(last_checkin_time__isnull=True) & Q(checkout_time__isnull=True))
                )
            )

        # Enforce stable latest-first ordering for dashboard table.
        return queryset.order_by("-shift_date", "-checkin_time", "-last_checkin_time", "-modified_on", "-id")


class AttendanceCheckinV4ListView(generics.ListAPIView):
    """
    V4 Attendance API: Optimized by bulk-fetching CheckInLogs to avoid N+1 queries.
    """
    serializer_class = AttendanceCheckinDashboardV4Serializer

    def get_queryset(self):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        location_id = self.request.query_params.get("location")
        user_tz = get_user_timezone_from_request(self.request, location_id=location_id)
        date_filter = self.request.query_params.get("date_filter", "today")
        user_today = get_user_today(user_tz)

        if date_filter == "today":
            queryset = queryset.filter(shift_date=user_today)
        elif date_filter == "week":
            start_week = user_today - timedelta(days=user_today.weekday())
            end_week = min(start_week + timedelta(days=6), user_today)
            queryset = queryset.filter(shift_date__gte=start_week, shift_date__lte=end_week)
        elif date_filter == "month":
            start_of_month = user_today.replace(day=1)
            end_of_month = min(
                user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
                else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
                user_today
            )
            queryset = queryset.filter(shift_date__gte=start_of_month, shift_date__lte=end_of_month)
        elif date_filter == "custom":
            start_date = self.request.query_params.get("start_date")
            end_date = self.request.query_params.get("end_date")
            if start_date and end_date:
                try:
                    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                    end_date_obj = min(end_date_obj, user_today)
                    queryset = queryset.filter(shift_date__gte=start_date_obj, shift_date__lte=end_date_obj)
                except ValueError:
                    pass

        guard_id = self.request.query_params.get("guard")
        if guard_id:
            queryset = queryset.filter(guard_id=guard_id)

        search = (self.request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(guard__name__icontains=search) | Q(guard__employee_code__icontains=search)
            )

        role_param = (self.request.query_params.get("role") or "").strip().lower()
        if role_param and role_param != "all":
            queryset = queryset.filter(guard__role__iexact=role_param)

        location_id = self.request.query_params.get("location")
        if location_id:
            queryset = queryset.filter(org_location_id=location_id)

        shift_id = self.request.query_params.get("shift")
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)

        site_id = self.request.query_params.get("site_id")
        if site_id:
            queryset = queryset.filter(site_id=site_id)

        status = self.request.query_params.get("status")
        if status:
            queryset = _apply_attendance_v3_status_filter(queryset, status)

        defaulters = self.request.query_params.get("defaulters")
        if defaulters == "true":
            queryset = queryset.filter(
                Q(checkin_time__isnull=False) & (
                    (Q(last_checkin_time__isnull=False) & Q(last_checkout_time__isnull=True)) |
                    Q(last_checkin_time__gt=F("last_checkout_time")) |
                    (Q(last_checkin_time__isnull=True) & Q(checkout_time__isnull=True))
                )
            )

        return queryset.order_by("-shift_date", "-checkin_time", "-last_checkin_time", "-modified_on", "-id")

    def list(self, request, *args, **kwargs):
        queryset = self.filter_queryset(self.get_queryset())

        # Execute queryset once to get the list for serialization and log pre-fetching
        page_items = list(queryset)
        if not page_items:
            return Response([])

        # Determine global time window for log pre-fetching
        location_id = self.request.query_params.get("location")
        user_tz = get_user_timezone_from_request(self.request, location_id=location_id)
        
        guard_ids = {obj.guard_id for obj in page_items}
        
        # Calculate search window for the entire result set
        min_start_utc = None
        max_end_utc = None
        
        grace_minutes = _get_site_setting_int(
            key="shift_grace_time",
            location_id=location_id,
            default_value=30,
        )

        for obj in page_items:
            source_dt = obj.checkin_time or obj.created_on
            if not source_dt: continue
            
            shift_day = obj.shift_date or to_user_timezone(source_dt, user_tz).date()
            if obj.shift.end_time <= obj.shift.start_time and obj.shift_date is None:
                local_dt = to_user_timezone(source_dt, user_tz)
                if local_dt.time() < obj.shift.end_time:
                    shift_day = shift_day - timedelta(days=1)
            
            # Use same logic as _attendance_v3_shift_window_utc to find boundaries
            shift_start_dt_user = combine_date_time_in_user_tz(shift_day, obj.shift.start_time, user_tz).astimezone(user_tz)
            if obj.shift.end_time <= obj.shift.start_time:
                shift_end_dt_user = combine_date_time_in_user_tz(shift_day + timedelta(days=1), obj.shift.end_time, user_tz).astimezone(user_tz)
            else:
                shift_end_dt_user = combine_date_time_in_user_tz(shift_day, obj.shift.end_time, user_tz).astimezone(user_tz)

            start_utc = (shift_start_dt_user - timedelta(minutes=grace_minutes)).astimezone(pytz.UTC)
            end_utc = (shift_end_dt_user + timedelta(minutes=grace_minutes)).astimezone(pytz.UTC)
            
            if min_start_utc is None or start_utc < min_start_utc:
                min_start_utc = start_utc
            if max_end_utc is None or end_utc > max_end_utc:
                max_end_utc = end_utc

        prefetched_logs = {}
        if min_start_utc and max_end_utc:
            logs = CheckInLog.objects.filter(
                guard_id__in=guard_ids,
                timestamp__gte=min_start_utc,
                timestamp__lt=max_end_utc,
            ).order_by("timestamp")
            
            for log in logs:
                if log.guard_id not in prefetched_logs:
                    prefetched_logs[log.guard_id] = []
                prefetched_logs[log.guard_id].append(log)

        serializer = self.get_serializer(page_items, many=True, context={
            'request': request,
            'prefetched_logs': prefetched_logs
        })
        return Response(serializer.data)


def generate_checkin_excel_report_internal(
    filter_type='today',
    start_date=None,
    end_date=None,
    user_id=None,
    location_id=None,
    shift_id=None,
    request=None,
    search=None,
    role=None,
):
    """
    Internal helper function to generate check-in Excel report.
    Used by both the API endpoint and Celery tasks.
    
    Args:
        filter_type: 'today', 'this_week', 'this_month', or 'custom'
        start_date: For custom filter (YYYY-MM-DD string)
        end_date: For custom filter (YYYY-MM-DD string)
        user_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
        request: Optional request object for timezone (None for Celery tasks)
    
    Returns:
        dict with 'file_path', 'filename', and 'row_count'
    """
    # Get report data using shared core function (pass request for timezone)
    report_data = _get_checkin_report_data(
        filter_type=filter_type,
        start_date_str=start_date,
        end_date_str=end_date,
        user_id=user_id,
        location_id=location_id,
        shift_id=shift_id,
        request=request,
        search=search,
        role=role,
    )
    
    # Create Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Check-In Report"

    def format_checklist_cell(report_item):
        answers = report_item.get('checklist_answers')
        if isinstance(answers, list) and len(answers) > 0:
            lines = []
            for a in answers:
                label = (a or {}).get('label') or 'Item'
                checked = (a or {}).get('checked') is True
                lines.append(f"{label} : {'✓' if checked else '✗'}")
            return "\n".join(lines)
        return report_item.get('checklist_template_name') or ""

    # Header row
    headers = [
        'Shift Date', 'Name', 'Emp Code', 'Designation', 'Shift Name', 'Checkpoint Name',
        'Scheduled', 'Scanned', 'Status', 'Delay (minutes)',
        'Has Checklist', 'Checklist', 'Checklist Remarks'
    ]
    ws.append(headers)

    row_count = 0
    for item in report_data:
        ws.append([
            item['date'],
            item['guard_name'],
            item.get('employee_code') or '',
            item.get('designation') or '',
            item['shift_name'],
            item['checkpoint_name'],
            item['expected_time'].strftime("%Y-%m-%d %H:%M") if item['expected_time'] else "",
            item['actual_checkin_time'].strftime("%Y-%m-%d %H:%M") if item['actual_checkin_time'] else "",
            item['status'],
            item['delay_minutes'] if item['delay_minutes'] is not None else "",
            "Yes" if item.get('has_checklist') else "No",
            format_checklist_cell(item),
            item.get('checklist_remarks') or "",
        ])
        row_count += 1

    # Wrap text for the Checklist column (so items show line-by-line)
    try:
        from openpyxl.styles import Alignment
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=12, max_col=12):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions['L'].width = 55
        ws.column_dimensions['M'].width = 35
    except Exception:
        pass

    # Save to in-memory buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # Define file path
    filename = f"checkin_report_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)

    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())

    return {
        'file_path': file_path,
        'filename': filename,
        'row_count': row_count
    }



class DashboardCheckInReportExcelView(APIView):
    """
    API endpoint that generates and returns an Excel file download URL.
    Used by the frontend for exporting check-in reports.
    """
    permission_classes = [IsAuthenticated]  # Fixed: Added authentication requirement
    
    def get(self, request):
        # Parse filters
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        shift_id = request.query_params.get('shift_id')
        search = (request.query_params.get('search') or '').strip()
        role = (request.query_params.get('role') or '').strip()
        
        
        try:
            # Get report data using shared core function (pass request for timezone)
            report_data = _get_checkin_report_data(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id,
                shift_id=shift_id,
                request=request,
                search=search or None,
                role=role or None,
            )
            
            # Create Excel workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Check-In Report"

            def format_checklist_cell(report_item):
                answers = report_item.get('checklist_answers')
                if isinstance(answers, list) and len(answers) > 0:
                    lines = []
                    for a in answers:
                        label = (a or {}).get('label') or 'Item'
                        checked = (a or {}).get('checked') is True
                        lines.append(f"{label} : {'✓' if checked else '✗'}")
                    return "\n".join(lines)
                return report_item.get('checklist_template_name') or ""

            # Header row
            headers = [
                'Date', 'Name', 'Emp Code', 'Designation', 'Shift Name', 'Checkpoint Name',
                'Expected Time', 'Actual Check-In Time', 'Status', 'Delay (minutes)',
                'Has Checklist', 'Checklist', 'Checklist Remarks'
            ]
            ws.append(headers)

            # Add data rows
            for item in report_data:
                ws.append([
                    item['date'],
                    item['guard_name'],
                    item.get('employee_code') or '',
                    item.get('designation') or '',
                    item['shift_name'],
                    item['checkpoint_name'],
                    item['expected_time'].strftime("%Y-%m-%d %H:%M") if item['expected_time'] else "",
                    item['actual_checkin_time'].strftime("%Y-%m-%d %H:%M") if item['actual_checkin_time'] else "",
                    item['status'],
                    item['delay_minutes'] if item['delay_minutes'] is not None else "",
                    "Yes" if item.get('has_checklist') else "No",
                    format_checklist_cell(item),
                    item.get('checklist_remarks') or "",
                ])

            # Wrap text for the Checklist column (so items show line-by-line)
            try:
                from openpyxl.styles import Alignment
                for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=12, max_col=12):
                    for cell in row:
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                ws.column_dimensions['L'].width = 55
                ws.column_dimensions['M'].width = 35
            except Exception:
                pass

            # Save to in-memory buffer
            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)

            # Return Excel file as HTTP response (not JSON)
            excel_content = buffer.getvalue()
            
            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
            # Generate a meaningful filename based on filter
            if filter_type == 'custom' and start_date and end_date:
                filename = f"checkin_report_{start_date}_{end_date}.xlsx"
            else:
                filename = f"checkin_report_{timezone.now().strftime('%Y%m%d')}.xlsx"
            
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"An error occurred while generating the Excel report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


def generate_attendance_excel_report_internal(date_filter='today', start_date=None, end_date=None, guard_id=None, location_id=None, shift_id=None, status_filter=None, defaulters=False, request=None):
    """
    Internal helper function to generate attendance check-in Excel report.
    This contains the business logic that both the API endpoint and Celery tasks can use.
    
    Args:
        date_filter: 'today', 'week', 'month', or 'custom'
        start_date: For custom filter (YYYY-MM-DD string)
        end_date: For custom filter (YYYY-MM-DD string)
        guard_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
        shift_id: Optional UUID string to filter by shift
        status_filter: Optional status string
        defaulters: Boolean to filter only defaulters
        request: Optional request object for timezone detection (required for proper timezone handling)
    
    Returns: dict with 'file_path' and 'filename'
    """
    queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

    # Get user timezone from request (authenticated user making the request)
    # For superadmin viewing location-specific reports, uses location admin's timezone
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        # Fallback: use default timezone if no request (should not happen in normal API calls)
        user_tz = pytz.timezone('Asia/Kolkata')
        user_today = get_user_today(user_tz)

    if date_filter == "today":
        # Convert today to UTC range for query
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
        queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
    elif date_filter == "week":
        start_week = user_today - timedelta(days=user_today.weekday())
        # Limit to user_today to prevent showing future dates
        end_week = min(start_week + timedelta(days=6), user_today)
        start_utc, end_utc = convert_date_range_to_utc(start_week, end_week, user_tz)
        queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
    elif date_filter == "month":
        # Get month boundaries in user timezone
        start_of_month = user_today.replace(day=1)
        # Limit to user_today to prevent showing future dates
        end_of_month = min(
            user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
            else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
            user_today
        )
        start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
        queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
    elif date_filter == "custom" and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            # Limit to user_today to prevent showing future dates
            end_date_obj = min(end_date_obj, user_today)
            # Convert to UTC range for query
            start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        except ValueError:
            raise ValueError("Invalid custom date format. Use YYYY-MM-DD.")

    # Additional filters
    if guard_id:
        queryset = queryset.filter(guard_id=guard_id)

    if location_id:
        queryset = queryset.filter(org_location_id=location_id)

    if shift_id:
        queryset = queryset.filter(shift_id=shift_id)

    if status_filter:
        queryset = queryset.filter(status=status_filter)

    if defaulters:
        # Use UTC date range for today
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
        queryset = queryset.filter(
            checkin_time__gte=start_utc,
            checkin_time__lt=end_utc + timedelta(days=1),
            checkout_time__isnull=True
        )

    # Create Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Checkins"

    # Header with reordered columns and added Date + Duration
    ws.append(["Shift Date", "Name", "Shift", "Location", "Checkin Time", "Checkout Time", "Duration (HH:MM)", "Status", "Remarks"])

    row_count = 0  # Track number of data rows
    for obj in queryset:
        # Convert UTC times to user timezone for display in Excel
        checkin = to_user_timezone(obj.checkin_time, user_tz) if obj.checkin_time else None
        checkout = to_user_timezone(obj.checkout_time, user_tz) if obj.checkout_time else None
        shift = obj.shift
        attendance_status = ""
        other_statuses = []
        duration = ""

        if shift:
            # Check if shift is overnight
            is_overnight = shift.end_time <= shift.start_time
            
            if checkin:
                # checkin is already in user timezone (converted above)
                checkin_date = checkin.date()
                
                if is_overnight:
                    # For overnight shifts, end time is on the next day
                    # Combine date and time in user timezone
                    shift_start = user_tz.localize(datetime.combine(checkin_date, shift.start_time))
                    # If checkout is on the same date as checkin, shift end is next day
                    # If checkout is on next day, shift end is on checkout date
                    if checkout:
                        checkout_date = checkout.date()
                        if checkout_date > checkin_date:
                            # Shift spans two days, end is on checkout date
                            shift_end = user_tz.localize(datetime.combine(checkout_date, shift.end_time))
                        else:
                            # Both on same date (shouldn't happen for overnight, but handle it)
                            shift_end = user_tz.localize(datetime.combine(checkin_date + timedelta(days=1), shift.end_time))
                    else:
                        # No checkout yet, assume end is next day
                        shift_end = user_tz.localize(datetime.combine(checkin_date + timedelta(days=1), shift.end_time))
                else:
                    # Normal shift - both times on same day
                    shift_start = user_tz.localize(datetime.combine(checkin_date, shift.start_time))
                    shift_end = user_tz.localize(datetime.combine(checkin_date, shift.end_time))
            else:
                shift_start = None
                shift_end = None

        if checkin and checkout:
            # Duration
            delta = checkout - checkin
            total_hours = delta.total_seconds() / 3600
            hours, remainder = divmod(delta.total_seconds(), 3600)
            minutes = remainder // 60
            duration = f"{int(hours):02}:{int(minutes):02}"
            # Compare first-checkin → last-checkout duration to scheduled shift length (hours).
            if shift:
                req_hours = _shift_required_duration_minutes(shift) / 60.0
                if req_hours > 0:
                    if total_hours >= req_hours:
                        attendance_status = "Present"
                    else:
                        attendance_status = "Absent"
                else:
                    attendance_status = ""
            else:
                attendance_status = ""

            grace_minutes = _get_site_setting_int(
                key="shift_grace_time",
                location_id=getattr(obj, "org_location_id", None),
                default_value=30,
            )

            # Check-in Timing
            if shift_start:
                checkin_diff = (checkin - shift_start).total_seconds() / 60  # Removed abs() to detect early vs late
                if -30 <= checkin_diff <= 30:
                    other_statuses.append("On-time Checked-in")
                elif checkin_diff < -grace_minutes:
                    other_statuses.append("Early Checked-in")
                elif checkin_diff > grace_minutes:
                    other_statuses.append("Delay Checked-in")

            # Check-out Timing
            if shift_end:
                checkout_diff = abs((checkout - shift_end).total_seconds()) / 60
                if checkout_diff <= grace_minutes:
                    other_statuses.append("On-time Checked-out")
                elif checkout < shift_end and checkout_diff < grace_minutes:
                    other_statuses.append("Early Checked-out")

        elif checkin and not checkout:
            attendance_status = ""
            other_statuses.append("Missed Checked-out")
        elif not checkin and shift:
            attendance_status = ""
            other_statuses.append("Missed Check-in")

        ws.append([
            checkin.strftime('%Y-%m-%d') if checkin else "",
            obj.guard.name,
            shift.name if shift else "",
            obj.org_location.name if obj.org_location else "",
            checkin.strftime('%Y-%m-%d %H:%M:%S') if checkin else "",
            checkout.strftime('%Y-%m-%d %H:%M:%S') if checkout else "",
            duration,
            attendance_status,
            ", ".join(other_statuses)
        ])
        row_count += 1  # Increment row count

    # Save to in-memory buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # Define file path
    filename = f"attendance_export_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)

    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())

    return {
        'file_path': file_path,
        'filename': filename,
        'row_count': row_count  # Return the number of data rows
    }


class AttendanceCheckinExportView(APIView):
    def get(self, request):
        # Parse filters
        date_filter = request.query_params.get("date_filter", "today")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        guard_id = request.query_params.get("guard")
        location_id = request.query_params.get("location")
        shift_id = request.query_params.get("shift")
        status_filter = request.query_params.get("status")
        defaulters = request.query_params.get("defaulters") == "true"
        
        log_msg = f"[ATTENDANCE_EXPORT_API] GET request - Filter: {date_filter}, Start: {start_date}, End: {end_date}, Guard ID: {guard_id}, Location ID: {location_id}, Shift ID: {shift_id}, Status: {status_filter}, Defaulters: {defaulters}"
        
        try:
            # Use internal helper function to avoid code duplication
            result = generate_attendance_excel_report_internal(
                date_filter=date_filter,
                start_date=start_date,
                end_date=end_date,
                guard_id=guard_id,
                location_id=location_id,
                shift_id=shift_id,
                status_filter=status_filter,
                defaulters=defaulters,
                request=request  # Pass request for timezone detection
            )
            
            file_path = result['file_path']
            
            # Return Excel file as HTTP response (not JSON)
            with open(file_path, 'rb') as f:
                excel_content = f.read()
            
            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
            # Generate a meaningful filename based on date filter
            if date_filter == 'custom' and start_date and end_date:
                filename = f"attendance_report_{start_date}_{end_date}.xlsx"
            else:
                filename = f"attendance_report_{timezone.now().strftime('%Y%m%d')}.xlsx"
            
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            error_msg = f"[ATTENDANCE_EXPORT_API] ValueError: {str(e)}"
            logger.error(error_msg)
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            error_msg = f"[ATTENDANCE_EXPORT_API] Exception: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return Response({"error": f"Failed to generate report: {str(e)}"}, status=500)


def generate_attendance_v3_excel_report_internal(
    date_filter='today',
    start_date=None,
    end_date=None,
    guard_id=None,
    location_id=None,
    shift_id=None,
    status_filter=None,
    defaulters=False,
    request=None,
    search=None,
    role=None,
    site_id=None,
):
    """
    V3 attendance export helper based on AttendanceCheckin master summary fields.
    """
    queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        user_tz = pytz.timezone('Asia/Kolkata')
        user_today = get_user_today(user_tz)

    if date_filter == "today":
        queryset = queryset.filter(shift_date=user_today)
    elif date_filter == "week":
        start_week = user_today - timedelta(days=user_today.weekday())
        end_week = min(start_week + timedelta(days=6), user_today)
        queryset = queryset.filter(shift_date__gte=start_week, shift_date__lte=end_week)
    elif date_filter == "month":
        start_of_month = user_today.replace(day=1)
        end_of_month = min(
            user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
            else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
            user_today
        )
        queryset = queryset.filter(shift_date__gte=start_of_month, shift_date__lte=end_of_month)
    elif date_filter == "custom" and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            end_date_obj = min(end_date_obj, user_today)
            queryset = queryset.filter(shift_date__gte=start_date_obj, shift_date__lte=end_date_obj)
        except ValueError:
            raise ValueError("Invalid custom date format. Use YYYY-MM-DD.")

    if guard_id:
        queryset = queryset.filter(guard_id=guard_id)
    if search and str(search).strip():
        s = str(search).strip()
        queryset = queryset.filter(
            Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s)
        )
    if role and str(role).strip().lower() not in ("", "all"):
        queryset = queryset.filter(guard__role__iexact=str(role).strip().lower())
    if location_id:
        queryset = queryset.filter(org_location_id=location_id)
    if shift_id:
        queryset = queryset.filter(shift_id=shift_id)
    if site_id:
        queryset = queryset.filter(site_id=site_id)
    if status_filter:
        queryset = _apply_attendance_v3_status_filter(queryset, status_filter)
    if defaulters:
        queryset = queryset.filter(
            checkin_time__isnull=False,
        ).filter(
            (Q(last_checkin_time__isnull=False) & Q(last_checkout_time__isnull=True)) |
            Q(last_checkin_time__gt=F("last_checkout_time")) |
            (Q(last_checkin_time__isnull=True) & Q(checkout_time__isnull=True))
        )

    # Match dashboard ordering in export: newest first.
    queryset = queryset.order_by("-shift_date", "-checkin_time", "-last_checkin_time", "-modified_on", "-id")

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Checkins V3"
    ws.append([
        "Shift Date",
        "Name",
        "Emp Code",
        "Designation",
        "Shift",
        "Location",
        "Site",
        "First Checkin",
        "Last Checkout",
        "Checkin Count",
        "Checkout Count",
        "Live State",
        "Sessions",
        "Duration (HH:MM)",
        "PA Status",
        "Remarks",
    ])

    rows = []
    for obj in queryset:
        first_checkin = to_user_timezone(obj.checkin_time, user_tz) if obj.checkin_time else None
        last_checkout = to_user_timezone(obj.last_checkout_time, user_tz) if obj.last_checkout_time else (to_user_timezone(obj.checkout_time, user_tz) if obj.checkout_time else None)
        duration = ""
        if obj.duration_minutes is not None:
            minutes_total = int(obj.duration_minutes)
            duration = f"{minutes_total // 60:02}:{minutes_total % 60:02}"
        pa_map = {
            "P": "Present",
            "OW": "On Work",
            "M": "Missed Checkout",
            "LD": "Less Duration",
            "A": "Absent (Legacy)",
        }
        pa_text = pa_map.get(obj.pa_status, "")
        live_state = "Checked In" if (
            obj.last_checkin_time and (not obj.last_checkout_time or obj.last_checkin_time > obj.last_checkout_time)
        ) else "Checked Out"
        if not pa_text and live_state == "Checked In":
            pa_text = "On Work"
        session_lines = _attendance_v3_build_session_lines_for_export(obj, user_tz)

        other_statuses = []
        if first_checkin and not last_checkout:
            other_statuses.append("Missed Checked-out")
        elif not first_checkin and obj.shift:
            other_statuses.append("Missed Check-in")

        effective_dt = first_checkin or (
            to_user_timezone(obj.last_checkin_time, user_tz) if obj.last_checkin_time else (
                to_user_timezone(obj.created_on, user_tz) if obj.created_on else None
            )
        )
        shift_date_key = obj.shift_date or (effective_dt.date() if effective_dt else date.min)
        shift_dt_key = effective_dt if effective_dt else datetime.min.replace(tzinfo=user_tz)
        rows.append((
            shift_date_key,
            shift_dt_key,
            [
                shift_date_key.strftime('%Y-%m-%d') if shift_date_key != date.min else "",
                obj.guard.name,
                getattr(obj.guard, "employee_code", None) or "",
                (getattr(obj.guard, "role", None) or "").strip(),
                obj.shift.name if obj.shift else "",
                obj.org_location.name if obj.org_location else "",
                obj.site.name if obj.site else "",
                first_checkin.strftime('%Y-%m-%d %H:%M:%S') if first_checkin else "",
                last_checkout.strftime('%Y-%m-%d %H:%M:%S') if last_checkout else "",
                int(obj.checkin_count or 0),
                int(obj.checkout_count or 0),
                live_state,
                session_lines,
                duration,
                pa_text,
                ", ".join(other_statuses),
            ],
        ))

    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)  # shift-date DESC, then time DESC
    rows.sort(key=lambda x: (str(x[2][3] or "").strip().upper() or "UNASSIGNED"))  # Then group by designation ASC
    row_count = 0
    for _dkey, _tkey, row_data in rows:
        ws.append(row_data)
        row_count += 1

    try:
        from openpyxl.styles import Alignment
        # Sessions column is 12th (L) after Name, Emp Code, Designation
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=12, max_col=12):
            for cell in row:
                cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions['L'].width = 42
    except Exception:
        pass

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    filename = f"attendance_export_v3_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())

    return {'file_path': file_path, 'filename': filename, 'row_count': row_count}


class AttendanceCheckinV3ExportView(APIView):
    def get(self, request):
        date_filter = request.query_params.get("date_filter", "today")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        guard_id = request.query_params.get("guard")
        location_id = request.query_params.get("location")
        shift_id = request.query_params.get("shift")
        status_filter = request.query_params.get("status")
        defaulters = request.query_params.get("defaulters") == "true"
        search = request.query_params.get("search")
        role = request.query_params.get("role")
        site_id = request.query_params.get("site_id")

        try:
            result = generate_attendance_v3_excel_report_internal(
                date_filter=date_filter,
                start_date=start_date,
                end_date=end_date,
                guard_id=guard_id,
                location_id=location_id,
                shift_id=shift_id,
                status_filter=status_filter,
                defaulters=defaulters,
                request=request,
                search=search,
                role=role,
                site_id=site_id,
            )
            with open(result['file_path'], 'rb') as f:
                excel_content = f.read()

            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            if date_filter == 'custom' and start_date and end_date:
                filename = f"attendance_report_v3_{start_date}_{end_date}.xlsx"
            else:
                filename = f"attendance_report_v3_{timezone.now().strftime('%Y%m%d')}.xlsx"

            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"[ATTENDANCE_EXPORT_V3_API] Exception: {str(e)}", exc_info=True)
            return Response({"error": f"Failed to generate v3 report: {str(e)}"}, status=500)


def generate_attendance_v4_excel_report_internal(
    date_filter='today',
    start_date=None,
    end_date=None,
    guard_id=None,
    location_id=None,
    shift_id=None,
    status_filter=None,
    defaulters=False,
    request=None,
    search=None,
    role=None,
    site_id=None,
):
    """
    V4 attendance export helper: Optimized by bulk-fetching CheckInLogs.
    """
    queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        user_tz = pytz.timezone('Asia/Kolkata')
        user_today = get_user_today(user_tz)

    if date_filter == "today":
        queryset = queryset.filter(shift_date=user_today)
    elif date_filter == "week":
        start_week = user_today - timedelta(days=user_today.weekday())
        end_week = min(start_week + timedelta(days=6), user_today)
        queryset = queryset.filter(shift_date__gte=start_week, shift_date__lte=end_week)
    elif date_filter == "month":
        start_of_month = user_today.replace(day=1)
        end_of_month = min(
            user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
            else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
            user_today
        )
        queryset = queryset.filter(shift_date__gte=start_of_month, shift_date__lte=end_of_month)
    elif date_filter == "custom" and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            end_date_obj = min(end_date_obj, user_today)
            queryset = queryset.filter(shift_date__gte=start_date_obj, shift_date__lte=end_date_obj)
        except ValueError:
            raise ValueError("Invalid custom date format. Use YYYY-MM-DD.")

    if guard_id:
        queryset = queryset.filter(guard_id=guard_id)
    if search and str(search).strip():
        s = str(search).strip()
        queryset = queryset.filter(
            Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s)
        )
    if role and str(role).strip().lower() not in ("", "all"):
        queryset = queryset.filter(guard__role__iexact=str(role).strip().lower())
    if location_id:
        queryset = queryset.filter(org_location_id=location_id)
    if site_id:
        queryset = queryset.filter(site_id=site_id)
    if shift_id:
        queryset = queryset.filter(shift_id=shift_id)
    if status_filter:
        queryset = _apply_attendance_v3_status_filter(queryset, status_filter)
    if defaulters:
        queryset = queryset.filter(
            checkin_time__isnull=False,
        ).filter(
            (Q(last_checkin_time__isnull=False) & Q(last_checkout_time__isnull=True)) |
            Q(last_checkin_time__gt=F("last_checkout_time")) |
            (Q(last_checkin_time__isnull=True) & Q(checkout_time__isnull=True))
        )

    queryset = queryset.order_by("-shift_date", "-checkin_time", "-last_checkin_time", "-modified_on", "-id")
    
    # Execute queryset
    page_items = list(queryset)
    
    # Bulk fetch logs
    prefetched_logs = {}
    if page_items:
        guard_ids = {obj.guard_id for obj in page_items}
        min_start_utc = None
        max_end_utc = None
        
        grace_minutes = _get_site_setting_int(
            key="shift_grace_time",
            location_id=location_id,
            default_value=30,
        )

        for obj in page_items:
            source_dt = obj.checkin_time or obj.created_on
            if not source_dt: continue
            shift_day = obj.shift_date or to_user_timezone(source_dt, user_tz).date()
            if obj.shift.end_time <= obj.shift.start_time and obj.shift_date is None:
                if to_user_timezone(source_dt, user_tz).time() < obj.shift.end_time:
                    shift_day = shift_day - timedelta(days=1)
            
            # Boundaries
            s_u = combine_date_time_in_user_tz(shift_day, obj.shift.start_time, user_tz).astimezone(pytz.UTC) - timedelta(minutes=grace_minutes)
            if obj.shift.end_time <= obj.shift.start_time:
                e_u = combine_date_time_in_user_tz(shift_day + timedelta(days=1), obj.shift.end_time, user_tz).astimezone(pytz.UTC) + timedelta(minutes=grace_minutes)
            else:
                e_u = combine_date_time_in_user_tz(shift_day, obj.shift.end_time, user_tz).astimezone(pytz.UTC) + timedelta(minutes=grace_minutes)
            
            if min_start_utc is None or s_u < min_start_utc: min_start_utc = s_u
            if max_end_utc is None or e_u > max_end_utc: max_end_utc = e_u

        if min_start_utc and max_end_utc:
            all_logs = CheckInLog.objects.filter(
                guard_id__in=guard_ids,
                timestamp__gte=min_start_utc,
                timestamp__lt=max_end_utc,
            ).order_by("timestamp")
            for l in all_logs:
                if l.guard_id not in prefetched_logs: prefetched_logs[l.guard_id] = []
                prefetched_logs[l.guard_id].append(l)

    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Checkins V4"
    ws.append([
        "Shift Date", "Name", "Emp Code", "Designation", "Shift", "Location",
        "First Checkin", "Last Checkout", "Checkin Count", "Checkout Count",
        "Live State", "Sessions", "Duration (HH:MM)", "PA Status", "Remarks",
    ])

    rows = []
    for obj in page_items:
        # Optimized session lines logic without per-row DB query
        guard_logs = prefetched_logs.get(obj.guard_id, [])
        source_dt = obj.checkin_time or obj.created_on
        shift_day = obj.shift_date or to_user_timezone(source_dt, user_tz).date()
        if obj.shift.end_time <= obj.shift.start_time and obj.shift_date is None:
            if to_user_timezone(source_dt, user_tz).time() < obj.shift.end_time:
                shift_day = shift_day - timedelta(days=1)
        
        s_u = combine_date_time_in_user_tz(shift_day, obj.shift.start_time, user_tz).astimezone(pytz.UTC) - timedelta(minutes=grace_minutes)
        if obj.shift.end_time <= obj.shift.start_time:
            e_u = combine_date_time_in_user_tz(shift_day + timedelta(days=1), obj.shift.end_time, user_tz).astimezone(pytz.UTC) + timedelta(minutes=grace_minutes)
        else:
            e_u = combine_date_time_in_user_tz(shift_day, obj.shift.end_time, user_tz).astimezone(pytz.UTC) + timedelta(minutes=grace_minutes)

        matched_logs = [l for l in guard_logs if l.assignment_id == obj.assignment_id and l.shift_id == obj.shift_id and s_u <= l.timestamp < e_u]
        
        lines = []
        open_checkin = None
        idx = 1
        for log in matched_logs:
            if log.type == "checkin":
                open_checkin = log.timestamp
            elif log.type == "checkout" and open_checkin is not None and log.timestamp > open_checkin:
                in_local = to_user_timezone(open_checkin, user_tz)
                out_local = to_user_timezone(log.timestamp, user_tz)
                mins = int((log.timestamp - open_checkin).total_seconds() // 60)
                lines.append(f"{idx}) {in_local.strftime('%H:%M')} -> {out_local.strftime('%H:%M')} ({mins//60}h {mins%60}m)")
                idx += 1
                open_checkin = None
        if open_checkin:
            lines.append(f"{idx}) {to_user_timezone(open_checkin, user_tz).strftime('%H:%M')} -> Open")
        session_lines = "\n".join(lines)

        first_checkin = to_user_timezone(obj.checkin_time, user_tz) if obj.checkin_time else None
        last_checkout = to_user_timezone(obj.last_checkout_time, user_tz) if obj.last_checkout_time else (to_user_timezone(obj.checkout_time, user_tz) if obj.checkout_time else None)
        duration = ""
        if obj.duration_minutes is not None:
            m = int(obj.duration_minutes)
            duration = f"{m // 60:02}:{m % 60:02}"
        
        pa_map = {"P": "Present", "OW": "On Work", "M": "Missed Checkout", "LD": "Less Duration", "A": "Absent (Legacy)"}
        live_state = "Checked In" if (obj.last_checkin_time and (not obj.last_checkout_time or obj.last_checkin_time > obj.last_checkout_time)) else "Checked Out"
        pa_text = pa_map.get(obj.pa_status, "")
        if not pa_text and live_state == "Checked In": pa_text = "On Work"

        effective_dt = first_checkin or (to_user_timezone(obj.last_checkin_time, user_tz) if obj.last_checkin_time else to_user_timezone(obj.created_on, user_tz))
        s_date_key = obj.shift_date or (effective_dt.date() if effective_dt else date.min)
        
        rows.append((
            s_date_key,
            effective_dt or datetime.min.replace(tzinfo=user_tz),
            [
                s_date_key.strftime('%Y-%m-%d') if s_date_key != date.min else "",
                obj.guard.name,
                getattr(obj.guard, "employee_code", None) or "",
                (getattr(obj.guard, "role", None) or "").strip(),
                obj.shift.name if obj.shift else "",
                obj.org_location.name if obj.org_location else "",
                first_checkin.strftime('%Y-%m-%d %H:%M:%S') if first_checkin else "",
                last_checkout.strftime('%Y-%m-%d %H:%M:%S') if last_checkout else "",
                int(obj.checkin_count or 0),
                int(obj.checkout_count or 0),
                live_state,
                session_lines,
                duration,
                pa_text,
                ", ".join(["Missed Checked-out"] if first_checkin and not last_checkout else (["Missed Check-in"] if not first_checkin and obj.shift else [])),
            ],
        ))

    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    rows.sort(key=lambda x: (str(x[2][3] or "").strip().upper() or "UNASSIGNED"))
    for _, _, r_data in rows:
        ws.append(r_data)

    try:
        from openpyxl.styles import Alignment
        for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=12, max_col=12):
            for cell in row: cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions['L'].width = 42
    except: pass

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    filename = f"attendance_export_v4_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)
    with open(file_path, 'wb') as f: f.write(buffer.getvalue())
    return {'file_path': file_path, 'filename': filename, 'row_count': len(page_items)}


class AttendanceCheckinV4ExportView(APIView):
    def get(self, request):
        params = request.query_params
        try:
            result = generate_attendance_v4_excel_report_internal(
                date_filter=params.get("date_filter", "today"),
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
                guard_id=params.get("guard"),
                location_id=params.get("location"),
                shift_id=params.get("shift"),
                status_filter=params.get("status"),
                defaulters=params.get("defaulters") == "true",
                request=request,
                search=params.get("search"),
                role=params.get("role"),
                site_id=params.get("site_id"),
            )
            with open(result['file_path'], 'rb') as f:
                content = f.read()
            response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            fname = f"attendance_report_v4_{params.get('start_date', '')}_{params.get('end_date', '')}.xlsx" if params.get('date_filter') == 'custom' else f"attendance_report_v4_{timezone.now().strftime('%Y%m%d')}.xlsx"
            response["Content-Disposition"] = f'attachment; filename="{fname}"'
            return response
        except ValueError as e: return Response({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"[ATTENDANCE_EXPORT_V4_API] Exception: {str(e)}", exc_info=True)
            return Response({"error": f"Failed to generate v4 report: {str(e)}"}, status=500)


class AttendanceCheckinV4PdfExportView(APIView):
    """Daily attendance detail summary PDF (landscape). Same filters as export_v4."""

    def get(self, request):
        from dashboard.attendance_detail_pdf import generate_attendance_v4_pdf_report_internal

        params = request.query_params
        try:
            result = generate_attendance_v4_pdf_report_internal(
                date_filter=params.get("date_filter", "today"),
                start_date=params.get("start_date"),
                end_date=params.get("end_date"),
                guard_id=params.get("guard"),
                location_id=params.get("location"),
                shift_id=params.get("shift"),
                status_filter=params.get("status"),
                defaulters=params.get("defaulters") == "true",
                request=request,
                search=params.get("search"),
                role=params.get("role"),
                site_id=params.get("site_id"),
            )
            content = result.get("pdf_bytes")
            if not content:
                with open(result["file_path"], "rb") as f:
                    content = f.read()
            response = HttpResponse(content, content_type="application/pdf")
            if params.get("date_filter") == "custom" and params.get("start_date"):
                fname = f"attendance_detail_{params.get('start_date')}_{params.get('end_date', '')}.pdf"
            else:
                fname = f"attendance_detail_{timezone.now().strftime('%Y%m%d')}.pdf"
            response["Content-Disposition"] = f'attachment; filename="{fname}"'
            return response
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"[ATTENDANCE_EXPORT_V4_PDF] Exception: {str(e)}", exc_info=True)
            return Response({"error": f"Failed to generate PDF report: {str(e)}"}, status=500)


def _append_monthly_day_totals(row, date_range):
    """
    Count summary days from daily cells:
    - present_days: count of "P"
    - weekoff_days: count of "W"
    """
    present = 0
    weekoff = 0
    for d in date_range:
        key = d.strftime("%d-%b")
        val = row.get(key, "")
        if val == "P":
            present += 1
        elif val == "W":
            weekoff += 1
    row["present_days"] = present
    row["weekoff_days"] = weekoff


def _monthly_normalize_status(raw_status):
    status_val = str(raw_status or "").strip().upper()
    if status_val == "A":
        # Legacy absent value should be represented as LD.
        return "LD"
    if status_val in {"P", "OW", "LD", "W", "M"}:
        return status_val
    return ""


def _monthly_group_rows_for_excel(summary_data, date_range):
    grouped = defaultdict(list)
    for row in summary_data:
        rank = (row.get("designation") or "").strip() or "UNASSIGNED"
        grouped[rank].append(row)

    ordered_ranks = sorted(grouped.keys(), key=lambda x: x.lower())
    groups = []
    for rank in ordered_ranks:
        members = sorted(grouped[rank], key=lambda r: (r.get("name") or "").lower())
        date_present_totals = {}
        date_weekoff_totals = {}
        for d in date_range:
            key = d.strftime("%d-%b")
            present_count = 0
            weekoff_count = 0
            for m in members:
                v = m.get(key, "")
                if v == "W":
                    weekoff_count += 1
                elif v in {"P", "OW", "LD", "M"}:
                    present_count += 1
            date_present_totals[key] = present_count
            date_weekoff_totals[key] = weekoff_count

        groups.append(
            {
                "rank": rank.upper(),
                "members": members,
                "date_present_totals": date_present_totals,
                "date_weekoff_totals": date_weekoff_totals,
            }
        )
    return groups


def _get_monthly_attendance_summary_data(
    month=None,
    start_date_str=None,
    end_date_str=None,
    location_id=None,
    user_id=None,
    search=None,
    role=None,
    request=None,
):
    """
    Internal helper function to generate monthly attendance summary data.
    This contains the business logic shared by both JSON and Excel endpoints.
    
    Args:
        month: Optional string in YYYY-MM format
        start_date_str: Optional string in YYYY-MM-DD format (for custom range)
        end_date_str: Optional string in YYYY-MM-DD format (for custom range)
        location_id: Optional UUID string to filter by location
        user_id: Optional UUID string to filter by guard
        request: Optional request object for timezone detection (to determine "today" for future date check)
    
    Returns:
        dict with 'summary_data' (list of dicts), 'date_range' (list of dates), 
        'start_date', and 'end_date'
    """
    # Parse and validate date range
    if month:
        try:
            year, month_num = map(int, month.split("-"))
            if not (1 <= month_num <= 12):
                raise ValueError("Month must be between 1 and 12")
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid month format. Use YYYY-MM. Error: {e}")
    elif start_date_str and end_date_str:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)
        if not start_date or not end_date:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")
    else:
        today = now().date()
        start_date = today.replace(day=1)
        end_date = today

    # Get assignments within date range
    assignments = Assignment.objects.filter(
        start_date__lte=end_date,
        end_date__gte=start_date,
        is_deleted=False
    )

    if location_id:
        assignments = assignments.filter(location_id=location_id)
    if user_id:
        assignments = assignments.filter(guard_id=user_id)
    elif search and str(search).strip():
        s = str(search).strip()
        assignments = assignments.filter(
            Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s)
        )
    if not user_id and role and str(role).strip().lower() not in ("", "all"):
        assignments = assignments.filter(guard__role__iexact=str(role).strip().lower())

    # Get user timezone and today for future date check
    # IMPORTANT: Get user_today BEFORE generating date_range to ensure correct future date detection
    # For superadmin viewing location-specific reports, uses location admin's timezone
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        # Fallback: use default timezone if no request (for Celery tasks)
        user_tz = pytz.timezone('Asia/Kolkata')
        user_today = get_user_today(user_tz)

    # Generate date range
    # For month parameter, always include full month (even future dates will show as "-")
    # For custom range, limit to user_today if end_date is in future
    if month:
        # Keep full month range - future dates will be handled in the loop to show "-"
        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    else:
        # For custom range, limit to user_today if end_date is in future
        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        if end_date > user_today:
            date_range = [d for d in date_range if d <= user_today]
            end_date = user_today

    # Group assignments by guard + location
    grouped = defaultdict(lambda: {
        "guard": None,
        "location": None,
        "assignments": []
    })

    for assignment in assignments.select_related("guard", "location"):
        key = (assignment.guard.id, assignment.location.id if assignment.location else None)
        grouped[key]["guard"] = assignment.guard
        grouped[key]["location"] = assignment.location.name if assignment.location else "N/A"
        grouped[key]["assignments"].append(assignment)

    summary_data = []
    grouped_guard_ids = [gid for (gid, _loc_id) in grouped.keys()]
    grouped_location_ids = [loc_id for (_gid, loc_id) in grouped.keys() if loc_id]
    weekoff_lookup = set()
    if grouped_guard_ids:
        weekoff_qs = AttendanceWeekOff.objects.filter(
            user_id__in=grouped_guard_ids,
            weekoff_date__gte=start_date,
            weekoff_date__lte=end_date,
        )
        if grouped_location_ids:
            weekoff_qs = weekoff_qs.filter(location_id__in=grouped_location_ids)
        weekoff_lookup = {
            (str(uid), str(loc), wd)
            for uid, loc, wd in weekoff_qs.values_list("user_id", "location_id", "weekoff_date")
        }

    # Build summary for each guard-location combination
    for (guard_id, loc_id), data in grouped.items():
        guard = data["guard"]
        location = data["location"]
        row = {
            "user_id": str(guard_id),
            "location_id": str(loc_id) if loc_id else None,
            "name": guard.name,
            "location": location,
            "employee_code": getattr(guard, "employee_code", None) or "",
            "designation": (getattr(guard, "role", None) or "").strip(),
        }

        # Check attendance for each date
        for date in date_range:
            cell_key = date.strftime("%d-%b")
            # Check if date is in the future - if so, show "-"
            if date > user_today:
                row[cell_key] = ""
                continue

            if (str(guard_id), str(loc_id), date) in weekoff_lookup:
                row[cell_key] = "W"
                continue

            active_assignments = [
                a for a in data["assignments"]
                if a.start_date <= date <= a.end_date
            ]

            if not active_assignments:
                row[cell_key] = ""
                continue

            # Use strict shift-instance windows against AttendanceCheckin master table.
            attendance = None
            candidate_attendance = []
            for assgn in active_assignments:
                if not assgn.shift or not assgn.location:
                    continue
                w_start_utc, w_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                    date,
                    assgn.shift,
                    user_tz,
                    location_id=getattr(assgn.location, "id", None),
                )
                att = AttendanceCheckin.objects.filter(
                    guard=guard,
                    assignment=assgn,
                    shift=assgn.shift,
                    org_location=assgn.location,
                ).filter(
                    Q(checkin_time__gte=w_start_utc, checkin_time__lt=w_end_utc) |
                    Q(created_on__gte=w_start_utc, created_on__lt=w_end_utc)
                ).order_by("-last_checkout_time", "-checkout_time", "-modified_on").first()
                if att:
                    candidate_attendance.append(att)

            if candidate_attendance:
                candidate_attendance.sort(
                    key=lambda x: (
                        x.last_checkout_time or datetime.min.replace(tzinfo=pytz.UTC),
                        x.checkout_time or datetime.min.replace(tzinfo=pytz.UTC),
                        x.modified_on or datetime.min.replace(tzinfo=pytz.UTC),
                    ),
                    reverse=True,
                )
                attendance = candidate_attendance[0]

            if not attendance:
                row[cell_key] = ""
                continue

            # Prefer persisted pa_status (set after checkout_v2/v3 refresh).
            if attendance.pa_status:
                row[cell_key] = _monthly_normalize_status(attendance.pa_status)
            elif attendance.checkout_time or attendance.last_checkout_time:
                # Fallback for any records created before the new persist logic.
                computed_status = (
                    _attendance_v3_compute_pa_status_from_duration(
                        attendance.duration_minutes, attendance.shift
                    )
                    or "LD"
                )
                row[cell_key] = _monthly_normalize_status(computed_status)
            else:
                # Still on duty (open shift) should be shown as OW.
                row[cell_key] = (
                    "OW" if (attendance.checkin_time or attendance.last_checkin_time) else ""
                )

        _append_monthly_day_totals(row, date_range)

        summary_data.append(row)

    summary_data.sort(key=lambda x: (x.get('designation', '') or '').strip().upper() or 'UNASSIGNED')

    return {
        'summary_data': summary_data,
        'date_range': date_range,
        'start_date': start_date,
        'end_date': end_date
    }


def generate_monthly_attendance_summary_excel_internal(
    month=None,
    start_date_str=None,
    end_date_str=None,
    location_id=None,
    user_id=None,
    search=None,
    role=None,
    request=None,
    include_location_column=None,
):
    """
    Internal helper function to generate monthly attendance summary Excel report.
    This contains the business logic that both the API endpoint and Celery tasks can use.
    
    Args:
        month: Optional string in YYYY-MM format
        start_date_str: Optional string in YYYY-MM-DD format (for custom range)
        end_date_str: Optional string in YYYY-MM-DD format (for custom range)
        location_id: Optional UUID string to filter by location
        user_id: Optional UUID string to filter by guard
        request: Optional request object for timezone detection (to determine "today" for future date check)
        include_location_column: If True, include Location column. If None, True when request.user.is_superuser else False.
    
    Returns: dict with 'file_path', 'filename', 'start_date', 'end_date', 'row_count'
    """
    # Get summary data using core helper (pass request for timezone detection)
    result = _get_monthly_attendance_summary_data(
        month=month,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        location_id=location_id,
        user_id=user_id,
        search=search,
        role=role,
        request=request  # Pass request for timezone detection
    )
    
    summary_data = result['summary_data']
    date_range = result['date_range']
    start_date = result['start_date']
    end_date = result['end_date']

    if include_location_column is None:
        include_location_column = bool(
            request and getattr(request.user, "is_superuser", False)
        )
    
    # Create Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Monthly Attendance Summary"
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    groups = _monthly_group_rows_for_excel(summary_data, date_range)
    left_headers = ["SNO", "ID NO", "RANK", "NAME"]
    if include_location_column:
        left_headers.append("LOCATION")
    right_headers = ["Total Days", "Week off"]
    date_headers = [f"{d.day}-{d.strftime('%b-%y')}" for d in date_range]
    day_headers = [d.strftime("%a").upper() for d in date_range]
    all_headers = left_headers + date_headers + right_headers
    total_cols = len(all_headers)

    # Title row (with location context when available)
    month_label = start_date.strftime("%B-%Y").upper()
    location_label = "ALL LOCATIONS"
    if location_id:
        loc_obj = Location.objects.filter(id=location_id, is_deleted=False).first()
        if loc_obj and getattr(loc_obj, "name", None):
            location_label = str(loc_obj.name).strip().upper()
    title = f"{location_label} MONTHLY ATTENDANCE FOR THE MONTH OF {month_label}"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols)
    ws.cell(row=1, column=1, value=title)

    # Header rows
    for c, h in enumerate(left_headers, start=1):
        ws.cell(row=2, column=c, value=h)
    for idx, h in enumerate(date_headers, start=len(left_headers) + 1):
        ws.cell(row=2, column=idx, value=h)
    ws.cell(row=2, column=total_cols - 1, value=right_headers[0])
    ws.cell(row=2, column=total_cols, value=right_headers[1])
    for idx, h in enumerate(day_headers, start=len(left_headers) + 1):
        ws.cell(row=3, column=idx, value=h)
    # Merge left and right headers vertically to mimic print layout.
    for c in range(1, len(left_headers) + 1):
        ws.merge_cells(start_row=2, start_column=c, end_row=3, end_column=c)
    ws.merge_cells(start_row=2, start_column=total_cols - 1, end_row=3, end_column=total_cols - 1)
    ws.merge_cells(start_row=2, start_column=total_cols, end_row=3, end_column=total_cols)

    thin = Side(style="thin", color="8A97A6")
    medium = Side(style="medium", color="4F5B66")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    strong_border = Border(left=medium, right=medium, top=medium, bottom=medium)
    header_fill = PatternFill(fill_type="solid", start_color="B7CCE2", end_color="B7CCE2")
    subtotal_fill = PatternFill(fill_type="solid", start_color="F5E7DB", end_color="F5E7DB")
    title_fill = PatternFill(fill_type="solid", start_color="B7CCE2", end_color="B7CCE2")
    weekoff_mark_fill = PatternFill(fill_type="solid", start_color="8CCFF7", end_color="8CCFF7")

    base_font_name = "Arial"
    for row_no in [1, 2, 3]:
        for col_no in range(1, total_cols + 1):
            cell = ws.cell(row=row_no, column=col_no)
            cell.fill = title_fill if row_no == 1 else header_fill
            cell.font = Font(
                name=base_font_name,
                bold=True,
                size=11 if row_no == 1 else (9 if row_no == 2 else 8),
            )
            if row_no == 2 and (len(left_headers) < col_no < total_cols - 1):
                cell.alignment = Alignment(horizontal="center", vertical="center", text_rotation=90)
            elif row_no == 3:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
            else:
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = strong_border if row_no in (1, 2, 3) else border

    ws.row_dimensions[1].height = 24
    ws.row_dimensions[2].height = 66
    ws.row_dimensions[3].height = 22

    current_row = 4
    serial = 1
    row_count = 0
    designation_day_totals = defaultdict(lambda: defaultdict(int))
    designation_weekoff_totals = defaultdict(lambda: defaultdict(int))
    designation_total_days = defaultdict(int)
    designation_total_weekoff = defaultdict(int)

    for group in groups:
        rank_name = group["rank"]
        members = group["members"]
        for m in members:
            statuses = []
            user_total_days = 0
            user_weekoff_days = 0
            for d in date_range:
                key = d.strftime("%d-%b")
                status_val = _monthly_normalize_status(m.get(key, ""))
                statuses.append(status_val)
                if status_val == "W":
                    user_weekoff_days += 1
                elif status_val:
                    user_total_days += 1
                designation_day_totals[rank_name][key] += 1 if status_val and status_val != "W" else 0
                designation_weekoff_totals[rank_name][key] += 1 if status_val == "W" else 0

            designation_total_days[rank_name] += user_total_days
            designation_total_weekoff[rank_name] += user_weekoff_days

            member_name = (m.get("name") or "")
            member_name = str(member_name).upper() if member_name else ""
            values = [serial, m.get("employee_code") or "", rank_name, member_name]
            if include_location_column:
                values.append(m.get("location") or "")
            values.extend(statuses)
            values.extend([user_total_days, user_weekoff_days])
            for col_no, val in enumerate(values, start=1):
                cell = ws.cell(row=current_row, column=col_no, value=val)
                cell.border = border
                cell.font = Font(name=base_font_name, size=10)
                if col_no in (1, 2):
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_no == 3:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                elif col_no <= len(left_headers):
                    cell.alignment = Alignment(horizontal="left", vertical="center")
                else:
                    cell.alignment = Alignment(horizontal="center", vertical="center")
                # Highlight weekoff marks like the reference Excel.
                is_date_col = len(left_headers) < col_no <= (len(left_headers) + len(date_range))
                if is_date_col and str(val).upper() == "W":
                    cell.fill = weekoff_mark_fill
            ws.row_dimensions[current_row].height = 20
            current_row += 1
            serial += 1
            row_count += 1

        # Group subtotal row
        subtotal_vals = ["", "", "TOTAL PRESENT", ""]
        if include_location_column:
            subtotal_vals.append("")
        day_totals = [group["date_present_totals"].get(d.strftime("%d-%b"), 0) for d in date_range]
        subtotal_vals.extend(day_totals)
        subtotal_vals.extend([sum(day_totals), sum(group["date_weekoff_totals"].values())])
        for col_no, val in enumerate(subtotal_vals, start=1):
            cell = ws.cell(row=current_row, column=col_no, value=val)
            cell.fill = subtotal_fill
            cell.font = Font(name=base_font_name, bold=True, size=10)
            cell.border = strong_border if col_no <= len(left_headers) + len(date_range) else border
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[current_row].height = 20
        current_row += 1

    # Footer summary matrix (designation totals by day)
    for rank_name in sorted(designation_day_totals.keys()):
        footer_vals = ["", "", rank_name, ""]
        if include_location_column:
            footer_vals.append("")
        day_vals = [designation_day_totals[rank_name].get(d.strftime("%d-%b"), 0) for d in date_range]
        footer_vals.extend(day_vals)
        footer_vals.extend([designation_total_days[rank_name], designation_total_weekoff[rank_name]])
        for col_no, val in enumerate(footer_vals, start=1):
            cell = ws.cell(row=current_row, column=col_no, value=val)
            cell.font = Font(name=base_font_name, bold=True, size=10)
            cell.border = border
            cell.alignment = Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[current_row].height = 20
        current_row += 1

    # Weekoff totals row
    weekoff_day_vals = []
    for d in date_range:
        key = d.strftime("%d-%b")
        weekoff_day_vals.append(sum(designation_weekoff_totals[r].get(key, 0) for r in designation_weekoff_totals.keys()))
    weekoff_total_vals = ["", "", "WEEK OFF", ""]
    if include_location_column:
        weekoff_total_vals.append("")
    weekoff_total_vals.extend(weekoff_day_vals)
    weekoff_total_vals.extend([sum(weekoff_day_vals), sum(designation_total_weekoff.values())])
    for col_no, val in enumerate(weekoff_total_vals, start=1):
        cell = ws.cell(row=current_row, column=col_no, value=val)
        cell.font = Font(name=base_font_name, bold=True, size=10)
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[current_row].height = 20
    current_row += 1

    # Total head count row
    head_count_vals = ["", "", "Total Head count", ""]
    if include_location_column:
        head_count_vals.append("")
    day_head_counts = []
    for d in date_range:
        key = d.strftime("%d-%b")
        count = sum(1 for r in summary_data if _monthly_normalize_status(r.get(key, "")) != "")
        day_head_counts.append(count)
    head_count_vals.extend(day_head_counts)
    head_count_vals.extend([sum(designation_total_days.values()), sum(designation_total_weekoff.values())])
    for col_no, val in enumerate(head_count_vals, start=1):
        cell = ws.cell(row=current_row, column=col_no, value=val)
        cell.font = Font(name=base_font_name, bold=True, size=10)
        cell.border = border
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[current_row].height = 20

    # Apply borders/alignment for all data rows
    for r in range(4, current_row + 1):
        for c in range(1, total_cols + 1):
            cell = ws.cell(row=r, column=c)
            if cell.border != border:
                cell.border = border
            if not cell.alignment:
                cell.alignment = Alignment(horizontal="center", vertical="center")

    # Column widths
    widths = [6, 11, 20, 26]
    if include_location_column:
        widths.append(20)
    widths.extend([4.2] * len(date_range))
    widths.extend([8, 8])
    for idx, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(idx)].width = w

    # Freeze title/header + identity columns, so day columns scroll horizontally
    # while SNO/ID/RANK/NAME stay sticky like the reference.
    freeze_col = len(left_headers) + 1
    ws.freeze_panes = ws.cell(row=4, column=freeze_col)
    
    # Save to in-memory buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    # Define file path
    filename = f"attendance_summary_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)
    
    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())
    
    return {
        'file_path': file_path,
        'filename': filename,
        'start_date': start_date,
        'end_date': end_date,
        'row_count': row_count  # Number of guards in the report
    }


def _get_monthly_attendance_summary_data_v2(
    month=None, start_date_str=None, end_date_str=None, location_id=None, site_id=None, user_id=None, search=None, role=None, request=None,
):
    if month:
        try:
            year, month_num = map(int, month.split("-"))
            start_date, end_date = datetime(year, month_num, 1).date(), datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except: raise ValueError("Invalid month format. Use YYYY-MM.")
    elif start_date_str and end_date_str:
        start_date, end_date = parse_date(start_date_str), parse_date(end_date_str)
        if not start_date or not end_date: raise ValueError("Invalid date format. Use YYYY-MM-DD")
    else:
        today = now().date()
        start_date, end_date = today.replace(day=1), today

    aq = Assignment.objects.filter(start_date__lte=end_date, end_date__gte=start_date, is_deleted=False)
    if location_id: aq = aq.filter(location_id=location_id)
    if user_id: aq = aq.filter(guard_id=user_id)
    elif search and str(search).strip():
        s = str(search).strip()
        aq = aq.filter(Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s))
    if not user_id and role and str(role).strip().lower() not in ("", "all"):
        aq = aq.filter(guard__role__iexact=str(role).strip().lower())

    assignments = list(aq.select_related("guard", "location", "shift"))
    if request: tz, today = get_user_timezone_from_request(request, location_id=location_id), get_user_today(get_user_timezone_from_request(request, location_id=location_id))
    else: tz, today = pytz.timezone('Asia/Kolkata'), get_user_today(pytz.timezone('Asia/Kolkata'))
    
    date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    if not month and end_date > today: date_range = [d for d in date_range if d <= today]; end_date = today

    grouped = defaultdict(lambda: {"guard": None, "location": None, "assignments": []})
    for a in assignments:
        key = (a.guard_id, a.location_id if a.location else None)
        grouped[key]["guard"], grouped[key]["location"] = a.guard, a.location.name if a.location else "N/A"
        grouped[key]["assignments"].append(a)

    gids, lids = [gid for (gid, _) in grouped.keys()], list({lid for (_, lid) in grouped.keys() if lid})
    w_lookup = set()
    if gids:
        wq = AttendanceWeekOff.objects.filter(user_id__in=gids, weekoff_date__gte=start_date, weekoff_date__lte=end_date)
        if location_id or lids: wq = wq.filter(location_id__in=([location_id] if location_id else lids))
        w_lookup = {(str(u), str(l), wd) for u, l, wd in wq.values_list("user_id", "location_id", "weekoff_date")}
    
    # Pre-fetch site names if site_id is provided or if we want to show it
    site_names = {}
    if site_id:
        site_obj = LocationSite.objects.filter(id=site_id).only('name').first()
        if site_obj:
            site_names[str(site_id)] = site_obj.name

    a_lookup = {}
    if gids:
        att_qs = AttendanceCheckin.objects.filter(guard_id__in=gids, shift_date__range=(start_date, end_date))
        if site_id:
            att_qs = att_qs.filter(site_id=site_id)
        for att in att_qs.values('guard_id', 'org_location_id', 'shift_date', 'pa_status', 'last_checkout_time', 'checkout_time', 'duration_minutes', 'shift_id', 'modified_on', 'checkin_time', 'last_checkin_time', 'site_id'):
            key = (str(att['guard_id']), str(att['org_location_id']) if att['org_location_id'] else None, att['shift_date'])
            existing = a_lookup.get(key)
            if existing:
                new_l, old_l = att['last_checkout_time'] or att['checkout_time'] or att['modified_on'], existing['last_checkout_time'] or existing['checkout_time'] or existing['modified_on']
                if new_l and (not old_l or new_l > old_l): a_lookup[key] = att
            else: a_lookup[key] = att

    summary_data = []
    for (g_pk, l_pk), data in grouped.items():
        guard, sgid, slid = data["guard"], str(g_pk), str(l_pk) if l_pk else None
        row = {
            "user_id": sgid, 
            "location_id": slid, 
            "name": guard.name, 
            "location": data["location"], 
            "employee_code": getattr(guard, "employee_code", None) or "", 
            "designation": (getattr(guard, "role", None) or "").strip(),
            "site_name": site_names.get(str(site_id)) if site_id else "",
            "site_map": {}
        }
        for date in date_range:
            k = date.strftime("%d-%b")
            if date > today: row[k] = ""; continue
            if (sgid, slid, date) in w_lookup: row[k] = "W"; continue
            if not any(a.start_date <= date <= a.end_date for a in data["assignments"]): row[k] = ""; continue
            att = a_lookup.get((sgid, slid, date))
            if not att: row[k] = ""; continue
            row["site_map"][k] = str(att['site_id']) if att.get('site_id') else None
            if att.get('pa_status'): row[k] = _monthly_normalize_status(att['pa_status'])
            elif att.get('checkout_time') or att.get('last_checkout_time'):
                 s_obj = next((a.shift for a in data['assignments'] if a.shift_id == att['shift_id']), None)
                 row[k] = _monthly_normalize_status(_attendance_v3_compute_pa_status_from_duration(att['duration_minutes'], s_obj) or "LD")
            else: row[k] = "OW" if (att.get('checkin_time') or att.get('last_checkin_time')) else ""
        _append_monthly_day_totals(row, date_range); summary_data.append(row)
    
    summary_data.sort(key=lambda x: (x.get('designation', '') or '').strip().upper() or 'UNASSIGNED')
    
    return {'summary_data': summary_data, 'date_range': date_range, 'start_date': start_date, 'end_date': end_date}

def generate_monthly_attendance_summary_excel_internal_v2(
    month=None, start_date_str=None, end_date_str=None, location_id=None, site_id=None, user_id=None, search=None, role=None, request=None, include_location_column=None,
):
    result = _get_monthly_attendance_summary_data_v2(month=month, start_date_str=start_date_str, end_date_str=end_date_str, location_id=location_id, site_id=site_id, user_id=user_id, search=search, role=role, request=request)
    summary_data, date_range, start_date, end_date = result['summary_data'], result['date_range'], result['start_date'], result['end_date']
    if include_location_column is None: include_location_column = bool(request and getattr(request.user, "is_superuser", False))
    wb = Workbook(); ws = wb.active; ws.title = "Monthly Attendance Summary"
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side; from openpyxl.utils import get_column_letter
    groups = _monthly_group_rows_for_excel(summary_data, date_range)
    l_heads = ["SNO", "ID NO", "RANK", "NAME"]; 
    if include_location_column: l_heads.append("LOCATION")
    r_heads, d_heads, dy_heads = ["Total Days", "Week off"], [f"{d.day}-{d.strftime('%b-%y')}" for d in date_range], [d.strftime("%a").upper() for d in date_range]
    total_cols = len(l_heads + d_heads + r_heads); m_lbl, l_lbl = start_date.strftime("%B-%Y").upper(), "ALL LOCATIONS"
    if location_id:
        loc = Location.objects.filter(id=location_id, is_deleted=False).values('name').first()
        if loc:
            l_lbl = str(loc['name']).strip().upper()
            if site_id:
                site = LocationSite.objects.filter(id=site_id, is_active=True).values('name').first()
                if site:
                    l_lbl = f"{l_lbl} - {str(site['name']).strip().upper()}"
    ws.merge_cells(start_row=1, start_column=1, end_row=1, end_column=total_cols); ws.cell(row=1, column=1, value=f"{l_lbl} MONTHLY ATTENDANCE FOR THE MONTH OF {m_lbl}")
    for c, h in enumerate(l_heads, start=1): ws.cell(row=2, column=c, value=h)
    for idx, h in enumerate(d_heads, start=len(l_heads) + 1): ws.cell(row=2, column=idx, value=h)
    ws.cell(row=2, column=total_cols - 1, value=r_heads[0]); ws.cell(row=2, column=total_cols, value=r_heads[1])
    for idx, h in enumerate(dy_heads, start=len(l_heads) + 1): ws.cell(row=3, column=idx, value=h)
    for c in range(1, len(l_heads) + 1): ws.merge_cells(start_row=2, start_column=c, end_row=3, end_column=c)
    ws.merge_cells(start_row=2, start_column=total_cols - 1, end_row=3, end_column=total_cols - 1); ws.merge_cells(start_row=2, start_column=total_cols, end_row=3, end_column=total_cols)
    thin, med = Side(style="thin", color="8A97A6"), Side(style="medium", color="4F5B66")
    brd, sbrd = Border(left=thin, right=thin, top=thin, bottom=thin), Border(left=med, right=med, top=med, bottom=med)
    h_f, s_f, t_f, w_f = [PatternFill(fill_type="solid", start_color=c, end_color=c) for c in ["B7CCE2", "F5E7DB", "B7CCE2", "8CCFF7"]]
    for r_n in [1, 2, 3]:
        for c_n in range(1, total_cols + 1):
            cell = ws.cell(row=r_n, column=c_n); cell.fill, cell.border = (t_f if r_n == 1 else h_f), sbrd if r_n in (1,2,3) else brd
            cell.font = Font(name="Arial", bold=True, size=11 if r_n == 1 else (9 if r_n == 2 else 8))
            if r_n == 2 and (len(l_heads) < c_n < total_cols - 1): cell.alignment = Alignment(horizontal="center", vertical="center", text_rotation=90)
            elif r_n == 3: cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=False)
            else: cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    ws.row_dimensions[1].height, ws.row_dimensions[2].height, ws.row_dimensions[3].height = 24, 66, 22
    cur_r, ser, r_day_t, r_wk_t, r_t_d, r_t_w = 4, 1, defaultdict(lambda: defaultdict(int)), defaultdict(lambda: defaultdict(int)), defaultdict(int), defaultdict(int)
    for group in groups:
        rank, members = group["rank"], group["members"]
        for m in members:
            stats, u_t_d, u_w_d = [], 0, 0
            for d in date_range:
                k = d.strftime("%d-%b"); v = _monthly_normalize_status(m.get(k, ""))
                stats.append(v)
                if v == "W": u_w_d += 1
                elif v: u_t_d += 1
                r_day_t[rank][k] += 1 if v and v != "W" else 0; r_wk_t[rank][k] += 1 if v == "W" else 0
            r_t_d[rank], r_t_w[rank] = r_t_d[rank] + u_t_d, r_t_w[rank] + u_w_d
            row_v = [ser, m.get("employee_code") or "", rank, str(m.get("name") or "").upper()]
            if include_location_column: row_v.append(m.get("location") or "")
            row_v.extend(stats); row_v.extend([u_t_d, u_w_d])
            for c_n, val in enumerate(row_v, start=1):
                cell = ws.cell(row=cur_r, column=c_n, value=val); cell.border, cell.font = brd, Font(name="Arial", size=10)
                if c_n in (1, 2, 3): cell.alignment = Alignment(horizontal="center", vertical="center")
                elif c_n <= len(l_heads): cell.alignment = Alignment(horizontal="left", vertical="center")
                else: cell.alignment = Alignment(horizontal="center", vertical="center")
                if len(l_heads) < c_n <= (len(l_heads) + len(date_range)) and str(val).upper() == "W": cell.fill = w_f
            ws.row_dimensions[cur_r].height, cur_r, ser = 20, cur_r + 1, ser + 1
        sub_v = ["", "", "TOTAL PRESENT", ""]; 
        if include_location_column: sub_v.append("")
        d_ts = [group["date_present_totals"].get(d.strftime("%d-%b"), 0) for d in date_range]
        sub_v.extend(d_ts); sub_v.extend([sum(d_ts), sum(group["date_weekoff_totals"].values())])
        for c_n, val in enumerate(sub_v, start=1):
            cell = ws.cell(row=cur_r, column=c_n, value=val); cell.fill, cell.font, cell.alignment = s_f, Font(name="Arial", bold=True, size=10), Alignment(horizontal="center", vertical="center")
            cell.border = sbrd if c_n <= len(l_heads) + len(d_heads) else brd
        ws.row_dimensions[cur_r].height, cur_r = 20, cur_r + 1
    for r_n in sorted(r_day_t.keys()):
        f_v = ["", "", r_n, ""]; 
        if include_location_column: f_v.append("")
        d_vs = [r_day_t[r_n].get(d.strftime("%d-%b"), 0) for d in date_range]
        f_v.extend(d_vs); f_v.extend([r_t_d[r_n], r_t_w[r_n]])
        for c_n, val in enumerate(f_v, start=1):
            cell = ws.cell(row=cur_r, column=c_n, value=val); cell.font, cell.border, cell.alignment = Font(name="Arial", bold=True, size=10), brd, Alignment(horizontal="center", vertical="center")
        ws.row_dimensions[cur_r].height, cur_r = 20, cur_r + 1
    wk_d_vs = [sum(r_wk_t[r].get(d.strftime("%d-%b"), 0) for r in r_wk_t.keys()) for d in date_range]
    wk_t_v = ["", "", "WEEK OFF", ""]; 
    if include_location_column: wk_t_v.append("")
    wk_t_v.extend(wk_d_vs); wk_t_v.extend([sum(wk_d_vs), sum(r_t_w.values())])
    for c_n, val in enumerate(wk_t_v, start=1):
        cell = ws.cell(row=cur_r, column=c_n, value=val); cell.font, cell.border, cell.alignment = Font(name="Arial", bold=True, size=10), brd, Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[cur_r].height, cur_r = 20, cur_r + 1
    h_c_v = ["", "", "Total Head count", ""]; 
    if include_location_column: h_c_v.append("")
    d_hccs = [sum(1 for r in summary_data if _monthly_normalize_status(r.get(d.strftime("%d-%b"), "")) != "") for d in date_range]
    h_c_v.extend(d_hccs); h_c_v.extend([sum(r_t_d.values()), sum(r_t_w.values())])
    for c_n, val in enumerate(h_c_v, start=1):
        cell = ws.cell(row=cur_r, column=c_n, value=val); cell.font, cell.border, cell.alignment = Font(name="Arial", bold=True, size=10), brd, Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[cur_r].height = 20
    for r in range(4, cur_r + 1):
        for c in range(1, total_cols + 1):
            cell = ws.cell(row=r, column=c); 
            if cell.border != brd: cell.border = brd
            if not cell.alignment: cell.alignment = Alignment(horizontal="center", vertical="center")
    w_ids = [6, 11, 20, 26]
    if include_location_column: w_ids.append(20)
    w_ids.extend([4.2] * len(date_range)); w_ids.extend([8, 8])
    for idx, w in enumerate(w_ids, start=1): ws.column_dimensions[get_column_letter(idx)].width = w
    ws.freeze_panes = ws.cell(row=4, column=len(l_heads) + 1)
    buffer = BytesIO(); wb.save(buffer); buffer.seek(0)
    filename = f"attendance_summary_v2_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)
    with open(file_path, 'wb') as f: f.write(buffer.getvalue())
    return {'file_path': file_path, 'filename': filename, 'start_date': start_date, 'end_date': end_date, 'row_count': len(summary_data)}

class MonthlyAttendanceSummaryViewSetV2(ViewSet):
    @action(detail=False, methods=["get"])
    def summary(self, request):
        p = request.query_params
        try:
            res = _get_monthly_attendance_summary_data_v2(month=p.get("month"), start_date_str=p.get("start_date"), end_date_str=p.get("end_date"), location_id=p.get("location_id"), site_id=p.get("site_id"), user_id=p.get("user_id"), search=p.get("search"), role=p.get("role"), request=request)
            return Response(res['summary_data'])
        except ValueError as e: return Response({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"[MONTHLY_ATTENDANCE_SUMMARY_V2_API] {str(e)}", exc_info=True)
            return Response({"error": str(e)}, status=500)

class MonthlyAttendanceExcelViewSetV2(ViewSet):
    @action(detail=False, methods=["get"])
    def export_excel(self, request):
        p = request.query_params
        try:
            res = generate_monthly_attendance_summary_excel_internal_v2(month=p.get("month"), start_date_str=p.get("start_date"), end_date_str=p.get("end_date"), request=request, location_id=p.get("location_id"), site_id=p.get("site_id"), user_id=p.get("user_id"), search=p.get("search"), role=p.get("role"), include_location_column=request.user.is_superuser)
            with open(res['file_path'], 'rb') as f: content = f.read()
            response = HttpResponse(content, content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
            response["Content-Disposition"] = f'attachment; filename="attendance_summary_v2_{res["start_date"].strftime("%Y%m%d")}.xlsx"'
            return response
        except ValueError as e: return Response({"error": str(e)}, status=400)
        except Exception as e: return Response({"error": str(e)}, status=500)

class MonthlyAttendanceSummaryViewSet(ViewSet):

    @action(detail=False, methods=["get"])
    def summary(self, request):
        # Parse filters
        location_id = request.query_params.get("location_id")
        user_id = request.query_params.get("user_id")
        month = request.query_params.get("month")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        search = request.query_params.get("search")
        role = request.query_params.get("role")

        try:
            # Use internal helper function (pass request for timezone detection)
            result = _get_monthly_attendance_summary_data(
                month=month,
                start_date_str=start_date,
                end_date_str=end_date,
                location_id=location_id,
                user_id=user_id,
                search=search,
                role=role,
                request=request  # Pass request for timezone detection
            )
            
            return Response(result['summary_data'])
            
        except ValueError as e:
            error_msg = f"[MONTHLY_ATTENDANCE_SUMMARY_API] ValueError: {str(e)}"
            logger.error(error_msg)
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            error_msg = f"[MONTHLY_ATTENDANCE_SUMMARY_API] Exception: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return Response({"error": f"Failed to generate summary: {str(e)}"}, status=500)


# class MonthlyAttendanceSummaryViewSet(ViewSet):
#     @action(detail=False, methods=["get"])
#     def summary(self, request):
#         # Filters from query params
#         location_id = request.query_params.get("location_id")
#         user_id = request.query_params.get("user_id")
#         month = request.query_params.get("month")  # format: YYYY-MM
#         start_date = request.query_params.get("start_date")
#         end_date = request.query_params.get("end_date")

#         # Resolve date range
#         if month:
#             year, month_num = map(int, month.split("-"))
#             start_date = datetime(year, month_num, 1).date()
#             end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
#         elif start_date and end_date:
#             start_date = parse_date(start_date)
#             end_date = parse_date(end_date)
#         else:
#             today = now().date()
#             start_date = today.replace(day=1)
#             end_date = today

#         # Get assignments within date range
#         assignments = Assignment.objects.filter(
#             start_date__lte=end_date,
#             end_date__gte=start_date,
#             is_deleted=False
#         )

#         if location_id:
#             assignments = assignments.filter(location_id=location_id)
#         if user_id:
#             assignments = assignments.filter(guard_id=user_id)

#         # Build guard-wise attendance map
# #         summary = []
# #         date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

# #         for assignment in assignments.select_related("guard", "location"):
# #             guard = assignment.guard
# #             location = assignment.location.name if assignment.location else "N/A"

# #             row = {
# #                 #"name": guard.get_full_name() or guard.username,
# #                 "name": guard.name,
# #                 "location": location
# #             }

# #             for date in date_range:
# #                 # Check if assignment is active on this date
# #                 if assignment.start_date <= date <= assignment.end_date:
# #                     checkin = AttendanceCheckin.objects.filter(
# #                         guard=guard,
# #                         assignment=assignment,
# #                         shift=assignment.shift,
# #                         org_location=assignment.location,
# #                         checkin_time__date=date
# #                     ).first()

# # #                   row[date.strftime("%d-%b")] = checkin.status[0].upper() if checkin else "A"
# #                     row[date.strftime("%d-%b")] = "P" if checkin else "A" if assignment.start_date <= date <= assignment.end_date else "-"

# #                 else:
# #                     row[date.strftime("%d-%b")] = "-"

# #             summary.append(row)

#         # Build guard-wise attendance map
#         summary = []
#         #date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
#         date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
#         # Group assignments by guard + location
#         from collections import defaultdict

#         # Group assignments by guard + location
#         grouped = defaultdict(lambda: {
#             "guard": None,
#             "location": None,
#             "assignments": []
#         })

#         guard_map = defaultdict(lambda: {"assignments": [], "location": None})

#         # for assignment in assignments.select_related("guard", "location"):
#         #     key = (assignment.guard.id, assignment.location.id if assignment.location else None)
#         #     guard_map[key]["assignments"].append(assignment)
#         #     guard_map[key]["location"] = assignment.location.name if assignment.location else "N/A"
#         #     guard_map[key]["guard"] = assignment.guard

#         for assignment in assignments.select_related("guard", "location"):
#             key = (assignment.guard.id, assignment.location.id if assignment.location else None)
#             grouped[key]["guard"] = assignment.guard
#             grouped[key]["location"] = assignment.location.name if assignment.location else "N/A"
#             grouped[key]["assignments"].append(assignment)
        
#         summary = []

#         # Build summary rows
#         for (guard_id, location_id), data in guard_map.items():
#             guard = data["guard"]
#             location = data["location"]
#             row = {
#                 "name": guard.name,
#                 "location": location
#             }

#         for date in date_range:
#                 # Get all assignments active on this date
#                 active_assignments = [
#                     a for a in data["assignments"]
#                     if a.start_date <= date <= a.end_date
#                 ]

#                 if not active_assignments:
#                     row[date.strftime("%d-%b")] = "-"
#                     continue

#                 # Check if any assignment has a checkin
#                 has_checkin = AttendanceCheckin.objects.filter(
#                     guard=guard,
#                     assignment__in=active_assignments,
#                     checkin_time__date=date
#                 ).exists()

#                 row[date.strftime("%d-%b")] = "P" if has_checkin else "A"

    
#         summary.append(row)


#         return Response(summary)


class MonthlyAttendanceExcelViewSet(ViewSet):

    @action(detail=False, methods=["get"])
    def export_excel(self, request):
        # Parse filters
        location_id = request.query_params.get("location_id")
        user_id = request.query_params.get("user_id")
        month = request.query_params.get("month")
        
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        search = request.query_params.get("search")
        role = request.query_params.get("role")

        try:
            # Use Excel helper function (consistent with daily reports pattern)
            result = generate_monthly_attendance_summary_excel_internal(
                month=month,
                start_date_str=start_date,
                end_date_str=end_date,
                request=request,  # Pass request for timezone detection
                location_id=location_id,
                user_id=user_id,
                search=search,
                role=role,
                include_location_column=request.user.is_superuser,
            )
            
            file_path = result['file_path']
            start_date_obj = result['start_date']
            end_date_obj = result['end_date']
            
            # Return Excel file as HTTP response
            with open(file_path, 'rb') as f:
                excel_content = f.read()
            
            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            filename = f"attendance_summary_{start_date_obj.strftime('%Y%m%d')}_{end_date_obj.strftime('%Y%m%d')}.xlsx"
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": f"Failed to generate Excel: {str(e)}"}, status=500)

    # def export_excel(self, request):
    #     location_id = request.query_params.get("location_id")
    #     user_id = request.query_params.get("user_id")
    #     month = request.query_params.get("month")
    #     start_date = request.query_params.get("start_date")
    #     end_date = request.query_params.get("end_date")

    #     if month:
    #         year, month_num = map(int, month.split("-"))
    #         start_date = datetime(year, month_num, 1).date()
    #         end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
    #     elif start_date and end_date:
    #         start_date = parse_date(start_date)
    #         end_date = parse_date(end_date)
    #     else:
    #         today = now().date()
    #         start_date = today.replace(day=1)
    #         end_date = today

    #     assignments = Assignment.objects.filter(
    #         start_date__lte=end_date,
    #         end_date__gte=start_date,
    #         is_deleted=False
    #     )

    #     if location_id:
    #         assignments = assignments.filter(location_id=location_id)
    #     if user_id:
    #         assignments = assignments.filter(guard_id=user_id)

    #     date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    #     wb = Workbook()
    #     ws = wb.active
    #     ws.title = "Attendance Summary"

    #     # Header row
    #     headers = ["Name", "Location"] + [date.strftime("%d-%b") for date in date_range]
    #     ws.append(headers)

    #     for assignment in assignments.select_related("guard", "location"):
    #         guard = assignment.guard
    #         location = assignment.location.name if assignment.location else "N/A"

    #         row = [guard.name, location]

    #         for date in date_range:
    #             if assignment.start_date <= date <= assignment.end_date:
    #                 checkin = AttendanceCheckin.objects.filter(
    #                     guard=guard,
    #                     assignment=assignment,
    #                     shift=assignment.shift,
    #                     org_location=assignment.location,
    #                     checkin_time__date=date
    #                 ).first()
    #                 status_code = "P" if checkin else "A"
    #             else:
    #                 status_code = "-"
    #             row.append(status_code)

    #         ws.append(row)

    #     # Prepare response
    #     response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    #     filename = f"attendance_summary_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
    #     response["Content-Disposition"] = f'attachment; filename="{filename}"'
    #     wb.save(response)
    #     return response


# --- Week-off (AttendanceWeekOff) Excel template / upload / grid -----------------

WEEKOFF_COMMIT_CACHE_PREFIX = "weekoff_commit:"
WEEKOFF_COMMIT_CACHE_TTL = 900  # seconds


def _weekoff_location_param(request):
    if request.method.upper() == "GET":
        return request.query_params.get("location_id")
    return request.data.get("location_id")


def _weekoff_resolve_location_id(request):
    """
    Same scope rules as AttendanceCheckinViewSet._resolve_scope_location_id.
    """
    actor = request.user
    requested_location_id = _weekoff_location_param(request)
    actor_role = (getattr(actor, "role", "") or "").strip().lower()

    if getattr(actor, "is_superuser", False):
        scoped_location_id = requested_location_id or getattr(actor, "location_id", None)
        if not scoped_location_id:
            return None, Response(
                {"error": "location_id is required for superuser"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return str(scoped_location_id), None

    if actor_role not in {"admin", "so", "fo"}:
        return None, Response(
            {"error": "Only admin/SO/FO/superuser can access this API"},
            status=status.HTTP_403_FORBIDDEN,
        )

    actor_location_id = getattr(actor, "location_id", None)
    if not actor_location_id:
        return None, Response(
            {"error": "User is not mapped to a location"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if requested_location_id and str(requested_location_id) != str(actor_location_id):
        return None, Response(
            {"error": "You can access only your own location"},
            status=status.HTTP_403_FORBIDDEN,
        )

    return str(actor_location_id), None


def _weekoff_parse_month(month_str):
    if not month_str:
        raise ValueError("month is required (YYYY-MM)")
    try:
        year, month_num = map(int, str(month_str).split("-"))
        if not (1 <= month_num <= 12):
            raise ValueError("Invalid month")
        start_date = date(year, month_num, 1)
        end_date = date(year, month_num, monthrange(year, month_num)[1])
    except (ValueError, AttributeError) as e:
        raise ValueError(f"Invalid month. Use YYYY-MM. ({e})") from e
    return start_date, end_date, year


def _weekoff_eligible_users_qs(location_id, search=None, role=None):
    qs = User.objects.filter(
        is_deleted=False,
        is_active=True,
        location_id=location_id,
    ).exclude(Q(role__iexact="admin") | Q(is_superuser=True))
    if role and str(role).strip().lower() not in ("", "all"):
        qs = qs.filter(role__iexact=str(role).strip().lower())
    if search and str(search).strip():
        s = str(search).strip()
        qs = qs.filter(
            Q(name__icontains=s) | Q(employee_code__icontains=s) | Q(email__icontains=s)
        )
    return qs.order_by("name", "employee_code")


def _weekoff_normalize_cell_mark(value):
    """Return 'W', '', or None if invalid."""
    if value is None:
        return ""
    if isinstance(value, (int, float)) and value == 0:
        return ""
    s = str(value).strip()
    if not s:
        return ""
    if s.upper() == "W":
        return "W"
    return None


def _weekoff_to_proper_case(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    normalized = raw.replace("_", " ")
    return " ".join(part[:1].upper() + part[1:].lower() for part in normalized.split())


def _weekoff_parse_upload_workbook(wb, _location_id, start_date, end_date, year, allowed_user_ids):
    """
    Parse first sheet: header row maps columns. Required: user_id.
    Date columns: headers parseable as %d-%b with request year (e.g. 01-Feb).
    """
    ws = wb.active
    header_row = []
    max_col = ws.max_column or 0
    for c in range(1, max_col + 1):
        cell = ws.cell(row=1, column=c)
        v = cell.value
        header_row.append(str(v).strip() if v is not None else "")

    lower_headers = [h.strip().lower() for h in header_row]
    try:
        user_id_col = lower_headers.index("user_id") + 1  # 1-based
    except ValueError as e:
        raise ValueError("Template must contain a 'user_id' column header in row 1") from e

    reserved = {"user_id", "employee_code", "name", "designation"}
    date_columns = []
    for idx, h in enumerate(header_row):
        hl = h.strip().lower()
        if hl in reserved or not h.strip():
            continue
        try:
            d = datetime.strptime(f"{h.strip()}-{year}", "%d-%b-%Y").date()
        except ValueError:
            continue
        if start_date <= d <= end_date:
            date_columns.append((idx + 1, d))

    if not date_columns:
        raise ValueError(
            "No date columns found (expected headers like 01-Feb matching the selected month)"
        )

    errors = []
    ops_map = {}

    max_row = ws.max_row or 0
    for r in range(2, max_row + 1):
        uid_cell = ws.cell(row=r, column=user_id_col).value
        if uid_cell is None or str(uid_cell).strip() == "":
            continue
        uid_str = str(uid_cell).strip()
        if uid_str not in allowed_user_ids:
            errors.append({"row": r, "message": "Unknown or ineligible user_id for this location"})
            continue

        for col_idx, d in date_columns:
            raw = ws.cell(row=r, column=col_idx).value
            mark = _weekoff_normalize_cell_mark(raw)
            if mark is None:
                errors.append(
                    {
                        "row": r,
                        "message": f"Invalid value in {d.isoformat()} column (only W or empty allowed)",
                    }
                )
                continue
            ops_map[(uid_str, d.isoformat())] = bool(mark == "W")

    return errors, list(ops_map.items())


class AttendanceWeekOffViewSetV2(ViewSet):
    """MODERN V2: Enhanced performance for Weekoff Grid and Excel Template."""
    permission_classes = [IsAuthenticated]
    queryset = AttendanceWeekOff.objects.none()

    @action(detail=False, methods=["get"], url_path="grid")
    def grid(self, request):
        scoped_location_id, error_response = _weekoff_resolve_location_id(request)
        if error_response:
            return error_response

        month = request.query_params.get("month")
        search = request.query_params.get("search")
        role = request.query_params.get("role")
        try:
            start_date, end_date, _year = _weekoff_parse_month(month)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        users_qs = _weekoff_eligible_users_qs(scoped_location_id, search=search, role=role)
        users = list(users_qs.only("id", "name", "employee_code", "role"))
        user_ids = [u.id for u in users]

        wo_qs = AttendanceWeekOff.objects.filter(
            location_id=scoped_location_id,
            weekoff_date__gte=start_date,
            weekoff_date__lte=end_date,
            user_id__in=user_ids,
        ).values_list("user_id", "weekoff_date")
        
        wo_by_user = defaultdict(set)
        for uid, wd in wo_qs:
            wo_by_user[str(uid)].add(wd)

        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        loc = Location.objects.filter(id=scoped_location_id, is_deleted=False).only("name").first()
        loc_name = loc.name if loc else ""

        rows = []
        for u in users:
            uid = str(u.id)
            row = {
                "user_id": uid,
                "name": getattr(u, "name", None) or "",
                "employee_code": getattr(u, "employee_code", None) or "",
                "designation": (getattr(u, "role", None) or "").strip().upper(),
                "location": loc_name,
            }
            dates_set = wo_by_user.get(uid, set())
            for d in date_range:
                key = d.strftime("%d-%b")
                row[key] = "W" if d in dates_set else "-"
            rows.append(row)

        return Response(rows, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="template")
    def template_excel(self, request):
        scoped_location_id, error_response = _weekoff_resolve_location_id(request)
        if error_response:
            return error_response

        month = request.query_params.get("month")
        search = request.query_params.get("search")
        role = request.query_params.get("role")
        try:
            start_date, end_date, _year = _weekoff_parse_month(month)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        loc = Location.objects.filter(id=scoped_location_id, is_deleted=False).only("name").first()
        if not loc:
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

        users_qs = _weekoff_eligible_users_qs(scoped_location_id, search=search, role=role)
        users = list(users_qs.only("id", "name", "employee_code", "role"))
        user_ids = [u.id for u in users]

        existing = AttendanceWeekOff.objects.filter(
            location_id=scoped_location_id,
            weekoff_date__gte=start_date,
            weekoff_date__lte=end_date,
            user_id__in=user_ids,
        ).values_list("user_id", "weekoff_date")
        wo_set = {(str(uid), d) for uid, d in existing}

        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        headers = ["user_id", "employee_code", "name", "designation"] + [
            d.strftime("%d-%b") for d in date_range
        ]

        wb = Workbook()
        ws = wb.active
        ws.title = "Weekoffs"
        ws.append(headers)
        
        grouped_users = defaultdict(list)
        for u in users:
            rank = (getattr(u, "role", None) or "").strip().upper() or "UNASSIGNED"
            grouped_users[rank].append(u)

        for rank in sorted(grouped_users.keys()):
            rank_users = sorted(grouped_users[rank], key=lambda x: (getattr(x, "name", None) or "").upper())
            for u in rank_users:
                row = [
                    str(u.id),
                    getattr(u, "employee_code", None) or "",
                    getattr(u, "name", None) or "",
                    (getattr(u, "role", None) or "").strip().upper(),
                ]
                for d in date_range:
                    row.append("W" if (str(u.id), d) in wo_set else "")
                ws.append(row)

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        filename = f"weekoffs_template_{start_date.strftime('%Y%m')}_{scoped_location_id}.xlsx"
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class AttendanceWeekOffViewSet(ViewSet):
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=["get"], url_path="template")
    def template_excel(self, request):
        scoped_location_id, error_response = _weekoff_resolve_location_id(request)
        if error_response:
            return error_response

        month = request.query_params.get("month")
        search = request.query_params.get("search")
        role = request.query_params.get("role")
        try:
            start_date, end_date, _year = _weekoff_parse_month(month)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        loc = Location.objects.filter(id=scoped_location_id, is_deleted=False).first()
        if not loc:
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

        users = list(_weekoff_eligible_users_qs(scoped_location_id, search=search, role=role))
        user_ids = [u.id for u in users]

        existing = AttendanceWeekOff.objects.filter(
            location_id=scoped_location_id,
            weekoff_date__gte=start_date,
            weekoff_date__lte=end_date,
            user_id__in=user_ids,
        ).values_list("user_id", "weekoff_date")
        wo_set = {(str(uid), d) for uid, d in existing}

        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        headers = ["user_id", "employee_code", "name", "designation"] + [
            d.strftime("%d-%b") for d in date_range
        ]

        wb = Workbook()
        ws = wb.active
        ws.title = "Weekoffs"
        ws.append(headers)
        grouped_users = defaultdict(list)
        for u in users:
            rank = (getattr(u, "role", None) or "").strip().upper() or "UNASSIGNED"
            grouped_users[rank].append(u)

        for rank in sorted(grouped_users.keys()):
            rank_users = sorted(grouped_users[rank], key=lambda x: (getattr(x, "name", None) or "").upper())
            for u in rank_users:
                row = [
                    str(u.id),
                    getattr(u, "employee_code", None) or "",
                    getattr(u, "name", None) or "",
                    _weekoff_to_proper_case(getattr(u, "role", None)),
                ]
                for d in date_range:
                    row.append("W" if (str(u.id), d) in wo_set else "")
                ws.append(row)

        buffer = BytesIO()
        wb.save(buffer)
        buffer.seek(0)
        filename = f"weekoffs_template_{start_date.strftime('%Y%m')}_{scoped_location_id}.xlsx"
        response = HttpResponse(
            buffer.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(
        detail=False,
        methods=["post"],
        url_path="upload-verify",
        parser_classes=[MultiPartParser, FormParser],
    )
    def upload_verify(self, request):
        scoped_location_id, error_response = _weekoff_resolve_location_id(request)
        if error_response:
            return error_response

        month = request.data.get("month")
        try:
            start_date, end_date, year = _weekoff_parse_month(month)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        up_file = request.FILES.get("file")
        if not up_file:
            return Response({"error": "file is required"}, status=status.HTTP_400_BAD_REQUEST)

        users_qs = _weekoff_eligible_users_qs(scoped_location_id)
        allowed_user_ids = {str(u.id) for u in users_qs.only("id")}

        try:
            wb = load_workbook(up_file)
        except Exception as e:
            return Response(
                {"error": f"Could not read Excel file: {e}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            errors, ops_items = _weekoff_parse_upload_workbook(
                wb, scoped_location_id, start_date, end_date, year, allowed_user_ids
            )
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        if errors:
            return Response({"ok": False, "errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        set_count = sum(1 for _k, set_w in ops_items if set_w)
        clear_count = sum(1 for _k, set_w in ops_items if not set_w)
        commit_token = str(uuid.uuid4())
        cache_payload = {
            "actor_id": str(request.user.id),
            "location_id": str(scoped_location_id),
            "month": str(month),
            "ops": [
                {"user_id": uid, "date": d_iso, "set_w": set_w}
                for (uid, d_iso), set_w in ops_items
            ],
        }
        cache.set(
            f"{WEEKOFF_COMMIT_CACHE_PREFIX}{commit_token}",
            cache_payload,
            WEEKOFF_COMMIT_CACHE_TTL,
        )

        ws_active = wb.active
        rows_in_file = max(0, (ws_active.max_row or 0) - 1)

        return Response(
            {
                "ok": True,
                "commit_token": commit_token,
                "expires_in_seconds": WEEKOFF_COMMIT_CACHE_TTL,
                "summary": {
                    "cells_set_to_w": set_count,
                    "cells_cleared": clear_count,
                    "rows_in_file": rows_in_file,
                },
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["post"], url_path="commit")
    def commit(self, request):
        scoped_location_id, error_response = _weekoff_resolve_location_id(request)
        if error_response:
            return error_response

        commit_token = (request.data.get("commit_token") or "").strip()
        if not commit_token:
            return Response({"error": "commit_token is required"}, status=status.HTTP_400_BAD_REQUEST)

        cache_key = f"{WEEKOFF_COMMIT_CACHE_PREFIX}{commit_token}"
        payload = cache.get(cache_key)
        if not payload:
            return Response(
                {"error": "Invalid or expired commit_token. Run upload-verify again."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if str(payload.get("actor_id")) != str(request.user.id):
            return Response(
                {"error": "commit_token was issued for a different user"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if str(payload.get("location_id")) != str(scoped_location_id):
            return Response(
                {"error": "location_id does not match the verified upload"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        ops = payload.get("ops") or []
        batch_id = uuid.uuid4()
        created = 0
        updated = 0
        deleted = 0

        with transaction.atomic():
            for op in ops:
                uid = op.get("user_id")
                d_iso = op.get("date")
                set_w = bool(op.get("set_w"))
                if not uid or not d_iso:
                    continue
                d = parse_date(str(d_iso))
                if not d:
                    continue
                if set_w:
                    _obj, was_created = AttendanceWeekOff.objects.update_or_create(
                        user_id=uid,
                        location_id=scoped_location_id,
                        weekoff_date=d,
                        defaults={
                            "mark": "W",
                            "source": "excel_upload",
                            "upload_batch_id": batch_id,
                            "created_by": request.user,
                        },
                    )
                    if was_created:
                        created += 1
                    else:
                        updated += 1
                else:
                    n, _ = AttendanceWeekOff.objects.filter(
                        user_id=uid,
                        location_id=scoped_location_id,
                        weekoff_date=d,
                    ).delete()
                    deleted += n

        cache.delete(cache_key)

        return Response(
            {
                "ok": True,
                "upload_batch_id": str(batch_id),
                "created": created,
                "updated": updated,
                "deleted": deleted,
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="grid")
    def grid(self, request):
        scoped_location_id, error_response = _weekoff_resolve_location_id(request)
        if error_response:
            return error_response

        month = request.query_params.get("month")
        search = request.query_params.get("search")
        role = request.query_params.get("role")
        try:
            start_date, end_date, _year = _weekoff_parse_month(month)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        users = list(_weekoff_eligible_users_qs(scoped_location_id, search=search, role=role))
        user_ids = [u.id for u in users]

        wo_qs = AttendanceWeekOff.objects.filter(
            location_id=scoped_location_id,
            weekoff_date__gte=start_date,
            weekoff_date__lte=end_date,
            user_id__in=user_ids,
        ).values_list("user_id", "weekoff_date")
        wo_by_user = defaultdict(set)
        for uid, wd in wo_qs:
            wo_by_user[str(uid)].add(wd)

        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        loc = Location.objects.filter(id=scoped_location_id, is_deleted=False).first()
        loc_name = loc.name if loc else ""

        rows = []
        for u in users:
            uid = str(u.id)
            row = {
                "user_id": uid,
                "name": getattr(u, "name", None) or "",
                "employee_code": getattr(u, "employee_code", None) or "",
                "designation": _weekoff_to_proper_case(getattr(u, "role", None)),
                "location": loc_name,
            }
            dates_set = wo_by_user.get(uid, set())
            for d in date_range:
                key = d.strftime("%d-%b")
                row[key] = "W" if d in dates_set else "-"
            rows.append(row)

        return Response(rows, status=status.HTTP_200_OK)


# --- Checkin Report API v2 (Performance Optimized) ---
def _get_checkin_report_data_v2(
    filter_type='today',
    start_date_str=None,
    end_date_str=None,
    user_id=None,
    location_id=None,
    shift_id=None,
    request=None,
    search=None,
    role=None,
    site_id=None,
):
    from django.utils.timezone import now as django_now
    allowed_delay = _get_site_setting_int('time', location_id=location_id, default_value=15)

    
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        today = get_user_today(user_tz)
    else:
        user_tz = pytz.timezone('Asia/Kolkata')
        today = get_user_today(user_tz)
    
    if filter_type == 'today':
        start_dt_user = datetime.combine(today, datetime.min.time())
        end_dt_user = datetime.combine(today, datetime.max.time())
    elif filter_type == 'this_week':
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        start_dt_user = datetime.combine(start_week, datetime.min.time())
        end_dt_user = datetime.combine(end_week, datetime.max.time())
    elif filter_type == 'this_month':
        start_dt_user = datetime(today.year, today.month, 1)
        next_month = start_dt_user.replace(day=28) + timedelta(days=4)
        end_dt_user = datetime(next_month.year, next_month.month, 1) - timedelta(seconds=1)
    elif filter_type == 'custom' and start_date_str and end_date_str:
        try:
            start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            start_dt_user = datetime.combine(start_date_obj, datetime.min.time())
            end_dt_user = datetime.combine(end_date_obj, datetime.max.time())
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
    else:
        raise ValueError("Invalid filter or missing dates")
    
    query_start_date = start_dt_user.date() - timedelta(days=1)
    query_end_date = end_dt_user.date()
    assignments = Assignment.objects.filter(
        start_date__lte=query_end_date,
        end_date__gte=query_start_date
    ).select_related('guard', 'location', 'shift').order_by('guard__name', 'id')
    
    if location_id:
        assignments = assignments.filter(location_id=location_id)
    if shift_id:
        assignments = assignments.filter(shift_id=shift_id)
    if user_id:
        assignments = assignments.filter(guard_id=user_id)
    elif search and str(search).strip():
        s = str(search).strip()
        assignments = assignments.filter(
            Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s)
        )
    if not user_id and role and str(role).strip().lower() not in ('', 'all'):
        assignments = assignments.filter(guard__role__iexact=str(role).strip().lower())
    
    if site_id:
        # Assignments don't have direct 'site' field.
        # Filter assignments that have at least one attendance record associated with this site.
        assignments = assignments.filter(attendance_records__site_id=site_id).distinct()
    
    report = []
    
    date_range = []
    current_date = start_dt_user.date()
    end_date = end_dt_user.date()
    
    if filter_type in ['this_week', 'this_month', 'custom']:
        end_date = min(end_date, today)
    
    while current_date <= end_date:
        date_range.append(current_date)
        current_date += timedelta(days=1)
    
    # --- BULK FETCH OPTIMIZATION FOR v2 ---
    from collections import defaultdict
    overall_start_date = start_dt_user.date() - timedelta(days=1)
    overall_end_date = end_dt_user.date() + timedelta(days=1)
    global_start_utc = combine_date_time_in_user_tz(overall_start_date, datetime.min.time(), user_tz).astimezone(pytz.UTC)
    global_end_utc = combine_date_time_in_user_tz(overall_end_date, datetime.max.time(), user_tz).astimezone(pytz.UTC)
    
    guard_ids = [a.guard_id for a in assignments]
    
    all_checkins_query = CheckIn.objects.filter(
        guard_id__in=guard_ids,
        timestamp__gte=global_start_utc,
        timestamp__lt=global_end_utc
    )
    if shift_id:
        all_checkins_query = all_checkins_query.filter(shift_id=shift_id)
        
    all_checkins = list(all_checkins_query.order_by('timestamp'))
    
    checkin_map = defaultdict(list)
    checkin_ids = []
    for c in all_checkins:
        key = (str(c.guard_id), str(c.shift_id), str(c.checkpoint_id))
        checkin_map[key].append(c)
        if getattr(c, 'has_checklist', False):
            checkin_ids.append(c.id)
            
    answer_map = {}
    if checkin_ids:
        all_answers = CheckInChecklistAnswer.objects.filter(
            checkin_id__in=checkin_ids,
            is_deleted=False
        ).select_related('checklist_template')
        for ans in all_answers:
            if str(ans.checkin_id) not in answer_map:
                answer_map[str(ans.checkin_id)] = ans
                
    # 3.5. Map AttendanceCheckin to Site Name (for the report rows)
    attendance_site_map = {}
    if site_id or True: # Always collect if we want to show it?
        # Only for the guards and dates we care about
        from .models import AttendanceCheckin
        acs = AttendanceCheckin.objects.filter(
            guard_id__in=guard_ids,
            shift_date__gte=start_dt_user.date(),
            shift_date__lte=end_date
        ).select_related('site')
        for ac in acs:
            if ac.site:
                attendance_site_map[(str(ac.guard_id), str(ac.shift_date), str(ac.shift_id))] = ac.site.name
                
    # 4. Fetch all checkpoints in a single query to prevent N+1 lookups
    all_checkpoint_ids = set()
    for assignment in assignments:
        for cp in (assignment.checkpoints or []):
            if cp.get('checkpoint_id'):
                all_checkpoint_ids.add(str(cp.get('checkpoint_id')))
                
    checkpoints_lookup = {}
    if all_checkpoint_ids:
        for cp_obj in Checkpoint.objects.filter(id__in=list(all_checkpoint_ids)):
            checkpoints_lookup[str(cp_obj.id)] = cp_obj.label

    # --------------------------------------

    for assignment in assignments:
        guard = assignment.guard
        shift = assignment.shift
        location = assignment.location
        
        for cp in (assignment.checkpoints or []):
            checkpoint_id = cp.get('checkpoint_id')
            expected_time_str = cp.get('time')
            
            if not checkpoint_id or not expected_time_str:
                continue
            
            try:
                expected_time_obj = datetime.strptime(expected_time_str, "%H:%M").time()
            except ValueError:
                continue
            
            checkpoint_name = checkpoints_lookup.get(str(checkpoint_id))
            if not checkpoint_name:
                checkpoint_name = "Unknown Checkpoint"
                continue
            
            is_overnight = shift.end_time <= shift.start_time
            
            dates_to_check = list(date_range)
            if is_overnight:
                prev_day = start_dt_user.date() - timedelta(days=1)
                if assignment.start_date <= prev_day <= assignment.end_date:
                    if prev_day not in dates_to_check:
                        dates_to_check.insert(0, prev_day)
            
            for check_date in dates_to_check:
                if check_date > today:
                    continue
                
                if not (assignment.start_date <= check_date <= assignment.end_date):
                    continue
                
                if is_overnight:
                    if expected_time_obj >= shift.start_time:
                        checkpoint_date = check_date
                    else:
                        checkpoint_date = check_date + timedelta(days=1)
                else:
                    checkpoint_date = check_date
                
                if not (start_dt_user.date() <= checkpoint_date <= end_date):
                    continue
                
                expected_datetime_user = combine_date_time_in_user_tz(checkpoint_date, expected_time_obj, user_tz)
                
                search_start_utc = (expected_datetime_user - timedelta(minutes=allowed_delay)).astimezone(pytz.UTC)
                search_end_utc = (expected_datetime_user + timedelta(minutes=allowed_delay)).astimezone(pytz.UTC)

                # Match in O(1) from the memory map instead of DB
                checkin = None
                map_key = (str(guard.id), str(shift.id), str(checkpoint_id))
                for c in checkin_map.get(map_key, []):
                    if search_start_utc <= c.timestamp < search_end_utc:
                        checkin = c
                        break
                
                actual_time = None
                delay = None
                
                if expected_datetime_user:
                    expected_time_user = expected_datetime_user.astimezone(user_tz)
                    user_now = get_user_now(user_tz)
                    if user_now > expected_time_user + timedelta(minutes=allowed_delay):
                        status = "Missed"
                    else:
                        status = "Pending"
                else:
                    status = "Missed"
                
                if checkin:
                    if not checkin.synced:
                        status = "Missed"
                        actual_time = None
                        delay = None
                    else:
                        actual_time = checkin.timestamp
                        actual_time_user = to_user_timezone(actual_time, user_tz)
                        expected_time_user = expected_datetime_user.astimezone(user_tz)
                        delay = int((actual_time_user - expected_time_user).total_seconds() / 60)
                        
                        # if delay <= 15:
                        #     status = "On Time"
                        # elif 15 < delay <= 30:
                        #     status = "Delayed"
                        # else:
                        #     status = "Missed"
                        
                        if delay <= allowed_delay:
                            status = "On Time"
                        else:
                            status = "Missed"
                
                has_checklist = bool(checkin.has_checklist) if checkin else False
                checklist_template_name = None
                checklist_remarks = None
                checklist_checked_count = None
                checklist_total_count = None

                checklist_answers = None
                if checkin and has_checklist:
                    answer = answer_map.get(str(checkin.id))

                    if answer:
                        checklist_template_name = answer.checklist_template.name if answer.checklist_template else None
                        checklist_remarks = answer.remarks
                        checklist_answers = answer.answers or []
                        try:
                            checklist_total_count = len(checklist_answers)
                            checklist_checked_count = sum(1 for a in checklist_answers if a.get("checked") is True)
                        except Exception:
                            checklist_total_count = None
                            checklist_checked_count = None

                expected_time_display = expected_datetime_user.astimezone(user_tz) if expected_datetime_user else None
                if actual_time:
                    actual_time_display = to_user_timezone(actual_time, user_tz)
                else:
                    actual_time_display = None
                
                report_date = checkpoint_date
                
                report.append({
                    'date': report_date.strftime('%Y-%m-%d'),
                    'guard_id': str(guard.id),
                    'guard_name': guard.name,
                    'employee_code': getattr(guard, 'employee_code', None) or '',
                    'designation': (getattr(guard, 'role', None) or '').strip(),
                    'location_id': str(location.id) if location else None,
                    'location_name': location.name if location else "",
                    'shift_id': str(shift.id) if shift else None,
                    'shift_name': shift.name if shift else "",
                    'checkpoint_id': str(checkpoint_id),
                    'checkpoint_name': checkpoint_name,
                    'expected_time': expected_time_display,
                    'actual_checkin_time': actual_time_display,
                    'status': status,
                    'delay_minutes': delay,
                    'checkin_id': str(checkin.id) if checkin else None,
                    'has_checklist': has_checklist,
                    'checklist_template_name': checklist_template_name,
                    'checklist_remarks': checklist_remarks,
                    'checklist_checked_count': checklist_checked_count,
                    'checklist_total_count': checklist_total_count,
                    'checklist_answers': checklist_answers,
                })
                
    report.sort(key=lambda r: (
        r['date'],
        str(r['expected_time']) if r['expected_time'] else '',
        r['guard_name'],
        r['checkpoint_name'],
    ))

    return report


class DashboardCheckInReportViewV2(APIView):
    """
    API endpoint that returns check-in report data as JSON.
    V2: Includes bulk fetch performance improvements for massive user counts.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        shift_id = request.query_params.get('shift_id')
        search = (request.query_params.get('search') or '').strip()
        role = (request.query_params.get('role') or '').strip()
        
        try:
            status_filter = (request.query_params.get('status') or 'all').strip()
            report_data = _get_checkin_report_data_v2(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id,
                shift_id=shift_id,
                request=request,
                search=search or None,
                role=role or None,
            )

            # Filter by status if provided
            if status_filter and status_filter != 'all':
                report_data = [r for r in report_data if r.get('status') == status_filter]
            
            for idx, item in enumerate(report_data):
                if item['expected_time']:
                    if hasattr(item['expected_time'], 'isoformat'):
                        item['expected_time'] = item['expected_time'].isoformat()
                    else:
                        item['expected_time'] = item['expected_time'].strftime('%Y-%m-%d %H:%M:%S')
                if item['actual_checkin_time']:
                    if hasattr(item['actual_checkin_time'], 'isoformat'):
                        item['actual_checkin_time'] = item['actual_checkin_time'].isoformat()
                    else:
                        item['actual_checkin_time'] = item['actual_checkin_time'].strftime('%Y-%m-%d %H:%M:%S')
            
            serializer = CheckInReportSerializer(report_data, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"An error occurred while generating the v2 report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DashboardCheckInReportExcelViewV2(APIView):
    """
    API endpoint that generates and returns an Excel file download.
    V2: Uses bulk-fetch optimized _get_checkin_report_data_v2 for fast generation.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        shift_id = request.query_params.get('shift_id')
        search = (request.query_params.get('search') or '').strip()
        role = (request.query_params.get('role') or '').strip()
        status_filter = (request.query_params.get('status') or '').strip()

        try:
            report_data = _get_checkin_report_data_v2(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id,
                shift_id=shift_id,
                request=request,
                search=search or None,
                role=role or None,
            )

            # Filter by status if provided
            if status_filter and status_filter != 'all':
                report_data = [r for r in report_data if r.get('status') == status_filter]

            # Create Excel workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Check-In Report"

            def format_checklist_cell(report_item):
                answers = report_item.get('checklist_answers')
                if isinstance(answers, list) and len(answers) > 0:
                    lines = []
                    for a in answers:
                        label = (a or {}).get('label') or 'Item'
                        checked = (a or {}).get('checked') is True
                        lines.append(f"{label} : {'✓' if checked else '✗'}")
                    return "\n".join(lines)
                return report_item.get('checklist_template_name') or ""

            # Header row
            headers = [
                'Date', 'Name', 'Emp Code', 'Designation', 'Shift Name', 'Checkpoint Name', 'Site',
                'Expected Time', 'Actual Check-In Time', 'Status', 'Delay (minutes)',
                'Has Checklist', 'Checklist', 'Checklist Remarks'
            ]
            ws.append(headers)

            # Add data rows
            for item in report_data:
                ws.append([
                    item['date'],
                    item['guard_name'],
                    item.get('employee_code') or '',
                    item.get('designation') or '',
                    item['shift_name'],
                    item['checkpoint_name'],
                    item.get('site_name') or '',
                    item['expected_time'].strftime("%Y-%m-%d %H:%M") if item['expected_time'] else "",
                    item['actual_checkin_time'].strftime("%Y-%m-%d %H:%M") if item['actual_checkin_time'] else "",
                    item['status'],
                    item['delay_minutes'] if item['delay_minutes'] is not None else "",
                    "Yes" if item.get('has_checklist') else "No",
                    format_checklist_cell(item),
                    item.get('checklist_remarks') or "",
                ])

            # Wrap text for the Checklist column (so items show line-by-line)
            try:
                from openpyxl.styles import Alignment
                for row in ws.iter_rows(min_row=2, max_row=ws.max_row, min_col=12, max_col=12):
                    for cell in row:
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                ws.column_dimensions['L'].width = 55
                ws.column_dimensions['M'].width = 35
            except Exception:
                pass

            # Save to in-memory buffer
            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)

            # Return Excel file as HTTP response
            excel_content = buffer.getvalue()

            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

            # Generate a meaningful filename based on filter
            if filter_type == 'custom' and start_date and end_date:
                filename = f"checkin_report_{start_date}_{end_date}.xlsx"
            else:
                filename = f"checkin_report_{timezone.now().strftime('%Y%m%d')}.xlsx"

            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response

        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"An error occurred while generating the v2 Excel report: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
