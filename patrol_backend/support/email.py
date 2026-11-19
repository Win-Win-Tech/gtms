"""
Support ticket email channel — reuses Django EMAIL_* / DEFAULT_FROM_EMAIL
(same stack as reports/services/email_sender.py). Never raises into APIs.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


def _valid_email(addr: str) -> str | None:
    email = (addr or "").strip()
    if not email:
        return None
    try:
        validate_email(email)
    except ValidationError:
        return None
    return email


def send_support_email(*, to_user, subject: str, body: str) -> bool:
    """
    Send a plain-text support notification email to a user.
    Returns True if send was attempted successfully.
    """
    if not to_user:
        return False
    recipient = _valid_email(getattr(to_user, "email", None) or "")
    if not recipient:
        return False
    try:
        msg = EmailMessage(
            subject=subject or "Support ticket update",
            body=body or "",
            from_email=getattr(settings, "DEFAULT_FROM_EMAIL", None)
            or settings.EMAIL_HOST_USER,
            to=[recipient],
        )
        msg.send(fail_silently=False)
        return True
    except Exception:
        logger.exception(
            "support email failed user=%s subject=%s",
            getattr(to_user, "id", None),
            subject,
        )
        return False


def format_ticket_email_body(ticket, intro: str, extra_lines=None) -> str:
    """Build a short plain-text body with ticket context + SLA due_at when set."""
    lines = [
        "Hello,",
        "",
        intro,
        "",
        f"Ticket: {ticket.ticket_number}",
        f"Subject: {ticket.subject or ''}",
        f"Status: {ticket.status or ''}",
        f"Priority: {ticket.priority or ''}",
    ]
    due_at = getattr(ticket, "due_at", None)
    if due_at:
        try:
            due_s = due_at.isoformat()
        except Exception:
            due_s = str(due_at)
        lines.append(f"Due at: {due_s}")
    for line in extra_lines or []:
        if line is not None and str(line).strip():
            lines.append(str(line))
    lines.extend(["", "Best regards,", "Guard Management System — Support"])
    return "\n".join(lines)
