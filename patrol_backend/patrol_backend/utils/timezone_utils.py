"""
Timezone utility functions for user timezone handling.

All datetimes are stored in UTC in the database.
These utilities convert between UTC (storage) and user timezone (display/input).
"""
import pytz
from django.utils import timezone as django_timezone
from datetime import datetime, date, timedelta
from django.utils.timezone import make_aware, is_naive


def get_user_timezone(user):
    """
    Get user's timezone or return default.
    
    Args:
        user: User object (can be None)
    
    Returns:
        pytz timezone object (default: Asia/Kolkata)
    """
    if user and hasattr(user, 'timezone') and user.timezone:
        try:
            return pytz.timezone(user.timezone)
        except (pytz.exceptions.UnknownTimeZoneError, AttributeError):
            pass
    
    # Default fallback to Asia/Kolkata (maintains backward compatibility)
    return pytz.timezone('Asia/Kolkata')


def get_user_timezone_from_request(request):
    """
    Get timezone from authenticated user model (from JWT token).
    
    Priority:
    1. User model timezone field (from authenticated user)
    2. Default (Asia/Kolkata)
    
    Args:
        request: Django request object
    
    Returns:
        pytz timezone object
    """
    # Get timezone from authenticated user (from JWT token)
    if hasattr(request, 'user') and request.user and request.user.is_authenticated:
        return get_user_timezone(request.user)
    
    # Default
    return pytz.timezone('Asia/Kolkata')


def to_user_timezone(dt, user_or_tz):
    """
    Convert UTC datetime to user's timezone.
    
    Args:
        dt: datetime object (UTC, can be naive or aware)
        user_or_tz: User object or pytz timezone object
    
    Returns:
        datetime object in user's timezone (timezone-aware)
    """
    if dt is None:
        return None
    
    # Get timezone
    if isinstance(user_or_tz, pytz.BaseTzInfo):
        user_tz = user_or_tz
    else:
        user_tz = get_user_timezone(user_or_tz)
    
    # If datetime is naive, assume it's in UTC (for backward compatibility)
    if is_naive(dt):
        dt = make_aware(dt, pytz.UTC)
    
    # Convert to user timezone
    return dt.astimezone(user_tz)


def from_user_timezone(dt_str_or_dt, user_or_tz, format='%Y-%m-%d %H:%M:%S'):
    """
    Convert user's local datetime string/datetime to UTC datetime.
    
    Args:
        dt_str_or_dt: datetime string or datetime object in user timezone
        user_or_tz: User object or pytz timezone object
        format: datetime string format (if dt_str_or_dt is string)
    
    Returns:
        datetime object in UTC (timezone-aware)
    """
    if not dt_str_or_dt:
        return None
    
    # Get timezone
    if isinstance(user_or_tz, pytz.BaseTzInfo):
        user_tz = user_or_tz
    else:
        user_tz = get_user_timezone(user_or_tz)
    
    # Parse if string
    if isinstance(dt_str_or_dt, str):
        dt = datetime.strptime(dt_str_or_dt, format)
    else:
        dt = dt_str_or_dt
    
    # Make it timezone-aware in user's timezone
    if is_naive(dt):
        dt = user_tz.localize(dt)
    
    # Convert to UTC
    return dt.astimezone(pytz.UTC)


def get_user_now(user_or_tz):
    """
    Get current time in user's timezone.
    
    Args:
        user_or_tz: User object or pytz timezone object
    
    Returns:
        datetime object in user's timezone (timezone-aware)
    """
    utc_now = django_timezone.now()
    return to_user_timezone(utc_now, user_or_tz)


def get_user_today(user_or_tz):
    """
    Get today's date in user's timezone.
    
    Args:
        user_or_tz: User object or pytz timezone object
    
    Returns:
        date object
    """
    user_now = get_user_now(user_or_tz)
    return user_now.date()


def convert_date_range_to_utc(start_date, end_date, user_or_tz):
    """
    Convert date range from user timezone to UTC for database queries.
    
    This ensures date filters work correctly across timezones.
    
    Args:
        start_date: date or datetime in user timezone
        end_date: date or datetime in user timezone
        user_or_tz: User object or pytz timezone object
    
    Returns:
        tuple: (start_datetime_utc, end_datetime_utc) - both timezone-aware UTC
    """
    # Get timezone
    if isinstance(user_or_tz, pytz.BaseTzInfo):
        user_tz = user_or_tz
    else:
        user_tz = get_user_timezone(user_or_tz)
    
    # Convert start_date to datetime at start of day in user timezone
    if isinstance(start_date, date) and not isinstance(start_date, datetime):
        start_dt = datetime.combine(start_date, datetime.min.time())
    else:
        start_dt = start_date if isinstance(start_date, datetime) else datetime.combine(start_date, datetime.min.time())
    
    # Make timezone-aware in user timezone
    if is_naive(start_dt):
        start_dt = user_tz.localize(start_dt)
    
    # Convert end_date to datetime at end of day in user timezone
    if isinstance(end_date, date) and not isinstance(end_date, datetime):
        end_dt = datetime.combine(end_date, datetime.max.time())
    else:
        end_dt = end_date if isinstance(end_date, datetime) else datetime.combine(end_date, datetime.max.time())
    
    # Make timezone-aware in user timezone
    if is_naive(end_dt):
        end_dt = user_tz.localize(end_dt)
    
    # Convert both to UTC
    start_utc = start_dt.astimezone(pytz.UTC)
    end_utc = end_dt.astimezone(pytz.UTC)
    
    return start_utc, end_utc


def combine_date_time_in_user_tz(date_obj, time_obj, user_or_tz):
    """
    Combine date and time objects in user's timezone, then return as UTC.
    
    Useful for creating datetime from shift times, checkpoint times, etc.
    
    Args:
        date_obj: date object
        time_obj: time object
        user_or_tz: User object or pytz timezone object
    
    Returns:
        datetime object in UTC (timezone-aware)
    """
    # Get timezone
    if isinstance(user_or_tz, pytz.BaseTzInfo):
        user_tz = user_or_tz
    else:
        user_tz = get_user_timezone(user_or_tz)
    
    # Combine date and time
    dt = datetime.combine(date_obj, time_obj)
    
    # Make timezone-aware in user timezone
    dt = user_tz.localize(dt)
    
    # Convert to UTC
    return dt.astimezone(pytz.UTC)

