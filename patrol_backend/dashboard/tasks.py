from datetime import timedelta

import pytz
from celery import shared_task
from django.db.models import F, Q
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
        .filter(Q(last_checkout_time__isnull=True) | Q(last_checkin_time__gt=F("last_checkout_time")))
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


@shared_task
def cleanup_old_media_files(days=61):
    """
    Periodic task to clean up media files older than 61 days (2 months) on Sunday.
    Deletes files from:
      - media/attendance_checkinlog/
      - media/attendance_checkins/
      - media/attendance_checkouts/
      - media/payslips/
      - media/logphoto/
      - media/*.pdf and media/*.xlsx in media root
    """
    import os
    from django.conf import settings
    from datetime import datetime, timedelta

    media_dir = settings.MEDIA_ROOT
    if not media_dir or not os.path.isdir(media_dir):
        return "Media directory not found or not configured."

    cutoff_date = datetime.now() - timedelta(days=days)
    
    target_folders = [
        "attendance_checkinlog",
        "attendance_checkins",
        "attendance_checkouts",
        "payslips",
        "logphoto"
    ]
    
    deleted_count = 0
    total_bytes_freed = 0

    # 1. Clear target subfolders
    for folder in target_folders:
        folder_path = os.path.join(media_dir, folder)
        if not os.path.isdir(folder_path):
            continue

        for root, dirs, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                    if mtime < cutoff_date:
                        size = os.path.getsize(file_path)
                        os.remove(file_path)
                        deleted_count += 1
                        total_bytes_freed += size
                except Exception:
                    pass

    # 2. Clear root files (.pdf, .xlsx)
    try:
        for file in os.listdir(media_dir):
            file_path = os.path.join(media_dir, file)
            if os.path.isfile(file_path):
                ext = os.path.splitext(file)[1].lower()
                if ext in ['.pdf', '.xlsx']:
                    try:
                        mtime = datetime.fromtimestamp(os.path.getmtime(file_path))
                        if mtime < cutoff_date:
                            size = os.path.getsize(file_path)
                            os.remove(file_path)
                            deleted_count += 1
                            total_bytes_freed += size
                    except Exception:
                        pass
    except Exception:
        pass

    return f"Cleanup complete. Deleted {deleted_count} files, freed {total_bytes_freed} bytes."


