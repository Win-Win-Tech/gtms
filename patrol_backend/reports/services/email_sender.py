"""Send org report emails with PDF/Excel attachments."""

from __future__ import annotations

import logging
import re

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)

# Split on comma / semicolon / whitespace / newlines — chips and pasted lists
_SPLIT_RE = re.compile(r"[,;\s]+")


def parse_recipients(raw) -> list[str]:
    """Parse and validate one or many recipient emails from str or list."""
    if isinstance(raw, (list, tuple)):
        parts = []
        for item in raw:
            parts.extend(_SPLIT_RE.split(str(item or "")))
    else:
        parts = _SPLIT_RE.split(str(raw or ""))

    emails = []
    seen = set()
    for part in parts:
        email = part.strip()
        if not email:
            continue
        try:
            validate_email(email)
        except ValidationError:
            logger.warning("Skipping invalid report email recipient: %s", email)
            continue
        key = email.lower()
        if key in seen:
            continue
        seen.add(key)
        emails.append(email)
    return emails


def send_report_email(location, subject, body_lines, attachments, recipients):
    """
    Send one email with binary attachments to all recipients.
    attachments: list of {filename, content, mime, display_name, row_count}
    """
    to = parse_recipients(recipients)
    if not to:
        logger.warning("No valid recipients for location %s", location.name)
        return {"sent": False, "attachments_count": 0, "reason": "no_recipients"}

    valid = [a for a in (attachments or []) if a.get("content") and a.get("filename")]
    if not valid:
        return {"sent": False, "attachments_count": 0, "reason": "no_attachments"}

    body = "\n".join(
        [
            "Hello,",
            "",
            f"Please find the automatic reports for {location.name}.",
            f"Address: {location.address or 'N/A'}",
            "",
            "Attachments:",
        ]
        + [
            f"  - {a.get('display_name') or a['filename']}"
            + (f" ({a.get('row_count')} records)" if a.get("row_count") is not None else "")
            for a in valid
        ]
        + list(body_lines or [])
        + ["", "Best regards,", "Guard Management System"]
    )

    # Primary To + remaining as BCC — some SMTP providers mishandle multi-To
    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=[to[0]],
        bcc=to[1:] if len(to) > 1 else None,
    )
    for att in valid:
        email.attach(att["filename"], att["content"], att.get("mime") or "application/octet-stream")

    logger.info(
        "Sending report email for %s to %s recipient(s) with %s attachment(s)",
        location.name,
        len(to),
        len(valid),
    )
    email.send(fail_silently=False)
    return {"sent": True, "attachments_count": len(valid), "recipients": to}
