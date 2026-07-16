"""Roll call helpers: overnight-safe shift_date and location scoping."""
from datetime import datetime, timedelta

from patrol_backend.utils.timezone_utils import (
    get_user_now,
    get_user_today,
    get_user_timezone_from_request,
)


def compute_shift_date(local_dt, shift):
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


def resolve_location_for_request(request, location_id=None):
    """
    Non-superuser: always use request.user.location.
    Superuser: optional location_id query/body, else user.location.
    Returns (location_id_str_or_None, error_message_or_None).
    """
    user = request.user
    if getattr(user, "is_superuser", False):
        loc = location_id or getattr(user, "location_id", None)
        return (str(loc) if loc else None, None)

    user_loc = getattr(user, "location_id", None)
    if not user_loc:
        return None, "User is not mapped to a location"
    if location_id and str(location_id) != str(user_loc):
        return None, "You can only access roll call for your own location"
    return str(user_loc), None


def get_shift_date_for_now(request, location_id, shift):
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    local_now = get_user_now(user_tz)
    return compute_shift_date(local_now, shift), user_tz


def apply_shift_date_filter(queryset, request, location_id, date_filter, start_date=None, end_date=None):
    """Filter queryset by shift_date using location timezone (today/week/month/custom)."""
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    user_today = get_user_today(user_tz)
    date_filter = (date_filter or "today").lower()

    if date_filter == "today":
        return queryset.filter(shift_date=user_today)
    if date_filter in ("week", "this_week"):
        start_week = user_today - timedelta(days=user_today.weekday())
        end_week = min(start_week + timedelta(days=6), user_today)
        return queryset.filter(shift_date__gte=start_week, shift_date__lte=end_week)
    if date_filter in ("month", "this_month"):
        start_of_month = user_today.replace(day=1)
        if user_today.month == 12:
            end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
        end_of_month = min(end_of_month, user_today)
        return queryset.filter(shift_date__gte=start_of_month, shift_date__lte=end_of_month)
    if date_filter == "custom" and start_date and end_date:
        try:
            start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_obj = min(datetime.strptime(end_date, "%Y-%m-%d").date(), user_today)
            return queryset.filter(shift_date__gte=start_obj, shift_date__lte=end_obj)
        except ValueError:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.")
    return queryset.filter(shift_date=user_today)
