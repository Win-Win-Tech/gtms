from datetime import timedelta
from decimal import Decimal
import logging

import pytz
from celery import shared_task
from django.db import transaction
from django.db.models import F, Q
from django.utils import timezone

from scheduler.models import SiteSetting
from patrol_backend.utils.timezone_utils import (
    combine_date_time_in_user_tz,
    get_user_timezone_from_request,
    to_user_timezone,
)

from .models import AttendanceCheckin, CheckInLog

logger = logging.getLogger(__name__)

AUTO_CHECKOUT_REMARK = "[Auto-checkout: shift ended + grace]"


def _get_site_setting_int(key, location_id=None, default_value=None, site_id=None):
    try:
        raw = SiteSetting.get_setting(
            key=key,
            location_id=location_id,
            site_id=site_id,
            default_value=default_value,
        )
        if raw is None:
            return default_value
        return int(raw)
    except Exception:
        return default_value


def auto_checkout_reason(grace_minutes):
    return f"Auto-checkout: no checkout by shift end + {int(grace_minutes)} min grace"


def shift_grace_deadline_utc(shift, shift_start_date, tz, grace_minutes):
    """Return (deadline_utc, checkout_ts). checkout_ts is just inside the exclusive window end."""
    is_overnight = shift.end_time <= shift.start_time
    shift_end_date = shift_start_date + timedelta(days=1) if is_overnight else shift_start_date
    shift_end_utc = combine_date_time_in_user_tz(shift_end_date, shift.end_time, tz)
    deadline_utc = shift_end_utc + timedelta(minutes=grace_minutes)
    checkout_ts = deadline_utc - timedelta(microseconds=1)
    return deadline_utc, checkout_ts


