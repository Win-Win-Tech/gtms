"""Visitor helpers: location scoping, date filters, QR generation."""
import secrets
import uuid
from datetime import datetime, timedelta
from io import BytesIO

from django.core.files.base import ContentFile

from patrol_backend.utils.timezone_utils import (
    convert_date_range_to_utc,
    get_user_timezone_from_request,
    get_user_today,
)


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
        return None, "You can only access visitors for your own location"
    return str(user_loc), None


def _date_range_q(start_utc, end_utc):
    """Match check_in_time when present, else created_on (pending entries)."""
    from django.db.models import Q

    end = end_utc + timedelta(days=1)
    return (
        Q(check_in_time__gte=start_utc, check_in_time__lt=end)
        | Q(check_in_time__isnull=True, created_on__gte=start_utc, created_on__lt=end)
    )


def apply_checkin_date_filter(queryset, request, location_id, date_filter, start_date=None, end_date=None):
    """Filter by check_in_time, or created_on when not yet checked in."""
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    user_today = get_user_today(user_tz)
    date_filter = (date_filter or "today").lower()

    if date_filter in ("all", ""):
        return queryset

    if date_filter == "today":
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
        return queryset.filter(_date_range_q(start_utc, end_utc))

    if date_filter in ("week", "this_week"):
        start_week = user_today - timedelta(days=user_today.weekday())
        end_week = start_week + timedelta(days=6)
        start_utc, end_utc = convert_date_range_to_utc(start_week, end_week, user_tz)
        return queryset.filter(_date_range_q(start_utc, end_utc))

    if date_filter in ("month", "this_month"):
        start_of_month = user_today.replace(day=1)
        if user_today.month == 12:
            end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
        start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
        return queryset.filter(_date_range_q(start_utc, end_utc))

    if date_filter == "custom" and start_date and end_date:
        try:
            start_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            start_utc, end_utc = convert_date_range_to_utc(start_obj, end_obj, user_tz)
            return queryset.filter(_date_range_q(start_utc, end_utc))
        except ValueError as exc:
            raise ValueError("Invalid date format. Use YYYY-MM-DD.") from exc

    start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
    return queryset.filter(_date_range_q(start_utc, end_utc))


def make_qr_token():
    return secrets.token_urlsafe(24)[:48] or str(uuid.uuid4()).replace("-", "")


def generate_qr_image_file(token):
    """Return ContentFile PNG for the given QR token. Requires: pip install qrcode[pil]"""
    try:
        import qrcode
    except ImportError as exc:
        raise ImportError(
            "Package 'qrcode' is required for visitor QR generation. "
            "Install with: pip install 'qrcode[pil]==8.0'"
        ) from exc

    qr = qrcode.QRCode(version=1, box_size=8, border=2)
    qr.add_data(token)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buf = BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return ContentFile(buf.getvalue(), name=f"visitor_qr_{token[:12]}.png")
