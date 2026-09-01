"""WebSocket dispatch helpers for tracking alerts (used by Phase 5 alert engine)."""


def build_tracking_alert_event(alert, *, subject_name="", site_name=""):
    """
    Build a channel-layer event dict for tracking_alert consumer handler.
    `alert` is a TrackingAlert model instance or dict-like payload.
    """
    if hasattr(alert, "alert_type"):
        alert_id = str(alert.id)
        alert_type = alert.alert_type
        site_id = str(alert.site_id)
        location_id = str(alert.location_id)
        subject_user_id = str(alert.subject_user_id)
        subject_role = alert.subject_role or ""
        message = alert.message or ""
        is_active = alert.is_active
        created_at = alert.created_at.isoformat() if alert.created_at else None
        latitude = float(alert.latitude) if alert.latitude is not None else None
        longitude = float(alert.longitude) if alert.longitude is not None else None
    else:
        alert_id = str(alert.get("id", ""))
        alert_type = alert.get("alert_type", "")
        site_id = str(alert.get("site_id", ""))
        location_id = str(alert.get("location_id", ""))
        subject_user_id = str(alert.get("subject_user_id", ""))
        subject_role = alert.get("subject_role", "")
        message = alert.get("message", "")
        is_active = alert.get("is_active", True)
        created_at = alert.get("created_at")
        latitude = alert.get("latitude")
        longitude = alert.get("longitude")

    return {
        "type": "tracking_alert",
        "alert_id": alert_id,
        "alert_type": alert_type,
        "site_id": site_id,
        "site_name": site_name,
        "location_id": location_id,
        "subject_user_id": subject_user_id,
        "subject_name": subject_name,
        "subject_role": subject_role,
        "message": message,
        "is_active": is_active,
        "latitude": latitude,
        "longitude": longitude,
        "timestamp_utc": created_at,
    }


async def dispatch_tracking_alert_ws(channel_layer, alert_event, *, site_id, recipient_user_ids=None):
    """
    Push a tracking_alert to site-scoped and per-user inbox groups.
    `alert_event` must include "type": "tracking_alert" (see build_tracking_alert_event).
    """
    if site_id:
        await channel_layer.group_send(f"site_{site_id}_tracking", alert_event)

    for user_id in recipient_user_ids or []:
        await channel_layer.group_send(f"tracking_user_{user_id}", alert_event)
