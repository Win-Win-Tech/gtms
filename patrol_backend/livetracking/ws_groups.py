"""WebSocket channel group resolution for live tracking and tracking alerts."""

from django.db.models import Q

from authapp.site_access import caller_can_access_site
from authapp.views import resolve_role_for_user

LIVE_TRACKING_PAGE = "Live Tracking"
LEGACY_LIVE_MAP_ROLES = frozenset({"admin", "so", "fo", "superadmin", "super_admin"})


def _is_superadmin(user):
    role = (getattr(user, "role", None) or "").lower()
    return bool(getattr(user, "is_superuser", False) or role in ("superadmin", "super_admin"))


def user_can_view_live_map(user):
    """True when the user should receive org-wide live location updates."""
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if _is_superadmin(user):
        return True

    role_obj = resolve_role_for_user(user)
    if role_obj and LIVE_TRACKING_PAGE in (role_obj.pages or []):
        return True

    return (getattr(user, "role", None) or "").lower() in LEGACY_LIVE_MAP_ROLES


def get_alert_recipient_site_ids(user):
    """
    Site IDs where the user is configured as a tracking-alert recipient
    and has site access.
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []

    role_obj = resolve_role_for_user(user)
    if not role_obj:
        return []

    from livetracking.models import SiteAlertRecipientConfig

    site_ids = []
    configs = SiteAlertRecipientConfig.objects.filter(
        recipient_role=role_obj,
    ).filter(
        Q(notify_boundary_breach=True) | Q(notify_location_missing=True)
    ).select_related("site")
    for config in configs:
        if caller_can_access_site(user, config.site):
            site_ids.append(str(config.site_id))
    return sorted(set(site_ids))


def resolve_ws_groups_for_user(user):
    """
    Return channel-layer group names for a connected WebSocket client.

    - Superadmin: all_locations
    - Live map viewers: location_{org_id}
    - Alert recipients: tracking_user_{user_id} + site_{site_id}_tracking per site
    - On-duty mobile senders: no listen groups (send-only)
    """
    if not user or not getattr(user, "is_authenticated", False):
        return []

    groups = set()
    location_id = getattr(user, "location_id", None)

    if _is_superadmin(user):
        groups.add("all_locations")
        return sorted(groups)

    if user_can_view_live_map(user) and location_id:
        groups.add(f"location_{location_id}")

    alert_site_ids = get_alert_recipient_site_ids(user)
    if alert_site_ids:
        groups.add(f"tracking_user_{user.id}")
        for site_id in alert_site_ids:
            groups.add(f"site_{site_id}_tracking")

    return sorted(groups)


OPEN_CHECKIN_Q = Q(last_checkin_time__isnull=False, last_checkout_time__isnull=True) | Q(
    last_checkin_time__isnull=True,
    checkin_time__isnull=False,
    checkout_time__isnull=True,
)


def get_open_checkin_for_user(user):
    """Return the user's current open AttendanceCheckin row, if any."""
    if not user or not getattr(user, "is_authenticated", False):
        return None

    from dashboard.models import AttendanceCheckin

    return (
        AttendanceCheckin.objects.filter(guard=user)
        .filter(OPEN_CHECKIN_Q)
        .select_related("site", "org_location")
        .order_by("-last_checkin_time", "-checkin_time")
        .first()
    )


def get_on_duty_user_ids(location_id=None):
    """User IDs with an open AttendanceCheckin, optionally scoped to one org."""
    from dashboard.models import AttendanceCheckin

    qs = AttendanceCheckin.objects.filter(OPEN_CHECKIN_Q)
    if location_id:
        qs = qs.filter(org_location_id=location_id)
    return qs.values_list("guard_id", flat=True).distinct()


def get_on_duty_context(user):
    """On-duty context for a location_update sender."""
    checkin = get_open_checkin_for_user(user)
    assigned_site = checkin.site if checkin and checkin.site_id else None
    org_location = checkin.org_location if checkin and checkin.org_location_id else None
    return {
        "on_duty": bool(checkin),
        "checkin": checkin,
        "assigned_site_id": str(assigned_site.id) if assigned_site else None,
        "assigned_site": assigned_site,
        "org_location": org_location,
    }
