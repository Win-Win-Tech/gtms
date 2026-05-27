"""
Fast-path helpers for kiosk face_attendance (minimize DB round-trips and disk I/O).
"""
from __future__ import annotations

import logging
import threading
from datetime import timedelta
from typing import Any, Callable, Dict, Optional, Tuple

from django.core.files.base import ContentFile
from django.db import transaction
from django.db.models import F
from django.utils import timezone

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
    latest_checkin,
    latest_checkout,
    has_open_session: bool,
    min_checkout_minutes: int = 5,
) -> Tuple[str, Any, Any, int]:
    """
    Create CheckInLog + update AttendanceCheckin with one image write.
    Returns (action_mode, attendance, log, http_status).
    Raises ValueError('checkout_too_early') with remaining_seconds in args via caller.
    """
    from dashboard.models import AttendanceCheckin, CheckInLog

    action_mode = "checkout" if has_open_session else "checkin"
    if action_mode == "checkout" and latest_checkin:
        elapsed = int((timezone.now() - latest_checkin).total_seconds())
        if elapsed < min_checkout_minutes * 60:
            raise CheckoutTooEarly(
                remaining=max(0, (min_checkout_minutes * 60) - elapsed),
                min_checkout_minutes=min_checkout_minutes,
            )

    now = timezone.now()
    with transaction.atomic():
        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={"created_on": now, "shift_date": shift_start_date},
        )

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

        update_fields = ["shift_date", "site"]
        attendance.shift_date = shift_start_date
        attendance.site = matched_site

        if action_mode == "checkin":
            AttendanceCheckin.objects.filter(pk=attendance.pk).update(
                checkin_count=F("checkin_count") + 1,
                last_checkin_time=now,
            )
            attendance.refresh_from_db()
            if not attendance.checkin_time:
                attendance.checkin_time = now
                update_fields.append("checkin_time")
            attendance.latitude = lat
            attendance.longitude = lon
            update_fields.extend(["latitude", "longitude"])
            attendance.checkin_image.name = log.image.name
            update_fields.append("checkin_image")
            attendance.pa_status = "OW"
            update_fields.append("pa_status")
            attendance.save(update_fields=list(dict.fromkeys(update_fields)))
            return action_mode, attendance, log, 201

        AttendanceCheckin.objects.filter(pk=attendance.pk).update(
            checkout_count=F("checkout_count") + 1,
            last_checkout_time=now,
            checkout_time=now,
        )
        attendance.refresh_from_db()
        attendance.checkout_image.name = log.image.name
        update_fields.append("checkout_image")
        attendance.save(update_fields=list(dict.fromkeys(update_fields)))
        return action_mode, attendance, log, 200


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
        "checkin_time": attendance.checkin_time.isoformat() if attendance.checkin_time else None,
        "checkout_time": attendance.checkout_time.isoformat() if attendance.checkout_time else None,
        "status": getattr(attendance, "status", None) or "present",
        "checkin_count": int(attendance.checkin_count or 0),
        "checkout_count": int(attendance.checkout_count or 0),
        "duration_minutes": attendance.duration_minutes,
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
    """Recompute duration/pa_status in background so API returns faster."""
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
