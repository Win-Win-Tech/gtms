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
    assigned_site = site

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

    if (
        prev_state == UserLiveLocation.BoundaryState.INSIDE
        and new_state == UserLiveLocation.BoundaryState.OUTSIDE
    ):
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
    elif (
        prev_state == UserLiveLocation.BoundaryState.OUTSIDE
        and new_state == UserLiveLocation.BoundaryState.INSIDE
    ):
        if live_loc.active_breach_alert_id:
            resolve_tracking_alert(live_loc.active_breach_alert)
            live_loc.active_breach_alert = None
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

    return {
        "assigned_site_id": str(assigned_site.id),
        "is_inside_boundary": live_loc.is_inside_boundary,
        "boundary_status": live_loc.boundary_state,
    }


@transaction.atomic
def clear_tracking_state_on_checkout(user, attendance=None) -> None:
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
    if site is not None:
        active_alerts = active_alerts.filter(site=site)

    for alert in active_alerts:
        resolve_tracking_alert(alert)

    if not live_loc:
        return

    live_loc.active_breach_alert = None
    live_loc.assigned_site = None
    live_loc.is_inside_boundary = None
    live_loc.boundary_state = UserLiveLocation.BoundaryState.UNKNOWN
    live_loc.last_location_at = None
    live_loc.save(
        update_fields=[
            "active_breach_alert",
            "assigned_site",
            "is_inside_boundary",
            "boundary_state",
            "last_location_at",
        ]
    )
    logger.info("[BOUNDARY] Cleared tracking state on checkout for user=%s", user.id)


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
