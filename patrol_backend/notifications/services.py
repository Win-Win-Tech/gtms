"""
FCM push + notification history helpers.

Never raise into visitor APIs — all failures are logged on NotificationLog.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from django.conf import settings
from django.utils import timezone

from .models import DeviceToken, NotificationLog

logger = logging.getLogger(__name__)

_firebase_app = None


def entry_notify_actor(entry):
    """Prefer scanned_by (invite QR), else created_by (manual entry)."""
    return getattr(entry, "scanned_by", None) or getattr(entry, "created_by", None)


def _entry_payload(entry, notif_type: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, str]:
    visitor = getattr(entry, "visitor", None)
    data = {
        "type": notif_type,
        "entry_id": str(entry.id),
        "visitor_name": getattr(visitor, "visitor_name", "") or "",
        "ic_passport_number": getattr(visitor, "ic_passport_number", "") or "",
        "status": entry.status or "",
        "host_id": str(entry.host_id) if entry.host_id else "",
        "location_id": str(entry.location_id) if entry.location_id else "",
        "entry_source": getattr(entry, "entry_source", "") or "",
    }
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            data[str(k)] = str(v)
    return data


def _init_firebase():
    global _firebase_app
    if _firebase_app is not None:
        return _firebase_app

    cred_path = getattr(settings, "FCM_CREDENTIALS_PATH", None) or os.environ.get(
        "FCM_CREDENTIALS_PATH", ""
    )
    if not cred_path or not os.path.isfile(cred_path):
        logger.warning("FCM credentials missing at %s — push send skipped", cred_path)
        return None

    try:
        import firebase_admin
        from firebase_admin import credentials

        if not firebase_admin._apps:
            cred = credentials.Certificate(cred_path)
            _firebase_app = firebase_admin.initialize_app(cred)
        else:
            _firebase_app = firebase_admin.get_app()
        return _firebase_app
    except Exception as exc:
        logger.exception("Firebase init failed: %s", exc)
        return None


def _send_fcm(tokens: list[str], title: str, body: str, data: Dict[str, str]) -> tuple[bool, str]:
    if not tokens:
        return False, "no_tokens"
    app = _init_firebase()
    if app is None:
        return False, "firebase_not_configured"
    try:
        from firebase_admin import messaging

        # FCM data values must be strings
        str_data = {str(k): str(v) for k, v in (data or {}).items()}
        message = messaging.MulticastMessage(
            tokens=tokens,
            notification=messaging.Notification(title=title, body=body),
            data=str_data,
            android=messaging.AndroidConfig(priority="high"),
            apns=messaging.APNSConfig(
                payload=messaging.APNSPayload(
                    aps=messaging.Aps(sound="default", content_available=True)
                )
            ),
        )
        result = messaging.send_each_for_multicast(message)
        # Deactivate invalid tokens
        for idx, resp in enumerate(result.responses):
            if resp.success:
                continue
            err = str(getattr(resp, "exception", "") or "")
            if "NotRegistered" in err or "InvalidArgument" in err or "UNREGISTERED" in err:
                DeviceToken.objects.filter(token=tokens[idx]).update(is_active=False)
        if result.success_count == 0 and result.failure_count > 0:
            return False, f"all_failed:{result.failure_count}"
        return True, f"sent={result.success_count} failed={result.failure_count}"
    except Exception as exc:
        logger.exception("FCM send failed: %s", exc)
        return False, str(exc)


def notify_user(
    user,
    notif_type: str,
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
    entry=None,
) -> Optional[NotificationLog]:
    """
    Create history row + attempt FCM to android/ios tokens.
    Returns NotificationLog or None if user is missing.
    """
    if not user:
        return None

    payload = dict(data or {})
    payload.setdefault("type", notif_type)

    log = NotificationLog.objects.create(
        user=user,
        type=notif_type,
        title=title,
        body=body or "",
        data=payload,
        related_entry=entry,
        channel=NotificationLog.CHANNEL_PUSH,
        delivery_status=NotificationLog.STATUS_PENDING,
    )

    tokens = list(
        DeviceToken.objects.filter(
            user=user,
            is_active=True,
            device_type__in=[DeviceToken.DEVICE_ANDROID, DeviceToken.DEVICE_IOS],
        ).values_list("token", flat=True)
    )

    if not tokens:
        log.delivery_status = NotificationLog.STATUS_SKIPPED
        log.error_message = "no_active_mobile_tokens"
        log.sent_at = timezone.now()
        log.save(update_fields=["delivery_status", "error_message", "sent_at"])
        return log

    ok, detail = _send_fcm(tokens, title, body, {k: str(v) for k, v in payload.items()})
    log.sent_at = timezone.now()
    if ok:
        log.delivery_status = NotificationLog.STATUS_SENT
        log.error_message = detail if "failed=" in detail else ""
    else:
        log.delivery_status = NotificationLog.STATUS_FAILED
        log.error_message = detail[:2000]
    log.save(update_fields=["delivery_status", "error_message", "sent_at"])
    return log


def notify_visitor_pending(entry) -> None:
    """Notify host that an entry needs approval."""
    try:
        host = entry.host
        if not host:
            return
        visitor_name = getattr(entry.visitor, "visitor_name", "Visitor")
        title = "Visitor approval needed"
        body = f"{visitor_name} is waiting for your approval."
        notify_user(
            host,
            NotificationLog.TYPE_VISITOR_PENDING,
            title,
            body,
            data=_entry_payload(entry, NotificationLog.TYPE_VISITOR_PENDING),
            entry=entry,
        )
    except Exception:
        logger.exception("notify_visitor_pending failed entry=%s", getattr(entry, "id", None))


def notify_visitor_host_action(entry, notif_type: str, title: str, body: str, extra=None) -> None:
    """Notify created_by / scanned_by after host approve/cancel/revert/reschedule."""
    try:
        actor = entry_notify_actor(entry)
        if not actor:
            return
        # Dedupe if host acted on their own entry
        if entry.host_id and str(entry.host_id) == str(actor.id):
            return
        notify_user(
            actor,
            notif_type,
            title,
            body,
            data=_entry_payload(entry, notif_type, extra),
            entry=entry,
        )
    except Exception:
        logger.exception(
            "notify_visitor_host_action failed type=%s entry=%s",
            notif_type,
            getattr(entry, "id", None),
        )
