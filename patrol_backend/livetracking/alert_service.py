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
from authapp.views import resolve_role_for_user
from patrol_backend.utils.boundary_utils import is_alert_type_enabled_for_site

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


def get_recipients_for_alert(
    site,
    alert_type: str,
    *,
    subject_user: Optional[User] = None,
    subject_role_name: Optional[str] = None,
) -> List[User]:
    """
    Users who should receive this alert:
    - Config rows where subject_role matches the person who triggered the alert
    - notify flag matches alert_type
    - recipient users have that recipient_role and site access
    """
    notify_field = ALERT_TYPE_NOTIFY_FIELD.get(alert_type)

    role_obj = None
    if subject_user is not None:
        role_obj = resolve_role_for_user(subject_user)
    if role_obj is None and subject_role_name:
        from authapp.models import Role

        name = subject_role_name.strip()
        location_id = getattr(site, "location_id", None)
        if location_id:
            role_obj = Role.objects.filter(name__iexact=name, location_id=location_id).first()
        if role_obj is None:
            role_obj = Role.objects.filter(name__iexact=name, location__isnull=True).first()

    if role_obj is None:
        logger.info(
            "[TRACKING_ALERT] No subject role resolved for site=%s type=%s subject=%s",
            getattr(site, "id", None),
            alert_type,
            getattr(subject_user, "id", None),
        )
        return []

    if not notify_field:
        if alert_type != TrackingAlert.AlertType.MANUAL_SOS:
            return []
        configs = SiteAlertRecipientConfig.objects.filter(
            site=site,
            subject_role=role_obj,
        ).filter(Q(notify_boundary_breach=True) | Q(notify_location_missing=True))
    else:
        configs = SiteAlertRecipientConfig.objects.filter(
            site=site,
            subject_role=role_obj,
            **{notify_field: True},
        )

    recipient_role_names = list(
        configs.select_related("recipient_role").values_list("recipient_role__name", flat=True)
    )
    if not recipient_role_names:
        return []

    role_filter = Q()
    for name in recipient_role_names:
        role_filter |= Q(role__iexact=name)

    qs = (
        users_queryset_for_site(site)
        .filter(role_filter, is_active=True, is_deleted=False)
        .distinct()
    )
    if subject_user is not None:
        qs = qs.exclude(id=subject_user.id)

    return list(qs)


def _create_recipient_rows(alert: TrackingAlert, recipients: Sequence[User]) -> None:
    for user in recipients:
        TrackingAlertRecipient.objects.get_or_create(
            alert=alert,
            user=user,
            channel=TrackingAlertRecipient.Channel.IN_APP,
        )


def _resolve_recipients(
    alert: TrackingAlert,
    recipients: Optional[Iterable[User]] = None,
) -> List[User]:
    if recipients is not None:
        return list(recipients)
    return get_recipients_for_alert(
        alert.site,
        alert.alert_type,
        subject_user=alert.subject_user,
        subject_role_name=alert.subject_role,
    )


def _alert_subject_site_names(alert: TrackingAlert) -> Tuple[str, str]:
    subject = alert.subject_user
    subject_name = getattr(subject, "name", None) or getattr(subject, "email", "Unknown")
    site_name = getattr(alert.site, "name", "") or ""
    return subject_name, site_name


def _tracking_alert_push_title(alert: TrackingAlert) -> str:
    if not alert.is_active:
        return "Tracking alert resolved"
    if alert.alert_type == TrackingAlert.AlertType.BOUNDARY_BREACH:
        return "Boundary breach"
    if alert.alert_type == TrackingAlert.AlertType.LOCATION_MISSING:
        return "Location missing"
    if alert.alert_type == TrackingAlert.AlertType.MANUAL_SOS:
        return "Emergency SOS"
    return "Tracking alert"


def _tracking_alert_fcm_data(alert: TrackingAlert, *, subject_name: str, site_name: str) -> dict:
    """FCM data map — all values must be strings."""
    event = build_tracking_alert_event(
        alert,
        subject_name=subject_name,
        site_name=site_name,
    )
    data = {}
    for key, value in event.items():
        if value is None:
            continue
        if isinstance(value, bool):
            data[key] = "true" if value else "false"
        else:
            data[key] = str(value)
    return data


def dispatch_tracking_alert_ws(
    alert: TrackingAlert,
    recipients: Optional[Iterable[User]] = None,
) -> None:
    """Push tracking_alert to site + per-user channel groups (sync wrapper)."""
    channel_layer = get_channel_layer()
    if channel_layer is None:
        logger.warning("[TRACKING_ALERT] No channel layer configured; WS dispatch skipped")
        return

    recipients = _resolve_recipients(alert, recipients)
    subject_name, site_name = _alert_subject_site_names(alert)
    event = build_tracking_alert_event(
        alert,
        subject_name=subject_name,
        site_name=site_name,
    )
    recipient_ids = [str(user.id) for user in recipients]

    async_to_sync(_dispatch_tracking_alert_ws_async)(
        channel_layer,
        event,
        site_id=str(alert.site_id),
        recipient_user_ids=recipient_ids,
    )


