"""
One-time / ops backfill: rebuild AttendanceCheckin rows from CheckInLog.

NOT called from checkin_v4 / checkout_v4 — run via management command only.
Creates one attendance row per (guard, assignment, shift, org_location, shift_date)
and fills times, duration, counts, images, site, pa_status from that day's log window.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, TextIO

import pytz
from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)

GOLDEN_WIN_LOCATION_ID = "42611273-5ae9-4187-958f-1fdfbf070073"


def default_from_date_last_month_30(today: Optional[date] = None) -> date:
    """Day 30 of the previous calendar month (or last day if month has fewer than 30 days)."""
    today = today or timezone.now().date()
    first_of_this = today.replace(day=1)
    last_of_prev = first_of_this - timedelta(days=1)
    day = min(30, last_of_prev.day)
    return last_of_prev.replace(day=day)


def _copy_log_image_to_attendance(log_image, attendance, dest_attr: str, name_prefix: str) -> bool:
    """Copy CheckInLog.image onto attendance checkin_image / checkout_image. Returns True if set."""
    if not log_image or not getattr(log_image, "name", None):
        return False
    try:
        log_image.open("rb")
        try:
            data = log_image.read()
        finally:
            log_image.close()
        if not data:
            return False
        fname = os.path.basename(log_image.name) or f"{name_prefix}.jpg"
        getattr(attendance, dest_attr).save(fname, ContentFile(data), save=False)
        return True
    except Exception as exc:
        logger.warning(
            "Failed copying log image to attendance.%s (log=%s): %s",
            dest_attr,
            getattr(log_image, "name", None),
            exc,
        )
        return False


def _apply_images_from_window_logs(attendance, logs) -> None:
    """
    Earliest check-in log with image → checkin_image.
    Latest checkout log with image → checkout_image.
    Clear checkout_image when there is no checkout in the window.
    """
    checkin_with_img = next(
        (log for log in logs if log.type == "checkin" and getattr(log.image, "name", None)),
        None,
    )
    checkout_with_img = None
    for log in reversed(logs):
        if log.type == "checkout" and getattr(log.image, "name", None):
            checkout_with_img = log
            break

    if checkin_with_img:
        _copy_log_image_to_attendance(
            checkin_with_img.image, attendance, "checkin_image", "checkin"
        )

    has_checkout = any(log.type == "checkout" for log in logs)
    if checkout_with_img:
        _copy_log_image_to_attendance(
            checkout_with_img.image, attendance, "checkout_image", "checkout"
        )
    elif not has_checkout and attendance.checkout_image:
        attendance.checkout_image = None


def backfill_attendance_from_checkin_logs(
    *,
    location_id: str = GOLDEN_WIN_LOCATION_ID,
    from_date: Optional[date] = None,
    to_date: Optional[date] = None,
    dry_run: bool = False,
    stdout: Optional[TextIO] = None,
) -> Dict[str, Any]:
    """
    Rebuild AttendanceCheckin for a location from CheckInLog between from_date and to_date
    (inclusive, in location TZ). One row per logical shift_date.

    Does not modify checkin_v4 / checkout_v4 — call from management command only.
    """
    from dashboard.models import AttendanceCheckin, CheckInLog
    from dashboard.views import (
        _attendance_v3_compute_shift_date,
        _attendance_v3_refresh_saved_fields,
        _attendance_v3_shift_window_utc,
    )
    from patrol_backend.utils.attendance_resolve import (
        build_log_window_filter,
        get_or_create_attendance_for_shift_day,
        sync_attendance_times_from_logs,
    )
    from patrol_backend.utils.timezone_utils import (
        get_user_timezone_from_request,
        to_user_timezone,
    )
    from scheduler.models import Location

    def _out(msg: str) -> None:
        if stdout is not None:
            stdout.write(msg)
        else:
            logger.info(msg)

    org_location = Location.objects.filter(id=location_id).first()
    if not org_location:
        raise ValueError(f"Location not found: {location_id}")

    user_tz = get_user_timezone_from_request(None, location_id=location_id) or pytz.timezone(
        "Asia/Kuala_Lumpur"
    )
    today_local = to_user_timezone(timezone.now(), user_tz).date()
    from_date = from_date or default_from_date_last_month_30(today_local)
    to_date = to_date or today_local
    if to_date < from_date:
        raise ValueError(f"to_date {to_date} is before from_date {from_date}")

    # Pad UTC range so overnight / grace punches near boundaries are included.
    pad_start = user_tz.localize(
        datetime.combine(from_date - timedelta(days=2), datetime.min.time())
    )
    pad_end = user_tz.localize(
        datetime.combine(to_date + timedelta(days=2), datetime.max.time().replace(microsecond=0))
    )
    range_start_utc = pad_start.astimezone(pytz.UTC)
    range_end_utc = pad_end.astimezone(pytz.UTC)

    _out(
        f"[backfill] location={org_location.name} ({location_id}) "
        f"from={from_date} to={to_date} dry_run={dry_run} tz={user_tz}"
    )

    combos = (
        CheckInLog.objects.filter(
            org_location_id=location_id,
            timestamp__gte=range_start_utc,
            timestamp__lt=range_end_utc,
            assignment_id__isnull=False,
            shift_id__isnull=False,
            guard_id__isnull=False,
        )
        .values("guard_id", "assignment_id", "shift_id")
        .distinct()
    )
    combo_list = list(combos)
    _out(f"[backfill] distinct guard/assignment/shift combos={len(combo_list)}")

    created = 0
    updated = 0
    skipped = 0
    errors: List[str] = []

    # Cache shift/assignment/user objects
    from django.contrib.auth import get_user_model
    from scheduler.models import Assignment, Shift

    User = get_user_model()

    for combo in combo_list:
        guard = User.objects.filter(id=combo["guard_id"]).first()
        assignment = (
            Assignment.objects.select_related("shift", "location")
            .filter(id=combo["assignment_id"])
            .first()
        )
        shift = Shift.objects.filter(id=combo["shift_id"]).first()
        if not guard or not assignment or not shift:
            skipped += 1
            continue

        day = from_date
        while day <= to_date:
            try:
                search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                    day, shift, user_tz, location_id=location_id
                )
                log_filter = build_log_window_filter(
                    guard,
                    assignment,
                    shift,
                    org_location,
                    search_start_utc,
                    search_end_utc,
                )
                logs = list(
                    CheckInLog.objects.filter(**log_filter)
                    .select_related("site")
                    .order_by("timestamp")
                )
                if not logs:
                    day += timedelta(days=1)
                    continue

                # Sanity: logs should map to this shift_date
                sample_local = to_user_timezone(logs[0].timestamp, user_tz)
                derived = _attendance_v3_compute_shift_date(sample_local, shift)
                if derived and derived != day:
                    # Overnight edge: skip if this window's punches belong to another day
                    # (rare when iterating by day with correct window). Still process if
                    # any log's derived date matches `day`.
                    if not any(
                        _attendance_v3_compute_shift_date(
                            to_user_timezone(lg.timestamp, user_tz), shift
                        )
                        == day
                        for lg in logs
                    ):
                        day += timedelta(days=1)
                        continue

                existing = AttendanceCheckin.objects.filter(
                    guard=guard,
                    assignment=assignment,
                    shift=shift,
                    org_location=org_location,
                    shift_date=day,
                ).first()
                is_new = existing is None

                if dry_run:
                    _out(
                        f"  [dry-run] {'CREATE' if is_new else 'UPDATE'} "
                        f"guard={guard.id} shift_date={day} logs={len(logs)} "
                        f"ci={sum(1 for l in logs if l.type=='checkin')} "
                        f"co={sum(1 for l in logs if l.type=='checkout')}"
                    )
                    if is_new:
                        created += 1
                    else:
                        updated += 1
                    day += timedelta(days=1)
                    continue

                attendance = get_or_create_attendance_for_shift_day(
                    guard, assignment, shift, org_location, day
                )
                attendance.shift_date = day
                sync_attendance_times_from_logs(attendance, log_filter)
                _apply_images_from_window_logs(attendance, logs)

                # Site from latest log that has one
                for log in reversed(logs):
                    if log.site_id:
                        attendance.site_id = log.site_id
                        break

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

                if is_new:
                    created += 1
                else:
                    updated += 1

                _out(
                    f"  {'CREATED' if is_new else 'UPDATED'} att={attendance.id} "
                    f"guard={getattr(guard, 'username', guard.id)} shift_date={day} "
                    f"ci={attendance.checkin_count} co={attendance.checkout_count} "
                    f"dur={attendance.duration_minutes} pa={attendance.pa_status}"
                )
            except Exception as exc:
                msg = (
                    f"Error guard={combo['guard_id']} assignment={combo['assignment_id']} "
                    f"day={day}: {exc}"
                )
                logger.exception(msg)
                errors.append(msg)
                _out(f"  ERROR {msg}")

            day += timedelta(days=1)

    summary = {
        "location_id": str(location_id),
        "from_date": str(from_date),
        "to_date": str(to_date),
        "dry_run": dry_run,
        "combos": len(combo_list),
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "errors": errors,
    }
    _out(
        f"[backfill] done created={created} updated={updated} "
        f"skipped={skipped} errors={len(errors)}"
    )
    return summary
