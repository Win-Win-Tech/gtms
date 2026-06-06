"""
Fast-path helpers for kiosk face_attendance (minimize DB round-trips and disk I/O).
Business rules live in attendance_resolve.py (same as checkin_v4 / checkout_v4).
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from patrol_backend.utils.attendance_resolve import (
    apply_v4_attendance_after_log,
    build_log_window_filter,
    get_or_create_attendance_for_shift_day,
)

logger = logging.getLogger(__name__)


def get_kiosk_session_state(
    user,
    assignment,
    shift,
    org_location,
    search_start_utc,
    search_end_utc,
) -> Tuple[Optional[Any], Optional[Any], bool]:
    """One query: latest checkin/checkout timestamps in shift window."""
    from dashboard.models import CheckInLog

    latest_checkin = None
    latest_checkout = None
    for row in (
        CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc,
            type__in=("checkin", "checkout"),
        )
        .order_by("-timestamp")
        .values("type", "timestamp")[:24]
    ):
        if row["type"] == "checkin" and latest_checkin is None:
            latest_checkin = row["timestamp"]
        elif row["type"] == "checkout" and latest_checkout is None:
            latest_checkout = row["timestamp"]
        if latest_checkin is not None and latest_checkout is not None:
            break
    has_open = bool(
        latest_checkin and (not latest_checkout or latest_checkin > latest_checkout)
    )
    return latest_checkin, latest_checkout, has_open


def kiosk_apply_punch(
    *,
    user,
    assignment,
    shift,
    org_location,
    shift_start_date,
    matched_site,
    lat,
    lon,
    raw_bytes: bytes,
    img_name: str,
    search_start_utc,
    search_end_utc,
    user_tz,
    latest_checkin,
    latest_checkout,
    has_open_session: bool,
    min_checkout_minutes: int = 5,
    refresh_fn: Optional[Callable] = None,
) -> Tuple[str, Any, Any, int]:
    """
    Create CheckInLog + update AttendanceCheckin using shared v4 rules.
    Returns (action_mode, attendance, log, http_status).
    """
    from dashboard.models import CheckInLog

    action_mode = "checkout" if has_open_session else "checkin"
    if action_mode == "checkout" and latest_checkin:
        elapsed = int((timezone.now() - latest_checkin).total_seconds())
        if elapsed < min_checkout_minutes * 60:
            raise CheckoutTooEarly(
                remaining=max(0, (min_checkout_minutes * 60) - elapsed),
                min_checkout_minutes=min_checkout_minutes,
            )

    log_filter = build_log_window_filter(
        user, assignment, shift, org_location, search_start_utc, search_end_utc
    )

    with transaction.atomic():
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

        attendance = get_or_create_attendance_for_shift_day(
            user, assignment, shift, org_location, shift_start_date
        )

        apply_v4_attendance_after_log(
            attendance=attendance,
            user=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            shift_start_date=shift_start_date,
            matched_site=matched_site,
            log_filter=log_filter,
            search_start_utc=search_start_utc,
            search_end_utc=search_end_utc,
            action_mode=action_mode,
            raw_bytes=raw_bytes,
            img_name=img_name,
            user_tz=user_tz,
            refresh_fn=refresh_fn,
        )
        http_status = 200 if action_mode == "checkout" else 201

    return action_mode, attendance, log, http_status


class CheckoutTooEarly(Exception):
    def __init__(self, remaining: int, min_checkout_minutes: int):
        self.remaining = remaining
        self.min_checkout_minutes = min_checkout_minutes


def build_kiosk_light_payload(
    attendance,
    *,
    action_mode: str,
    user_id: str,
    request=None,
) -> Dict[str, Any]:
    """Minimal response body (no DRF serializer)."""
    data = {
        "id": str(attendance.id),
        "shift_date": attendance.shift_date.isoformat() if attendance.shift_date else None,
        "checkin_time": attendance.checkin_time.isoformat() if attendance.checkin_time else None,
        "checkout_time": attendance.checkout_time.isoformat() if attendance.checkout_time else None,
        "status": getattr(attendance, "status", None) or "present",
        "checkin_count": int(attendance.checkin_count or 0),
        "checkout_count": int(attendance.checkout_count or 0),
        "duration_minutes": attendance.duration_minutes,
        "pa_status": getattr(attendance, "pa_status", None),
        "kiosk_mode": True,
        "mode": action_mode,
        "face_attendance": True,
        "face_verified": True,
        "has_shift": True,
        "user_id": user_id,
    }
    if request and attendance.checkin_image:
        try:
            data["checkin_image"] = request.build_absolute_uri(attendance.checkin_image.url)
        except Exception:
            pass
    if request and attendance.checkout_image:
        try:
            data["checkout_image"] = request.build_absolute_uri(attendance.checkout_image.url)
        except Exception:
            pass
    return data


def defer_attendance_v3_refresh(
    refresh_fn: Callable,
    attendance,
    user,
    assignment,
    shift,
    org_location,
    search_start_utc,
    search_end_utc,
) -> None:
    """Optional: recompute duration/pa_status in background (not recommended for production)."""
    att_id = attendance.pk

    def _run():
        from django.db import connection

        try:
            from dashboard.models import AttendanceCheckin

            att = AttendanceCheckin.objects.get(pk=att_id)
            refresh_fn(
                att, user, assignment, shift, org_location, search_start_utc, search_end_utc
            )
        except Exception as exc:
            logger.warning("Deferred kiosk attendance refresh failed att=%s: %s", att_id, exc)
        finally:
            connection.close()

    threading.Thread(target=_run, daemon=True).start()