def dispatch_tracking_alert_push(
    alert: TrackingAlert,
    recipients: Optional[Iterable[User]] = None,
) -> None:
    """
    FCM push to each recipient's active android/ios device tokens.
    Does not write visitor NotificationLog — inbox remains TrackingAlertRecipient.
    """
    from notifications.services import send_push_to_user

    recipients = _resolve_recipients(alert, recipients)
    if not recipients:
        return

    subject_name, site_name = _alert_subject_site_names(alert)
    title = _tracking_alert_push_title(alert)
    body = alert.message or title
    data = _tracking_alert_fcm_data(
        alert,
        subject_name=subject_name,
        site_name=site_name,
    )

    for user in recipients:
        try:
            ok, detail = send_push_to_user(user, title, body, data)
            if not ok:
                logger.info(
                    "[TRACKING_ALERT] FCM skipped/failed alert=%s user=%s detail=%s",
                    alert.id,
                    getattr(user, "id", None),
                    detail,
                )
        except Exception:
            logger.exception(
                "[TRACKING_ALERT] FCM error alert=%s user=%s",
                alert.id,
                getattr(user, "id", None),
            )


def _schedule_tracking_alert_notify(
    alert: TrackingAlert,
    recipients: Sequence[User],
    *,
    dispatch_ws: bool,
    dispatch_push: bool,
) -> None:
    """Run WS immediately; FCM after DB commit so network work is outside the transaction."""
    recipient_list = list(recipients)
    if dispatch_ws:
        dispatch_tracking_alert_ws(alert, recipient_list)

    if not dispatch_push:
        return

    alert_id = alert.id
    recipient_ids = [user.id for user in recipient_list]

    def _push_after_commit():
        try:
            refreshed = TrackingAlert.objects.select_related(
                "site",
                "subject_user",
            ).get(pk=alert_id)
            users = list(
                User.objects.filter(
                    id__in=recipient_ids,
                    is_active=True,
                    is_deleted=False,
                )
            )
            dispatch_tracking_alert_push(refreshed, users)
        except Exception:
            logger.exception(
                "[TRACKING_ALERT] FCM after-commit failed alert=%s",
                alert_id,
            )

    transaction.on_commit(_push_after_commit)


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
    dispatch_push: bool = True,
) -> Tuple[Optional[TrackingAlert], List[User]]:
    """Create alert row, recipient deliveries, WebSocket + FCM to recipients."""
    if alert_type in ALERT_TYPE_NOTIFY_FIELD and not is_alert_type_enabled_for_site(
        site, alert_type
    ):
        logger.info(
            "[TRACKING_ALERT] Skipped %s — site alert type disabled site=%s",
            alert_type,
            getattr(site, "id", None),
        )
        return None, []

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

    recipients = get_recipients_for_alert(
        site,
        alert_type,
        subject_user=subject_user,
        subject_role_name=alert.subject_role,
    )
    _create_recipient_rows(alert, recipients)

    _schedule_tracking_alert_notify(
        alert,
        recipients,
        dispatch_ws=dispatch_ws,
        dispatch_push=dispatch_push,
    )

    logger.info(
        "[TRACKING_ALERT] Created %s alert=%s site=%s subject=%s role=%s recipients=%s",
        alert_type,
        alert.id,
        site.id,
        subject_user.id,
        alert.subject_role,
        len(recipients),
    )
    return alert, recipients


@transaction.atomic
def resolve_tracking_alert(
    alert: TrackingAlert,
    *,
    dispatch_ws: bool = True,
    dispatch_push: bool = True,
) -> TrackingAlert:
    """Mark alert resolved and notify recipients (WS + FCM)."""
    if not alert.is_active:
        return alert

    alert.is_active = False
    alert.resolved_at = timezone.now()
    alert.save(update_fields=["is_active", "resolved_at"])

    recipients = list(
        User.objects.filter(
            id__in=TrackingAlertRecipient.objects.filter(
                alert=alert,
                channel=TrackingAlertRecipient.Channel.IN_APP,
            ).values_list("user_id", flat=True),
            is_active=True,
            is_deleted=False,
        )
    )
    if not recipients:
        recipients = _resolve_recipients(alert)

    _schedule_tracking_alert_notify(
        alert,
        recipients,
        dispatch_ws=dispatch_ws,
        dispatch_push=dispatch_push,
    )

    logger.info("[TRACKING_ALERT] Resolved alert=%s type=%s", alert.id, alert.alert_type)
    return alert
