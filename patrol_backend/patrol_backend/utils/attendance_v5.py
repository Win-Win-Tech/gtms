"""Site resolution and punch helpers for checkin_v5 / checkout_v5."""

from __future__ import annotations

from typing import Optional, Tuple

from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.site_access import assert_caller_can_access_site, caller_can_access_site, get_site_or_error
from patrol_backend.utils.proximity_utils import (
    get_nearest_site_within_proximity,
    is_within_site_attendance_radius,
)


def _normalize_site_id(site_id) -> Optional[str]:
    if site_id is None:
        return None
    text = str(site_id).strip()
    if not text or text.lower() in ("null", "none", "undefined"):
        return None
    return text


def resolve_v5_punch_site(
    *,
    site_id,
    latitude: float,
    longitude: float,
    org_location_id,
    user,
) -> Tuple[object, float, str]:
    """
    Resolve LocationSite for v5 punch.
    - site_id provided → use that site (must belong to org, user access, within radius)
    - site_id empty → nearest site within attendance_distance (user-accessible only)

    Returns (site, distance_meters, resolution) where resolution is 'request' or 'nearest'.
    """
    from authapp.site_access import allowed_site_ids

    normalized = _normalize_site_id(site_id)
    if normalized:
        site = get_site_or_error(normalized)
        if str(site.location_id) != str(org_location_id):
            raise ValidationError({"site_id": "Site does not belong to this organisation."})
        assert_caller_can_access_site(user, site)
        if not is_within_site_attendance_radius(latitude, longitude, org_location_id, site):
            raise ValidationError(
                {"error": "You are not within the authorized boundary of this site."}
            )
        from patrol_backend.utils.proximity_utils import distance_to_site_meters

        return site, distance_to_site_meters(latitude, longitude, site), "request"

    allowed = allowed_site_ids(user)
    site, dist = get_nearest_site_within_proximity(
        latitude, longitude, org_location_id, allowed_site_ids=allowed
    )
    if not site:
        raise ValidationError(
            {"error": "You are not within the authorized boundary of any site for this location."}
        )
    if not caller_can_access_site(user, site):
        raise PermissionDenied("You are not allowed to use this site.")
    return site, dist, "nearest"


def get_open_checkin_log(user, assignment, shift, org_location, search_start_utc, search_end_utc):
    """
    Latest check-in log for an open session in the shift window, or None.
    Open = no checkout after that check-in.
    """
    from dashboard.models import CheckInLog
    from patrol_backend.utils.attendance_resolve import build_log_window_filter

    log_filter = build_log_window_filter(
        user, assignment, shift, org_location, search_start_utc, search_end_utc
    )
    latest_checkin = (
        CheckInLog.objects.filter(**log_filter, type="checkin")
        .order_by("-timestamp")
        .select_related("site")
        .first()
    )
    if not latest_checkin:
        return None

    latest_checkout = (
        CheckInLog.objects.filter(**log_filter, type="checkout")
        .order_by("-timestamp")
        .first()
    )
    if latest_checkout and latest_checkout.timestamp >= latest_checkin.timestamp:
        return None
    return latest_checkin
