"""Boundary breach / location-missing runtime (Phase 6)."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Optional

from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from patrol_backend.utils.boundary_utils import (
    evaluate_site_boundary,
    get_boundary_still_outside_reminder_min,
    get_location_missing_timeout_min,
    is_boundary_monitoring_active,
    is_site_location_missing_alerts_enabled,
)

from .alert_service import create_tracking_alert, resolve_tracking_alert
from .models import TrackingAlert, UserLiveLocation
from .ws_groups import get_on_duty_user_ids, get_open_checkin_for_user

logger = logging.getLogger(__name__)


def _resolve_location_missing_alerts(user, site) -> int:
    resolved = 0
    qs = TrackingAlert.objects.filter(
        subject_user=user,
        site=site,
        alert_type=TrackingAlert.AlertType.LOCATION_MISSING,
        is_active=True,
    )
    for alert in qs:
        resolve_tracking_alert(alert)
        resolved += 1
    return resolved


def _maybe_still_outside_reminder(
    *,
    site,
    location_id,
    breach_alert: TrackingAlert,
    now,
) -> None:
    reminder_min = get_boundary_still_outside_reminder_min(location_id)
    if reminder_min <= 0 or not breach_alert:
        return

    elapsed_min = (now - breach_alert.created_at).total_seconds() / 60
    if elapsed_min < reminder_min:
        return

    bucket = int(elapsed_min // reminder_min)
    cache_key = f"still_outside_reminder:{breach_alert.id}:{bucket}"
    if cache.get(cache_key):
        return

    cache.set(cache_key, 1, timeout=max(reminder_min * 60 * 2, 60))
    from .alert_service import dispatch_tracking_alert_push, dispatch_tracking_alert_ws

    dispatch_tracking_alert_ws(breach_alert)
    dispatch_tracking_alert_push(breach_alert)
    logger.info(
        "[BOUNDARY] Still-outside reminder sent alert=%s bucket=%s",
        breach_alert.id,
        bucket,
    )


@transaction.atomic
def process_location_boundary_update(
    *,
    user,
    live_loc: UserLiveLocation,
    site,
    lat: float,
    lng: float,
    checkin=None,
    server_now_utc=None,
) -> dict:
    """
    Evaluate boundary, update live_loc state, fire/resolve alerts.
    Returns broadcast fields: assigned_site_id, is_inside_boundary, boundary_status.
    """
    now = server_now_utc or timezone.now()
    if checkin is None:
        checkin = get_open_checkin_for_user(user)
    assigned_site = site or (checkin.site if checkin and getattr(checkin, "site_id", None) else None)

    # If user has no active checkin (e.g. was auto-checked out due to boundary breach),
    # check if they have an active breach alert so we can track when they return inside
    # the site boundary and automatically resolve the active alert.
    if not assigned_site:
        if live_loc.active_breach_alert_id and live_loc.active_breach_alert and live_loc.active_breach_alert.is_active:
            assigned_site = live_loc.active_breach_alert.site
        elif live_loc.assigned_site_id:
            assigned_site = live_loc.assigned_site
        else:
            active_breach = (
                TrackingAlert.objects.filter(
                    subject_user=user,
                    alert_type=TrackingAlert.AlertType.BOUNDARY_BREACH,
                    is_active=True,
                )
                .select_related("site")
                .first()
            )
            if active_breach and active_breach.site:
                assigned_site = active_breach.site
                live_loc.active_breach_alert = active_breach

    if not assigned_site:
        live_loc.boundary_state = UserLiveLocation.BoundaryState.UNKNOWN
        live_loc.is_inside_boundary = None
        live_loc.save(update_fields=["boundary_state", "is_inside_boundary"])
        return {
            "assigned_site_id": None,
            "is_inside_boundary": None,
            "boundary_status": live_loc.boundary_state,
        }

    location_id = getattr(assigned_site, "location_id", None) or getattr(
        live_loc.location, "id", None
    )
    prev_state = live_loc.boundary_state or UserLiveLocation.BoundaryState.UNKNOWN

    if not is_boundary_monitoring_active(location_id, assigned_site):
        live_loc.boundary_state = UserLiveLocation.BoundaryState.UNKNOWN
        live_loc.is_inside_boundary = None
        live_loc.save(update_fields=["boundary_state", "is_inside_boundary"])
        _resolve_location_missing_alerts(user, assigned_site)
        return {
            "assigned_site_id": str(assigned_site.id),
            "is_inside_boundary": None,
            "boundary_status": live_loc.boundary_state,
        }

    previous_for_eval = prev_state if prev_state != UserLiveLocation.BoundaryState.UNKNOWN else None
    inside = evaluate_site_boundary(
        assigned_site,
        float(lat),
        float(lng),
        location_id=location_id,
        previous_boundary_state=previous_for_eval,
    )

    if inside is True:
        new_state = UserLiveLocation.BoundaryState.INSIDE
        is_inside = True
    elif inside is False:
        new_state = UserLiveLocation.BoundaryState.OUTSIDE
        is_inside = False
    else:
        new_state = UserLiveLocation.BoundaryState.UNKNOWN
        is_inside = None

    _resolve_location_missing_alerts(user, assigned_site)

    update_fields = ["boundary_state", "is_inside_boundary", "active_breach_alert"]

    has_active_breach_alert = bool(
        live_loc.active_breach_alert_id
        and live_loc.active_breach_alert
        and live_loc.active_breach_alert.is_active
    )
    if not has_active_breach_alert:
        # Also check DB in case UserLiveLocation FK wasn't populated
        has_active_breach_alert = TrackingAlert.objects.filter(
            subject_user=user,
            site=assigned_site,
            alert_type=TrackingAlert.AlertType.BOUNDARY_BREACH,
            is_active=True,
        ).exists()

    alert = None
    if new_state == UserLiveLocation.BoundaryState.OUTSIDE and not has_active_breach_alert:
        alert, _recipients = create_tracking_alert(
            alert_type=TrackingAlert.AlertType.BOUNDARY_BREACH,
            site=assigned_site,
            subject_user=user,
            attendance=checkin,
            latitude=lat,
            longitude=lng,
        )
        if alert:
            live_loc.active_breach_alert = alert
            logger.info(
                "[BOUNDARY] Breach alert created user=%s site=%s alert=%s",
                user.id,
                assigned_site.id,
                alert.id,
            )
    elif new_state == UserLiveLocation.BoundaryState.INSIDE:
        if live_loc.active_breach_alert_id:
            resolve_tracking_alert(live_loc.active_breach_alert, dispatch_ws=True)
            live_loc.active_breach_alert = None

        # Resolve all active boundary breach alerts for this user at this site
        active_breaches = TrackingAlert.objects.filter(
            subject_user=user,
            site=assigned_site,
            alert_type=TrackingAlert.AlertType.BOUNDARY_BREACH,
            is_active=True,
        )
        for ab in active_breaches:
            resolve_tracking_alert(ab, dispatch_ws=True)
            logger.info(
                "[BOUNDARY] Resolved active breach alert=%s for user=%s re-entering site=%s",
                ab.id,
                user.id,
                assigned_site.id,
            )

        # If user is checked out, clear assigned_site now that they are safely inside and alert is resolved
        if checkin is None and not get_open_checkin_for_user(user):
            live_loc.assigned_site = None
            update_fields.append("assigned_site")
    elif (
        new_state == UserLiveLocation.BoundaryState.OUTSIDE
        and live_loc.active_breach_alert_id
        and live_loc.active_breach_alert
        and live_loc.active_breach_alert.is_active
    ):
        _maybe_still_outside_reminder(
            site=assigned_site,
            location_id=location_id,
            breach_alert=live_loc.active_breach_alert,
            now=now,
        )

    live_loc.boundary_state = new_state
    live_loc.is_inside_boundary = is_inside
    live_loc.save(update_fields=update_fields)

    # When breach alert is triggered for a checked-in user, automatically check them out
    did_auto_checkout = False
    if alert:
        try:
            res = auto_checkout_user_on_boundary_breach(
                user=user,
                site=assigned_site,
                lat=lat,
                lng=lng,
                alert=alert,
                checkin=checkin,
                now_utc=now,
            )
            if res:
                did_auto_checkout = True
        except Exception:
            logger.exception(
                "[BOUNDARY_AUTO_CHECKOUT] Failed to auto-checkout user=%s on breach alert=%s",
                getattr(user, "id", "?"),
                getattr(alert, "id", "?"),
            )

    return {
        "assigned_site_id": str(assigned_site.id) if assigned_site else None,
        "is_inside_boundary": live_loc.is_inside_boundary,
        "boundary_status": live_loc.boundary_state,
        "did_auto_checkout": did_auto_checkout,
    }


def auto_checkout_user_on_boundary_breach(
    *,
    user,
    site,
    lat: float,
    lng: float,
    alert=None,
    checkin=None,
    now_utc=None,
):
    """
    Automatically checks out a user when boundary breach / SOS alert is triggered.
    Creates checkout CheckInLog, updates AttendanceCheckin to checked_out,
    recalculates shift duration & PA status, and ensures the breach alert is preserved.
    """
    from dashboard.models import CheckInLog, AttendanceCheckin
    from dashboard.views import (
        _attendance_v3_shift_window_utc,
        _attendance_v3_refresh_saved_fields,
    )
    from patrol_backend.utils.attendance_resolve import (
        build_log_window_filter,
        apply_v4_attendance_after_log,
    )
    from patrol_backend.utils.timezone_utils import get_user_timezone, get_user_today

    if checkin is None:
        checkin = get_open_checkin_for_user(user)

    if not checkin:
        logger.info(
            "[BOUNDARY_AUTO_CHECKOUT] No open checkin found for user=%s; skipping auto-checkout",
            getattr(user, "id", "?"),
        )
        return None

    try:
        checkin = AttendanceCheckin.objects.select_for_update().get(id=checkin.id)
    except AttendanceCheckin.DoesNotExist:
        return None

    assignment = getattr(checkin, "assignment", None)
    shift = getattr(checkin, "shift", None)
    org_location = getattr(checkin, "org_location", None)
    if not (assignment and shift and org_location):
        logger.warning(
            "[BOUNDARY_AUTO_CHECKOUT] Missing assignment/shift/location on checkin=%s user=%s",
            checkin.id,
            getattr(user, "id", "?"),
        )
        return None

    user_tz = get_user_timezone(user)
    shift_start_date = checkin.shift_date or (
        checkin.checkin_time.astimezone(user_tz).date() if checkin.checkin_time else get_user_today(user_tz)
    )

    curr_now = now_utc or timezone.now()
    search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
        shift_start_date, shift, user_tz, location_id=getattr(org_location, "id", None)
    )
    if curr_now >= search_end_utc:
        search_end_utc = curr_now + timedelta(minutes=15)
    if checkin.checkin_time and checkin.checkin_time <= search_start_utc:
        search_start_utc = checkin.checkin_time - timedelta(minutes=15)

    log_filter = build_log_window_filter(
        user, assignment, shift, org_location, search_start_utc, search_end_utc
    )

    matched_site = checkin.site or site

    reason_text = "Boundary breach: moved outside site boundary"
    try:
        if matched_site and getattr(matched_site, "latitude", None) and getattr(matched_site, "longitude", None):
            from geopy.distance import geodesic
            dist_m = geodesic((lat, lng), (float(matched_site.latitude), float(matched_site.longitude))).meters
            radius_m = getattr(matched_site, "boundary_radius_m", None)
            if radius_m and float(radius_m) > 0:
                excess_m = max(0.0, dist_m - float(radius_m))
                reason_text = f"Boundary breach: {dist_m:.1f}m from site center (radius: {float(radius_m):.1f}m, {excess_m:.1f}m outside)"
            else:
                reason_text = f"Boundary breach: {dist_m:.1f}m from site center"
    except Exception:
        pass

    # Create checkout CheckInLog with auto-checkout audit tracking
    checkout_log = CheckInLog.objects.create(
        guard=user,
        assignment=assignment,
        shift=shift,
        org_location=org_location,
        type="checkout",
        latitude=lat,
        longitude=lng,
        site=matched_site,
        is_auto_checkout=True,
        auto_checkout_reason=reason_text,
    )

    # Explicitly ensure checkout_time, last_checkout_time and status are set
    checkin.checkout_time = checkout_log.timestamp
    checkin.last_checkout_time = checkout_log.timestamp
    checkin.status = "checked_out"

    breach_remark = "[Auto-checkout: Boundary breach alert triggered]"
    if checkin.remarks:
        if breach_remark not in checkin.remarks:
            checkin.remarks = f"{checkin.remarks} {breach_remark}"
    else:
        checkin.remarks = breach_remark

    alert_id = getattr(alert, "id", None)

    apply_v4_attendance_after_log(
        attendance=checkin,
        user=user,
        assignment=assignment,
        shift=shift,
        org_location=org_location,
        shift_start_date=shift_start_date,
        matched_site=matched_site,
        log_filter=log_filter,
        search_start_utc=search_start_utc,
        search_end_utc=search_end_utc,
        action_mode="checkout",
        raw_bytes=None,
        img_name="auto_checkout.jpg",
        user_tz=user_tz,
        refresh_fn=_attendance_v3_refresh_saved_fields,
        skip_sibling_reconcile=False,
        preserve_alert_id=alert_id,
    )

    logger.info(
        "[BOUNDARY_AUTO_CHECKOUT] Auto-checkout completed for user=%s attendance=%s alert=%s site=%s lat=%.6f lng=%.6f",
        getattr(user, "id", "?"),
        getattr(checkin, "id", "?"),
        alert_id,
        getattr(matched_site, "id", "?") if matched_site else None,
        lat,
        lng,
    )
    return checkin


@transaction.atomic
def clear_tracking_state_on_checkout(user, attendance=None, preserve_alert_id=None) -> None:
    """Resolve active alerts and reset boundary fields when a user checks out."""
    live_loc = (
        UserLiveLocation.objects.select_related("active_breach_alert", "assigned_site")
        .filter(user=user)
        .first()
    )

    site = None
    if attendance is not None and getattr(attendance, "site_id", None):
        site = attendance.site
    elif live_loc and live_loc.assigned_site_id:
        site = live_loc.assigned_site

    active_alerts = TrackingAlert.objects.filter(subject_user=user, is_active=True)
    if preserve_alert_id:
        active_alerts = active_alerts.exclude(id=preserve_alert_id)
    if site is not None:
        active_alerts = active_alerts.filter(site=site)

    for alert in active_alerts:
        resolve_tracking_alert(alert)

    if not live_loc:
        return

    update_fields = [
        "assigned_site",
        "is_inside_boundary",
        "boundary_state",
    ]
    if preserve_alert_id and str(live_loc.active_breach_alert_id) == str(preserve_alert_id):
        # Keep active breach alert and assigned_site intact on UserLiveLocation so admins see it
        # and so boundary re-entry can automatically resolve the active alert
        if site:
            live_loc.assigned_site = site
    else:
        live_loc.active_breach_alert = None
        live_loc.assigned_site = None
        update_fields.append("active_breach_alert")

    live_loc.is_inside_boundary = None
    live_loc.boundary_state = UserLiveLocation.BoundaryState.UNKNOWN
    if not preserve_alert_id:
        live_loc.last_location_at = None
        update_fields.append("last_location_at")

    live_loc.save(update_fields=update_fields)
    logger.info(
        "[BOUNDARY] Cleared tracking state on checkout for user=%s (preserved_alert=%s)",
        user.id,
        preserve_alert_id,
    )


def run_location_missing_checks(location_id=None) -> dict:
    """
    Background scan: on-duty users with stale last_location_at get location_missing alerts.
    Returns summary counts for logging/monitoring.
    """
    from authapp.models import User

    now = timezone.now()
    created = 0
    skipped = 0

    for user_id in get_on_duty_user_ids(location_id=location_id):
        user = User.objects.filter(id=user_id, is_active=True, is_deleted=False).first()
        if not user:
            skipped += 1
            continue

        checkin = get_open_checkin_for_user(user)
        if not checkin:
            skipped += 1
            continue

        site = checkin.site
        if not site:
            skipped += 1
            continue

        org_location_id = site.location_id
        if not is_site_location_missing_alerts_enabled(site):
            skipped += 1
            continue

        timeout_min = get_location_missing_timeout_min(org_location_id)
        live_loc = UserLiveLocation.objects.filter(user=user).first()
        last_at = live_loc.last_location_at if live_loc else None
        if not last_at:
            last_at = checkin.last_checkin_time or checkin.checkin_time
        if not last_at:
            skipped += 1
            continue

        if now - last_at < timedelta(minutes=timeout_min):
            skipped += 1
            continue

        if TrackingAlert.objects.filter(
            subject_user=user,
            site=site,
            alert_type=TrackingAlert.AlertType.LOCATION_MISSING,
            is_active=True,
        ).exists():
            skipped += 1
            continue

        lat = live_loc.latitude if live_loc and live_loc.latitude is not None else None
        lng = live_loc.longitude if live_loc and live_loc.longitude is not None else None

        create_tracking_alert(
            alert_type=TrackingAlert.AlertType.LOCATION_MISSING,
            site=site,
            subject_user=user,
            attendance=checkin,
            latitude=lat,
            longitude=lng,
        )
        created += 1

    summary = {"created": created, "skipped": skipped, "location_id": location_id}
    logger.info("[BOUNDARY] location_missing scan %s", summary)
    return summary
