"""
Shared attendance row logic for checkin_v4 / checkout_v4 / kiosk face_attendance.

Rules (same as mobile v4):
- Resolve assignment + shift_start_date via get_today_assignment_v2 (handles overnight:
  shift_start_date may be yesterday while calendar today is the next day).
- One AttendanceCheckin row per (guard, assignment, shift, org_location, shift_date).
- If no row for that shift_date → create it.
- checkin_time / checkout_time always derived from CheckInLog inside that shift window.
- After each punch, re-sync other shift_date rows for the same guard/shift so old corrupted
  rows (e.g. last_checkin_time updated by a previous bug) are corrected from their own logs.
"""
from __future__ import annotations

import logging
from typing import Any, Callable, Dict, Optional

from django.core.files.base import ContentFile
from django.utils import timezone

logger = logging.getLogger(__name__)


def get_or_create_attendance_for_shift_day(
    user,
    assignment,
    shift,
    org_location,
    shift_start_date,
):
    """One row per logical shift day — keyed by shift_date (matches checkin_v3/v4)."""
    from dashboard.models import AttendanceCheckin

    attendance, _ = AttendanceCheckin.objects.get_or_create(
        guard=user,
        assignment=assignment,
        shift=shift,
        org_location=org_location,
        shift_date=shift_start_date,
        defaults={"created_on": timezone.now()},
    )
    return attendance


def build_log_window_filter(
    user,
    assignment,
    shift,
    org_location,
    search_start_utc,
    search_end_utc,
) -> Dict[str, Any]:
    return {
        "guard": user,
        "assignment": assignment,
        "shift": shift,
        "org_location": org_location,
        "timestamp__gte": search_start_utc,
        "timestamp__lt": search_end_utc,
    }


def sync_attendance_times_from_logs(attendance, log_filter) -> None:
    """
    Set checkin_time / checkout_time / lat / lon from logs in the shift window.
    Same as checkin_v4 + checkout_v4 post-log updates.
    """
    from dashboard.models import CheckInLog

    earliest_checkin = (
        CheckInLog.objects.filter(**log_filter, type="checkin")
        .order_by("timestamp")
        .first()
    )
    if earliest_checkin:
        attendance.checkin_time = earliest_checkin.timestamp
        attendance.latitude = earliest_checkin.latitude
        attendance.longitude = earliest_checkin.longitude
    else:
        attendance.checkin_time = None
        attendance.latitude = None
        attendance.longitude = None

    latest_checkout = (
        CheckInLog.objects.filter(**log_filter, type="checkout")
        .order_by("-timestamp")
        .first()
    )
    attendance.checkout_time = latest_checkout.timestamp if latest_checkout else None


def refresh_attendance_metrics(
    attendance,
    user,
    assignment,
    shift,
    org_location,
    search_start_utc,
    search_end_utc,
    refresh_fn: Callable,
) -> None:
    """Recompute counts, duration, pa_status, last_* from logs (v4 refresh)."""
    refresh_fn(
        attendance,
        user,
        assignment,
        shift,
        org_location,
        search_start_utc,
        search_end_utc,
    )


def reconcile_sibling_attendance_rows(
    user,
    assignment,
    shift,
    org_location,
    active_shift_date,
    user_tz,
    refresh_fn: Callable,
) -> None:
    """
    Re-sync attendance rows for OTHER shift_date values from their own log windows.
    Fixes rows polluted when an old bug wrote today's last_checkin_time onto a past row.
    """
    from dashboard.models import AttendanceCheckin
    from dashboard.views import _attendance_v3_shift_window_utc

    location_id = getattr(org_location, "id", None)
    siblings = AttendanceCheckin.objects.filter(
        guard=user,
        assignment=assignment,
        shift=shift,
        org_location=org_location,
    ).exclude(shift_date=active_shift_date)

    for att in siblings:
        row_shift_date = att.shift_date
        if not row_shift_date:
            continue
        try:
            start_utc, end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                row_shift_date,
                shift,
                user_tz,
                location_id=location_id,
            )
            log_filter = build_log_window_filter(
                user, assignment, shift, org_location, start_utc, end_utc
            )
            sync_attendance_times_from_logs(att, log_filter)
            att.save(
                update_fields=[
                    "checkin_time",
                    "checkout_time",
                    "latitude",
                    "longitude",
                    "modified_on",
                ]
            )
            refresh_attendance_metrics(
                att, user, assignment, shift, org_location, start_utc, end_utc, refresh_fn
            )
        except Exception as exc:
            logger.warning(
                "Sibling attendance reconcile failed att=%s shift_date=%s: %s",
                att.pk,
                row_shift_date,
                exc,
            )


def apply_v4_attendance_after_log(
    *,
    attendance,
    user,
    assignment,
    shift,
    org_location,
    shift_start_date,
    matched_site,
    log_filter,
    search_start_utc,
    search_end_utc,
    action_mode: str,
    raw_bytes: Optional[bytes],
    img_name: str,
    user_tz,
    refresh_fn: Optional[Callable] = None,
) -> None:
    """
    Update attendance row after CheckInLog was created — shared v4 field rules.
    action_mode: 'checkin' or 'checkout'
    """
    attendance.shift_date = shift_start_date
    attendance.site = matched_site
    sync_attendance_times_from_logs(attendance, log_filter)

    if action_mode == "checkin" and raw_bytes is not None:
        attendance.checkin_image.save(img_name, ContentFile(raw_bytes), save=False)
    elif action_mode == "checkout" and raw_bytes is not None:
        attendance.checkout_image.save(img_name, ContentFile(raw_bytes), save=False)

    attendance.save()

    if refresh_fn:
        refresh_attendance_metrics(
            attendance,
            user,
            assignment,
            shift,
            org_location,
            search_start_utc,
            search_end_utc,
            refresh_fn,
        )
        attendance.refresh_from_db()
        reconcile_sibling_attendance_rows(
            user,
            assignment,
            shift,
            org_location,
            shift_start_date,
            user_tz,
            refresh_fn,
        )
