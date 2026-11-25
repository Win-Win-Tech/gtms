"""
Support-ticket notifications via the shared notifications app (NotificationLog + FCM)
plus email for key events (create, assign, close, reopen, SLA).

Never raise into ticket APIs — failures are logged only.
"""

from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.db import transaction
from django.utils import timezone

logger = logging.getLogger(__name__)

User = get_user_model()

# NotificationLog.type values (also registered on NotificationLog.TYPE_CHOICES).
TYPE_TICKET_CREATED = "support_ticket_created"
TYPE_TICKET_ASSIGNED = "support_ticket_assigned"
TYPE_TICKET_RESOLVED = "support_ticket_resolved"
TYPE_TICKET_CLOSED = "support_ticket_closed"
TYPE_TICKET_INVALIDATED = "support_ticket_invalidated"
TYPE_TICKET_REFERRED_BACK = "support_ticket_referred_back"
TYPE_TICKET_COMMENT = "support_ticket_comment"
TYPE_TICKET_ATTACHMENT = "support_ticket_attachment"
TYPE_TICKET_REOPENED = "support_ticket_reopened"
TYPE_TICKET_SLA_WARNING = "support_ticket_sla_warning"
TYPE_TICKET_SLA_BREACH = "support_ticket_sla_breach"

# Events that also send email (in addition to NotificationLog / FCM).
EMAIL_EVENTS = {
    TYPE_TICKET_CREATED,
    TYPE_TICKET_ASSIGNED,
    TYPE_TICKET_CLOSED,
    TYPE_TICKET_REOPENED,
    TYPE_TICKET_SLA_WARNING,
    TYPE_TICKET_SLA_BREACH,
}

# Mobile deep-link keys (SMART PATROL Flutter / FCM data payload).
MOBILE_CLICK_ACTION = "FLUTTER_NOTIFICATION_CLICK"
MOBILE_SCREEN_HELP = "help_support"
MOBILE_SCREEN_TICKET_DETAIL = "support_ticket_detail"
MOBILE_SCREEN_MY_TICKETS = "support_my_tickets"


def _ticket_payload(ticket, notif_type: str, extra=None) -> dict:
    """
    FCM / NotificationLog.data for support events.

    Mobile opens ticket detail when `ticket_id` is present; otherwise Help & Support hub.
    Always includes Flutter click_action for background notification handling.
    """
    data = {
        "type": notif_type,
        "ticket_id": str(ticket.id),
        "ticket_number": ticket.ticket_number or "",
        "subject": (ticket.subject or "")[:200],
        "status": ticket.status or "",
        "priority": ticket.priority or "",
        "click_action": MOBILE_CLICK_ACTION,
        "screen": MOBILE_SCREEN_TICKET_DETAIL,
        "route": f"/help-support/tickets/{ticket.id}",
    }
    due_at = getattr(ticket, "due_at", None)
    if due_at:
        try:
            data["due_at"] = due_at.isoformat()
        except Exception:
            data["due_at"] = str(due_at)
    if extra:
        for k, v in extra.items():
            if v is None:
                continue
            data[str(k)] = str(v)
    return data


def _safe_email(user, notif_type: str, title: str, body: str, ticket) -> None:
    if notif_type not in EMAIL_EVENTS:
        return
    try:
        from .email import format_ticket_email_body, send_support_email

        email_body = format_ticket_email_body(ticket, intro=body or title)
        send_support_email(to_user=user, subject=title, body=email_body)
    except Exception:
        logger.exception(
            "support email notify failed type=%s ticket=%s user=%s",
            notif_type,
            getattr(ticket, "id", None),
            getattr(user, "id", None),
        )


def _safe_notify(user, notif_type: str, title: str, body: str, ticket, extra=None) -> None:
    if not user:
        return
    try:
        from notifications.services import notify_user

        notify_user(
            user,
            notif_type,
            title,
            body,
            data=_ticket_payload(ticket, notif_type, extra),
        )
    except Exception:
        logger.exception(
            "support notify failed type=%s ticket=%s user=%s",
            notif_type,
            getattr(ticket, "id", None),
            getattr(user, "id", None),
        )
    _safe_email(user, notif_type, title, body, ticket)


def notify_ticket_created(ticket) -> int:
    """Notify active SUPPORT agents that a new ticket is in the queue."""
    agents = User.objects.filter(
        role__iexact="support", is_active=True, is_deleted=False
    ).exclude(pk=ticket.created_by_id)
    title = f"New support ticket {ticket.ticket_number}"
    body = ticket.subject or "A new support ticket was raised."
    count = 0
    for agent in agents:
        _safe_notify(agent, TYPE_TICKET_CREATED, title, body, ticket)
        count += 1
    return count


