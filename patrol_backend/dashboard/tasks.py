from datetime import timedelta

import pytz
from celery import shared_task
from django.db.models import Q
from django.utils import timezone

from scheduler.models import SiteSetting
from patrol_backend.utils.timezone_utils import (
    combine_date_time_in_user_tz,
    get_user_timezone_from_request,
    to_user_timezone,
)

from .models import AttendanceCheckin


def _get_site_setting_int(key, location_id=None, default_value=None):
    try:
        raw = SiteSetting.get_setting(key=key, location_id=location_id, default_value=default_value)
        if raw is None:
            return default_value
        return int(raw)
    except Exception:
        return default_value


@shared_task
def mark_missed_checkout_v3(batch_size=1000):
    """
    Convert pa_status:
      OW -> M
    when the shift window is finished (shift_end + grace) but the session is still open.

    Overnight-safe because we use:
    - AttendanceCheckin.shift_date (logical shift start day)
    - shift.start_time/shift.end_time to compute shift end
    """
    now_utc = timezone.now()

    candidates = (
        AttendanceCheckin.objects.select_related("shift", "org_location")
        .filter(pa_status="OW")
        .filter(last_checkin_time__isnull=False)
        .filter(last_checkout_time__isnull=True)
        .filter(checkout_time__isnull=True)
        .order_by("shift_date")
    )

    tz_cache = {}
    grace_cache = {}

    missed_ids = []
    for row in candidates.iterator(chunk_size=batch_size):
        org_loc_id = row.org_location_id

        if org_loc_id not in tz_cache:
            # For Celery/non-request: timezone is derived from location admin/user.
            tz_cache[org_loc_id] = get_user_timezone_from_request(None, location_id=org_loc_id)
            grace_cache[org_loc_id] = _get_site_setting_int(
                key="shift_grace_time",
                location_id=org_loc_id,
                default_value=30,
            )

        tz = tz_cache[org_loc_id] or pytz.timezone("Asia/Kolkata")
        grace_minutes = grace_cache[org_loc_id] if grace_cache[org_loc_id] is not None else 30

        if row.shift_date:
            shift_start_date = row.shift_date
        else:
            # Fallback: derive shift_date from checkin_time in the location/business timezone.
            base_dt_utc = row.checkin_time or row.last_checkin_time or row.created_on
            local_dt = to_user_timezone(base_dt_utc, tz)
            shift_start_date = local_dt.date()
            if row.shift and row.shift.end_time <= row.shift.start_time and local_dt.time() < row.shift.end_time:
                shift_start_date = shift_start_date - timedelta(days=1)

        shift = row.shift
        if not shift:
            continue

        is_overnight = shift.end_time <= shift.start_time
        shift_end_date = shift_start_date + timedelta(days=1) if is_overnight else shift_start_date

        shift_end_utc = combine_date_time_in_user_tz(shift_end_date, shift.end_time, tz)
        deadline_utc = shift_end_utc + timedelta(minutes=grace_minutes)

        if now_utc >= deadline_utc:
            missed_ids.append(row.id)

    if missed_ids:
        AttendanceCheckin.objects.filter(id__in=missed_ids).update(
            pa_status="M",
            modified_on=now_utc,
        )