@shared_task
def mark_missed_checkout_v3(batch_size=1000):
    """
    After shift_end + grace, close still-open sessions with an auto-checkout punch
    at the deadline, then compute duration and A/HP/LD/P the same way as v5 APIs.
    present_duration / shift_grace_time lookups are site → org → global.

    Beat schedule is unchanged; task name stays mark_missed_checkout_v3.
    """
    from dashboard.views import (
        _attendance_v3_shift_window_utc,
        _attendance_v3_refresh_saved_fields,
    )
    from patrol_backend.utils.attendance_resolve import (
        build_log_window_filter,
        apply_v4_attendance_after_log,
    )

    now_utc = timezone.now()

    candidates = (
        AttendanceCheckin.objects.select_related(
            "shift", "org_location", "guard", "assignment", "site"
        )
        .filter(pa_status__in=["OW", "M"])
        .filter(last_checkin_time__isnull=False)
        .filter(Q(last_checkout_time__isnull=True) | Q(last_checkin_time__gt=F("last_checkout_time")))
        .order_by("shift_date")
    )

    tz_cache = {}
    grace_cache = {}
    done = 0

    for row in candidates.iterator(chunk_size=batch_size):
        org_loc_id = row.org_location_id
        site_id = row.site_id
        grace_key = (org_loc_id, site_id)

        if org_loc_id not in tz_cache:
            tz_cache[org_loc_id] = get_user_timezone_from_request(None, location_id=org_loc_id)

        if grace_key not in grace_cache:
            grace_cache[grace_key] = _get_site_setting_int(
                key="shift_grace_time",
                location_id=org_loc_id,
                site_id=site_id,
                default_value=30,
            )

        tz = tz_cache[org_loc_id] or pytz.timezone("Asia/Kolkata")
        grace_minutes = grace_cache[grace_key] if grace_cache[grace_key] is not None else 30

        if row.shift_date:
            shift_start_date = row.shift_date
        else:
            base_dt_utc = row.checkin_time or row.last_checkin_time or row.created_on
            local_dt = to_user_timezone(base_dt_utc, tz)
            shift_start_date = local_dt.date()
            if row.shift and row.shift.end_time <= row.shift.start_time and local_dt.time() < row.shift.end_time:
                shift_start_date = shift_start_date - timedelta(days=1)

        shift = row.shift
        if not shift:
            continue

        deadline_utc, checkout_ts = shift_grace_deadline_utc(
            shift, shift_start_date, tz, grace_minutes
        )
        if now_utc < deadline_utc:
            continue

        try:
            with transaction.atomic():
                locked = (
                    AttendanceCheckin.objects.select_for_update()
                    .select_related("shift", "org_location", "guard", "assignment", "site")
                    .filter(id=row.id)
                    .first()
                )
                if not locked:
                    continue
                if locked.pa_status not in ("OW", "M"):
                    continue
                still_open = locked.last_checkin_time and (
                    not locked.last_checkout_time
                    or locked.last_checkin_time > locked.last_checkout_time
                )
                if not still_open:
                    continue

                open_in = locked.last_checkin_time
                if checkout_ts <= open_in:
                    checkout_ts = open_in + timedelta(seconds=1)

                last_in = (
                    CheckInLog.objects.filter(
                        guard=locked.guard,
                        assignment=locked.assignment,
                        shift=locked.shift,
                        org_location=locked.org_location,
                        type="checkin",
                    )
                    .order_by("-timestamp")
                    .first()
                )
                lat = (
                    (last_in.latitude if last_in else None)
                    or locked.latitude
                    or Decimal("0")
                )
                lng = (
                    (last_in.longitude if last_in else None)
                    or locked.longitude
                    or Decimal("0")
                )
                matched_site = locked.site or (last_in.site if last_in else None)
                reason = auto_checkout_reason(grace_minutes)

                log = CheckInLog.objects.create(
                    guard=locked.guard,
                    assignment=locked.assignment,
                    shift=locked.shift,
                    org_location=locked.org_location,
                    type="checkout",
                    latitude=lat,
                    longitude=lng,
                    site=matched_site,
                    is_auto_checkout=True,
                    auto_checkout_reason=reason,
                )
                CheckInLog.objects.filter(id=log.id).update(timestamp=checkout_ts)

                if locked.remarks:
                    if AUTO_CHECKOUT_REMARK not in locked.remarks:
                        locked.remarks = f"{locked.remarks} {AUTO_CHECKOUT_REMARK}"
                else:
                    locked.remarks = AUTO_CHECKOUT_REMARK
                locked.status = "checked_out"
                locked.checkout_time = checkout_ts
                locked.last_checkout_time = checkout_ts

                search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                    shift_start_date,
                    locked.shift,
                    tz,
                    location_id=locked.org_location_id,
                    site_id=locked.site_id,
                )
                if checkout_ts >= search_end_utc:
                    search_end_utc = checkout_ts + timedelta(seconds=1)
                if open_in and open_in < search_start_utc:
                    search_start_utc = open_in - timedelta(minutes=15)

                log_filter = build_log_window_filter(
                    locked.guard,
                    locked.assignment,
                    locked.shift,
                    locked.org_location,
                    search_start_utc,
                    search_end_utc,
                )
                apply_v4_attendance_after_log(
                    attendance=locked,
                    user=locked.guard,
                    assignment=locked.assignment,
                    shift=locked.shift,
                    org_location=locked.org_location,
                    shift_start_date=shift_start_date,
                    matched_site=matched_site,
                    log_filter=log_filter,
                    search_start_utc=search_start_utc,
                    search_end_utc=search_end_utc,
                    action_mode="checkout",
                    raw_bytes=None,
                    img_name="auto_checkout.jpg",
                    user_tz=tz,
                    refresh_fn=_attendance_v3_refresh_saved_fields,
                    skip_sibling_reconcile=True,
                )
                done += 1
                logger.info(
                    "Auto-checkout attendance=%s guard=%s checkout_ts=%s pa_status=%s duration=%s",
                    locked.id,
                    locked.guard_id,
                    checkout_ts,
                    locked.pa_status,
                    locked.duration_minutes,
                )
        except Exception:
            logger.exception(
                "Auto-checkout failed attendance=%s guard=%s",
                getattr(row, "id", None),
                getattr(row, "guard_id", None),
            )

    return f"Auto-checked out {done} open sessions"


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
    from datetime import datetime

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