def _active_support_agents(*, exclude_ids=None):
    qs = User.objects.filter(role__iexact="support", is_active=True, is_deleted=False)
    exclude = {str(x) for x in (exclude_ids or []) if x}
    if exclude:
        qs = qs.exclude(pk__in=list(exclude))
    return qs


def _recipient_ids_seen():
    return set()


def notify_ticket_assigned(ticket, *, actor=None) -> int:
    """
    Phase 6: notify ticket creator AND SUPPORT agents on pickup/assign/transfer.

    Skips the acting agent to avoid self-notify. Writes NotificationLog rows + email.
    """
    assignee = ticket.current_assignee
    assignee_name = ""
    if assignee:
        assignee_name = assignee.name or assignee.email or "Support"
    title = f"Ticket {ticket.ticket_number} assigned"
    creator_body = (
        f"Your ticket is now with {assignee_name}."
        if assignee_name
        else "Your ticket was assigned."
    )
    support_body = (
        f"{ticket.ticket_number} assigned to {assignee_name}."
        if assignee_name
        else f"{ticket.ticket_number} was assigned."
    )
    extra = {"assignee_id": str(assignee.id) if assignee else ""}
    seen = _recipient_ids_seen()
    count = 0
    actor_id = str(getattr(actor, "id", "") or "")

    if ticket.created_by_id and str(ticket.created_by_id) != actor_id:
        _safe_notify(
            ticket.created_by,
            TYPE_TICKET_ASSIGNED,
            title,
            creator_body,
            ticket,
            extra=extra,
        )
        seen.add(str(ticket.created_by_id))
        count += 1

    for agent in _active_support_agents(exclude_ids=[actor_id] if actor_id else None):
        aid = str(agent.id)
        if aid in seen:
            continue
        _safe_notify(agent, TYPE_TICKET_ASSIGNED, title, support_body, ticket, extra=extra)
        seen.add(aid)
        count += 1
    return count


def notify_ticket_resolved(ticket) -> None:
    title = f"Ticket {ticket.ticket_number} resolved"
    summary = (ticket.resolution_summary or "").strip()
    body = summary[:400] if summary else "Your support ticket has been resolved."
    _safe_notify(ticket.created_by, TYPE_TICKET_RESOLVED, title, body, ticket)


def notify_ticket_closed(ticket, *, actor=None) -> int:
    """
    Phase 6: notify ticket creator AND SUPPORT agents on close.

    Skips the acting agent. Writes NotificationLog rows + email.
    """
    title = f"Ticket {ticket.ticket_number} closed"
    creator_body = "Your support ticket has been closed."
    support_body = f"{ticket.ticket_number} has been closed."
    seen = _recipient_ids_seen()
    count = 0
    actor_id = str(getattr(actor, "id", "") or "")

    if ticket.created_by_id and str(ticket.created_by_id) != actor_id:
        _safe_notify(
            ticket.created_by, TYPE_TICKET_CLOSED, title, creator_body, ticket
        )
        seen.add(str(ticket.created_by_id))
        count += 1

    for agent in _active_support_agents(exclude_ids=[actor_id] if actor_id else None):
        aid = str(agent.id)
        if aid in seen:
            continue
        _safe_notify(agent, TYPE_TICKET_CLOSED, title, support_body, ticket)
        seen.add(aid)
        count += 1
    return count


def notify_ticket_referred_back(ticket) -> None:
    """Notify the requester. No fan-out to other support agents."""
    title = f"Ticket {ticket.ticket_number} referred back"
    reason = (
        getattr(ticket, "referred_back_reason", None) or ticket.invalidation_reason or ""
    ).strip()
    body = reason[:400] if reason else "Your support ticket was referred back."
    _safe_notify(ticket.created_by, TYPE_TICKET_REFERRED_BACK, title, body, ticket)


def notify_ticket_invalidated(ticket) -> None:
    """Deprecated alias of notify_ticket_referred_back."""
    notify_ticket_referred_back(ticket)


def notify_ticket_public_comment(ticket, *, author, comment_preview: str) -> None:
    """Notify the other party on a PUBLIC comment (creator ↔ assignee)."""
    preview = (comment_preview or "").strip()[:200]
    title = f"New comment on {ticket.ticket_number}"
    body = preview or "A new comment was added to your ticket."
    recipients = []
    if ticket.created_by_id and str(ticket.created_by_id) != str(author.id):
        recipients.append(ticket.created_by)
    if (
        ticket.current_assignee_id
        and str(ticket.current_assignee_id) != str(author.id)
        and str(ticket.current_assignee_id) != str(ticket.created_by_id or "")
    ):
        recipients.append(ticket.current_assignee)
    for user in recipients:
        _safe_notify(
            user,
            TYPE_TICKET_COMMENT,
            title,
            body,
            ticket,
            extra={"author_id": str(author.id)},
        )


