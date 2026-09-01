"""Tracking alert create/resolve, recipient resolution, and WebSocket dispatch."""

import logging
from typing import Iterable, List, Optional, Sequence, Tuple

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from authapp.models import User
from authapp.site_access import users_queryset_for_site

from .models import SiteAlertRecipientConfig, TrackingAlert, TrackingAlertRecipient
from .ws_dispatch import build_tracking_alert_event, dispatch_tracking_alert_ws as _dispatch_tracking_alert_ws_async

logger = logging.getLogger(__name__)

ALERT_TYPE_NOTIFY_FIELD = {
    TrackingAlert.AlertType.BOUNDARY_BREACH: "notify_boundary_breach",
    TrackingAlert.AlertType.LOCATION_MISSING: "notify_location_missing",
}


def _default_alert_message(alert_type: str, *, subject_name: str, site_name: str) -> str:
    if alert_type == TrackingAlert.AlertType.BOUNDARY_BREACH:
        return f"{subject_name} left the site boundary at {site_name}"
    if alert_type == TrackingAlert.AlertType.LOCATION_MISSING:
        return f"No GPS received from {subject_name} at {site_name}"
    if alert_type == TrackingAlert.AlertType.MANUAL_SOS:
        return f"Emergency SOS from {subject_name} at {site_name}"
    return f"Tracking alert for {subject_name} at {site_name}"


def get_recipients_for_alert(site, alert_type: str) -> List[User]:
    """
    Users who should receive this alert: role in SiteAlertRecipientConfig for the site,
    notify flag matches alert_type, and user has site access.
    """
    notify_field = ALERT_TYPE_NOTIFY_FIELD.get(alert_type)
    if not notify_field:
        if alert_type != TrackingAlert.AlertType.MANUAL_SOS:
            return []
        configs = SiteAlertRecipientConfig.objects.filter(site=site).filter(
            Q(notify_boundary_breach=True) | Q(notify_location_missing=True)
        )
    else:
        configs = SiteAlertRecipientConfig.objects.filter(site=site, **{notify_field: True})

    role_names = list(configs.select_related("role").values_list("role__name", flat=True))
    if not role_names:
        return []

    role_filter = Q()
    for name in role_names:
        role_filter |= Q(role__iexact=name)

    return list(
        users_queryset_for_site(site)
        .filter(role_filter, is_active=True, is_deleted=False)
        .distinct()
    )


def _create_recipient_rows(alert: TrackingAlert, recipients: Sequence[User]) -> None:
    for user in recipients:
        TrackingAlertRecipient.objects.get_or_create(
            alert=alert,
            user=user,
            channel=TrackingAlertRecipient.Channel.IN_APP,
        )


def dispatch_tracking_alert_ws(
    alert: TrackingAlert,
    recipients: Optional[Iterable[User]] = None,
) -> None:
    """Push tracking_alert to site + per-user channel groups (sync wrapper)."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning("[TRACKING_ALERT] No channel layer configured; WS dispatch skipped")
        return

    if recipients is None:
        recipients = get_recipients_for_alert(alert.site, alert.alert_type)

    subject = alert.subject_user
    subject_name = getattr(subject, "name", None) or getattr(subject, "email", "Unknown")
    event = build_tracking_alert_event(
        alert,
        subject_name=subject_name,
        site_name=alert.site.name,
    )
    recipient_ids = [str(user.id) for user in recipients]

    async_to_sync(_dispatch_tracking_alert_ws_async)(
        channel_layer,
        event,
        site_id=str(alert.site_id),
        recipient_user_ids=recipient_ids,
    )


@transaction.atomic
def create_tracking_alert(
    *,
    alert_type: str,
    site,
    subject_user: User,
    attendance=None,
    latitude=None,
    longitude=None,
    message: str = "",
    dispatch_ws: bool = True,
) -> Tuple[TrackingAlert, List[User]]:
    """Create alert row, recipient deliveries, and optionally push via WebSocket."""
    subject_name = getattr(subject_user, "name", None) or getattr(subject_user, "email", "Unknown")
    if not message:
        message = _default_alert_message(
            alert_type,
            subject_name=subject_name,
            site_name=site.name,
        )

    alert = TrackingAlert.objects.create(
        alert_type=alert_type,
        site=site,
        location=site.location,
        subject_user=subject_user,
        subject_role=getattr(subject_user, "role", "") or "",
        attendance=attendance,
        latitude=latitude,
        longitude=longitude,
        message=message,
        is_active=True,
    )

    recipients = get_recipients_for_alert(site, alert_type)
    _create_recipient_rows(alert, recipients)

    if dispatch_ws:
        dispatch_tracking_alert_ws(alert, recipients)

    logger.info(
        "[TRACKING_ALERT] Created %s alert=%s site=%s subject=%s recipients=%s",
        alert_type,
        alert.id,
        site.id,
        subject_user.id,
        len(recipients),
    )
    return alert, recipients


@transaction.atomic
def resolve_tracking_alert(
    alert: TrackingAlert,
    *,
    dispatch_ws: bool = True,
) -> TrackingAlert:
    """Mark alert resolved and optionally notify recipients."""
    if not alert.is_active:
        return alert

    alert.is_active = False
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["is_active", "resolved_at"])

    if dispatch_ws:
        dispatch_tracking_alert_ws(alert)

    logger.info("[TRACKING_ALERT] Resolved alert=%s type=%s", alert.id, alert.alert_type)
    return alert
