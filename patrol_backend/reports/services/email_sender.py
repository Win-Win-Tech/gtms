"""Send org report emails with PDF/Excel attachments."""

from __future__ import annotations

import logging

from django.conf import settings
from django.core.mail import EmailMessage
from django.core.validators import validate_email
from django.core.exceptions import ValidationError

logger = logging.getLogger(__name__)


def parse_recipients(raw: str) -> list[str]:
    parts = [p.strip() for p in (raw or "").replace(";", ",").split(",")]
    emails = []
    for part in parts:
        if not part:
            continue
        try:
            validate_email(part)
            emails.append(part)
        except ValidationError:
            logger.warning("Skipping invalid report email recipient: %s", part)
    return emails


def send_report_email(location, subject, body_lines, attachments, recipients):
    """
    Send one email with binary attachments.
    attachments: list of {filename, content, mime, display_name, row_count}
    """
    to = parse_recipients(",".join(recipients) if isinstance(recipients, list) else (recipients or ""))
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
        + body_lines
        + ["", "Best regards,", "Guard Management System"]
    )

    email = EmailMessage(
        subject=subject,
        body=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        to=to,
    )
    for att in valid:
        email.attach(att["filename"], att["content"], att.get("mime") or "application/octet-stream")

    email.send(fail_silently=False)
    return {"sent": True, "attachments_count": len(valid), "recipients": to}
