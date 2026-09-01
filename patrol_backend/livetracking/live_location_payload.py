"""Shared payload builders for live tracking REST + WebSocket."""

from patrol_backend.utils.timezone_utils import to_user_timezone


def build_location_update_payload(live_loc, requesting_user):
    """
    Build a location_update dict matching the WebSocket broadcast shape.
    `live_loc` must have user (and optionally assigned_site) select_related.
    """
    subject_user = live_loc.user
    last_updated_utc = live_loc.last_location_at or live_loc.last_updated

    if last_updated_utc:
        local_dt = to_user_timezone(last_updated_utc, requesting_user)
        timestamp_str = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        timestamp_iso = local_dt.isoformat()
        timestamp_utc = last_updated_utc.isoformat()
    else:
        timestamp_str = None
        timestamp_iso = None
        timestamp_utc = None

    return {
        "type": "location_update",
        "user_id": str(subject_user.id),
        "name": getattr(subject_user, "name", getattr(subject_user, "email", "Unknown User")),
        "role": getattr(subject_user, "role", "guard"),
        "lat": float(live_loc.latitude),
        "lng": float(live_loc.longitude),
        "timestamp": timestamp_str,
        "timestamp_iso": timestamp_iso,
        "timestamp_utc": timestamp_utc,
        "on_duty": True,
        "assigned_site_id": str(live_loc.assigned_site_id) if live_loc.assigned_site_id else None,
        "is_inside_boundary": live_loc.is_inside_boundary,
        "boundary_status": live_loc.boundary_state,
    }
