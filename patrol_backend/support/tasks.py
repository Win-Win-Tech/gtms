"""Celery tasks for support tickets."""

import logging

from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name="support.tasks.auto_close_resolved_and_referred_tickets")
def auto_close_resolved_and_referred_tickets():
    """
    Close tickets that have been RESOLVED or REFERRED_BACK for 24 hours.

    status_entered_at is the clock, so a reopen that later returns to those
    statuses starts a new 24h window. No notification is sent.
    """
    from .services import auto_close_stale_tickets

    closed = auto_close_stale_tickets()
    if closed:
        logger.info("Auto-closed %s support ticket(s)", closed)
    return closed
