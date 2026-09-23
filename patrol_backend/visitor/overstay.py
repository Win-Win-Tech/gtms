"""Vehicle overstay detection + visitor NotificationLog delivery."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any, Dict, List

from django.db.models import Q
from django.utils import timezone

from authapp.models import User
from authapp.site_access import users_queryset_for_site
from notifications.services import notify_vehicle_overstay
from scheduler.models import SiteSetting

from .anpr.gate import normalize_plate
from .models import SiteVehicleOverstayRecipient, VehicleOverstayWhitelist, VisitorEntry

logger = logging.getLogger(__name__)

OVERSTAY_HOURS_KEY = "vehicle_overstay_hours"
DEFAULT_OVERSTAY_HOURS = 4.0


def get_overstay_hours(location_id) -> float:
    raw = SiteSetting.get_setting(
        OVERSTAY_HOURS_KEY,
        location_id=location_id,
        default_value=str(DEFAULT_OVERSTAY_HOURS),
    )
    try:
        hours = float(raw)
    except (TypeError, ValueError):
        return DEFAULT_OVERSTAY_HOURS
    return hours if hours > 0 else DEFAULT_OVERSTAY_HOURS


def is_plate_whitelisted(location_id, vehicle_number: str) -> bool:
    plate = normalize_plate(vehicle_number)
    if not plate or not location_id:
        return False
    return VehicleOverstayWhitelist.objects.filter(
        location_id=location_id,
        vehicle_number=plate,
        is_deleted=False,
    ).exists()


def get_overstay_recipients_for_site(site) -> List[User]:
    """
    Users who receive overstay SOS:
    - Configured SiteVehicleOverstayRecipient roles with site access
    - Always: org Admin users for the site's location (Admin is not configurable in UI)
    """
    if site is None:
        return []

    role_names = [
        name
        for name in SiteVehicleOverstayRecipient.objects.filter(site=site)
        .select_related("recipient_role")
        .values_list("recipient_role__name", flat=True)
        if name and str(name).lower() != "admin"
    ]

    role_users: List[User] = []
    if role_names:
        role_filter = Q()
        for name in role_names:
            role_filter |= Q(role__iexact=name)
        role_users = list(
            users_queryset_for_site(site)
            .filter(role_filter, is_active=True, is_deleted=False)
            .distinct()
        )

    location_id = getattr(site, "location_id", None)
    admin_users: List[User] = []
    if location_id:
        admin_users = list(
            User.objects.filter(
                location_id=location_id,
                role__iexact="admin",
                is_active=True,
                is_deleted=False,
            )
        )

    return list({u.id: u for u in (role_users + admin_users)}.values())


def try_send_vehicle_overstay_alert(entry: VisitorEntry) -> bool:
    """
    Notify configured roles + org Admin via NotificationLog + FCM.
    Returns False when site missing or no recipients (caller leaves overstay_alert_sent_at null).
    """
    site = getattr(entry, "site", None)
    if site is None:
        logger.debug("[OVERSTAY] skip notify — no site entry=%s", entry.id)
        return False

    recipients = get_overstay_recipients_for_site(site)
    if not recipients:
        logger.debug(
            "[OVERSTAY] skip notify — no recipients (roles/admin) site=%s entry=%s",
            site.id,
            entry.id,
        )
        return False

    # With no configured roles, Admin alone still receives SOS.
    created = notify_vehicle_overstay(entry, recipients)
    return created > 0


def process_vehicle_overstay(*, location_id=None) -> Dict[str, Any]:
    """
    Find checked-in vehicles past the org overstay threshold and attempt alert once.

    Leaves overstay_alert_sent_at null when notify fails (e.g. no recipients),
    so the next run can still send after roles are configured.
    """
    now = timezone.now()
    qs = (
        VisitorEntry.objects.filter(
            is_deleted=False,
            status=VisitorEntry.STATUS_CHECKED_IN,
            overstay_alert_sent_at__isnull=True,
            check_in_time__isnull=False,
        )
        .exclude(Q(vehicle_number__isnull=True) | Q(vehicle_number=""))
        .select_related("location", "site", "visitor")
    )
    if location_id:
        qs = qs.filter(location_id=location_id)

    scanned = 0
    skipped_threshold = 0
    skipped_whitelist = 0
    alerted = 0
    deferred = 0

    for entry in qs.iterator(chunk_size=200):
        scanned += 1
        hours = get_overstay_hours(entry.location_id)
        cutoff = now - timedelta(hours=hours)
        if entry.check_in_time > cutoff:
            skipped_threshold += 1
            continue

        plate = normalize_plate(entry.vehicle_number)
        if not plate:
            skipped_threshold += 1
            continue

        if is_plate_whitelisted(entry.location_id, plate):
            skipped_whitelist += 1
            continue

        sent = try_send_vehicle_overstay_alert(entry)
        if sent:
            entry.overstay_alert_sent_at = now
            entry.save(update_fields=["overstay_alert_sent_at", "modified_on"])
            alerted += 1
            logger.info(
                "[OVERSTAY] alerted entry=%s plate=%s hours=%.2f",
                entry.id,
                plate,
                hours,
            )
        else:
            deferred += 1
            logger.debug(
                "[OVERSTAY] deferred (no notify) entry=%s plate=%s hours=%.2f",
                entry.id,
                plate,
                hours,
            )

    summary = {
        "scanned": scanned,
        "skipped_threshold": skipped_threshold,
        "skipped_whitelist": skipped_whitelist,
        "alerted": alerted,
        "deferred": deferred,
    }
    logger.info("[OVERSTAY] process_vehicle_overstay %s", summary)
    return summary
