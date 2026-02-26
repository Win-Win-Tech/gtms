import os
import django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "gtms.settings")
django.setup()

from datetime import datetime, timedelta
import pytz
from django.utils import timezone
from users.models import User
from patrol_backend.dashboard.views import DashboardAttendanceViewSet
from patrol_backend.utils.timezone_utils import to_user_timezone, combine_date_time_in_user_tz

# Mock Request Context
user_id = "d58252b0-dd97-49ed-883c-adf897ee54c5"
user = User.objects.get(id=user_id)
user_tz = pytz.timezone("Asia/Kolkata")

# Time travel to Yesterday at 11:00 PM (Feb 25, 2026 at 23:00)
user_now = datetime(2026, 2, 25, 23, 0, 0, tzinfo=user_tz)
today = user_now.date()
yesterday = today - timedelta(days=1)


print(f"=== SIMULATING TIME: {user_now.strftime('%Y-%m-%d %H:%M:%S %Z')} ===")

# 1. Get Assignment Logic (Bypassing view to inject user_now)
view = DashboardAttendanceViewSet()
assignment = None
shift_start_date = None

from patrol_backend.gtms_models.models import Assignment
assignments = Assignment.objects.filter(
    guard_id=user.id,
    start_date__lte=today,
    end_date__gte=yesterday
).select_related('shift', 'location')

for acc in assignments:
    shift = acc.shift
    is_overnight = shift.end_time <= shift.start_time
    if acc.start_date <= yesterday and is_overnight:
        shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz) + timedelta(minutes=30)
        if user_now <= shift_end_dt:
            assignment = acc
            shift_start_date = yesterday
            break
    if acc.start_date <= today:
        shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz) - timedelta(minutes=30)
        if is_overnight:
            actual_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
            actual_end_dt = combine_date_time_in_user_tz(today + timedelta(days=1), shift.end_time, user_tz)
            window_start = actual_start_dt - timedelta(minutes=30)
            window_end = actual_end_dt + timedelta(minutes=30)
            yesterday_start_dt = combine_date_time_in_user_tz(yesterday, shift.start_time, user_tz)
            yesterday_end_dt = combine_date_time_in_user_tz(yesterday + timedelta(days=1), shift.end_time, user_tz)
            yesterday_window_start = yesterday_start_dt - timedelta(minutes=30)
            yesterday_window_end = yesterday_end_dt + timedelta(minutes=30)
            if yesterday_window_start <= user_now <= yesterday_window_end:
                 assignment = acc
                 shift_start_date = yesterday
                 break
            elif window_start <= user_now <= window_end:
                 assignment = acc
                 shift_start_date = today
                 break
        else:
            shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz) + timedelta(minutes=30)
            if shift_start_dt <= user_now <= shift_end_dt:
                assignment = acc
                shift_start_date = today
                break

if not assignment:
    print("NO ASSIGNMENT ACTIVE AT 11:00 PM")
else:
    print(f"Active Assignment ID: {assignment.id} | Shift: {assignment.shift.name} | Logical Start: {shift_start_date}")

    shift = assignment.shift
    location = assignment.location

    from patrol_backend.utils.timezone_utils import convert_date_range_to_utc
    start_utc, end_utc = convert_date_range_to_utc(shift_start_date, shift_start_date, user_tz)
    search_start_utc = start_utc
    search_end_utc = end_utc + timedelta(days=1)

    from patrol_backend.gtms_models.models import AttendanceCheckin
    attendance = AttendanceCheckin.objects.filter(
        guard=user, shift=shift, assignment=assignment,
        checkin_time__gte=search_start_utc,
        checkin_time__lt=search_end_utc
    ).first()

    if attendance:
        print(f"Attendance Record Found! Checkin Time: {attendance.checkin_time}, Checkout Time: {attendance.checkout_time}")
    else:
        print("NO ATTENDANCE RECORD FOUND for this search window!")

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

    earliest_checkin = shift_start_dt_user - timedelta(minutes=30)
    latest_checkin = shift_end_dt_user
    earliest_checkout = shift_start_dt_user
    latest_checkout = shift_end_dt_user + timedelta(minutes=30)

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
            checkout_time_user = to_user_timezone(attendance.checkout_time, user_tz)
            if user_now <= latest_checkout:
                from patrol_backend.gtms_models.models import CheckInLog
                latest_checkin = CheckInLog.objects.filter(
                    guard=user, assignment=assignment, shift=shift, org_location=location,
                    type="checkin", timestamp__gte=search_start_utc, timestamp__lt=search_end_utc,
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

    import json
    response = {
        "has_shift": True,
        "shift_id": str(shift.id),
        "shift_start": str(shift.start_time),
        "shift_end": str(shift.end_time),
        "location_name": location.name,
        "show_checkin": show_checkin,
        "show_checkout": show_checkout,
        "message": message
    }
    print(f"\nFINAL OUTPUT AT 11:00 PM:")
    print(json.dumps(response, indent=4))