def notify_ticket_reopened(ticket, *, actor, comment: str = "") -> None:
    """
    Notify on reopen:
    - Creator reopen → assignee (or all SUPPORT if unassigned)
    - SUPPORT reopen → creator + other SUPPORT agents (skip actor)
    """
    title = f"Ticket {ticket.ticket_number} reopened"
    body = (comment or "").strip()[:400] or "This support ticket was reopened."
    actor_id = str(getattr(actor, "id", "") or "")
    seen = _recipient_ids_seen()
    recipients = []

    if ticket.created_by_id and str(ticket.created_by_id) != actor_id:
        recipients.append(ticket.created_by)
        seen.add(str(ticket.created_by_id))

    if ticket.current_assignee_id and str(ticket.current_assignee_id) != actor_id:
        aid = str(ticket.current_assignee_id)
        if aid not in seen:
            recipients.append(ticket.current_assignee)
            seen.add(aid)

    # Always fan out to SUPPORT when unassigned or SUPPORT performed the reopen.
    from .permissions import is_support_user

    if is_support_user(actor) or not ticket.current_assignee_id:
        for agent in _active_support_agents(exclude_ids=[actor_id] if actor_id else None):
            aid = str(agent.id)
            if aid in seen:
                continue
            recipients.append(agent)
            seen.add(aid)

    for user in recipients:
        _safe_notify(
            user,
            TYPE_TICKET_REOPENED,
            title,
            body,
            ticket,
            extra={"reopened_by": actor_id},
        )


def notify_ticket_attachment(ticket, *, uploader, file_name: str) -> None:
    """Notify the other party when an attachment is added."""
    title = f"Attachment on {ticket.ticket_number}"
    body = f"File uploaded: {file_name}" if file_name else "A file was attached to your ticket."
    targets = []
    if ticket.created_by_id and str(ticket.created_by_id) != str(uploader.id):
        targets.append(ticket.created_by)
    if (
        ticket.current_assignee_id
        and str(ticket.current_assignee_id) != str(uploader.id)
        and str(ticket.current_assignee_id) != str(ticket.created_by_id or "")
    ):
        targets.append(ticket.current_assignee)
    for user in targets:
        _safe_notify(
            user,
            TYPE_TICKET_ATTACHMENT,
            title,
            body,
            ticket,
            extra={"file_name": file_name or ""},
        )


def maybe_notify_sla_for_queryset(qs, *, limit=25) -> int:
    """
    Best-effort SLA approaching/breach notifications for SUPPORT agents.
    Marks sla_warning_notified_at / sla_breach_notified_at so each fires once.
    """
    from .sla import ticket_is_approaching_due, ticket_is_overdue

    notified = 0
    now = timezone.now()
    tickets = list(
        qs.select_related("created_by", "current_assignee")
        .order_by("due_at")[: max(1, int(limit))]
    )
    for ticket in tickets:
        try:
            if ticket_is_overdue(ticket, now=now) and not ticket.sla_breach_notified_at:
                title = f"SLA breached: {ticket.ticket_number}"
                body = (
                    f"{ticket.ticket_number} is overdue"
                    + (f" (due {ticket.due_at.isoformat()})." if ticket.due_at else ".")
                )
                for agent in _active_support_agents():
                    _safe_notify(
                        agent,
                        TYPE_TICKET_SLA_BREACH,
                        title,
                        body,
                        ticket,
                        extra={"sla": "breach"},
                    )
                with transaction.atomic():
                    type(ticket).objects.filter(pk=ticket.pk, sla_breach_notified_at__isnull=True).update(
                        sla_breach_notified_at=now
                    )
                notified += 1
            elif (
                ticket_is_approaching_due(ticket, now=now)
                and not ticket.sla_warning_notified_at
                and not ticket.sla_breach_notified_at
            ):
                title = f"SLA due soon: {ticket.ticket_number}"
                body = (
                    f"{ticket.ticket_number} is approaching its due time"
                    + (f" ({ticket.due_at.isoformat()})." if ticket.due_at else ".")
                )
                for agent in _active_support_agents():
                    _safe_notify(
                        agent,
                        TYPE_TICKET_SLA_WARNING,
                        title,
                        body,
                        ticket,
                        extra={"sla": "warning"},
                    )
                with transaction.atomic():
                    type(ticket).objects.filter(
                        pk=ticket.pk, sla_warning_notified_at__isnull=True
                    ).update(sla_warning_notified_at=now)
                notified += 1
        except Exception:
            logger.exception(
                "support SLA notify failed ticket=%s", getattr(ticket, "id", None)
            )
    return notified
