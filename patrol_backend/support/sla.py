"""
Support ticket SLA / due-date helpers (Phase 7).

Defaults are hours from creation by priority; override via settings.SUPPORT_SLA_HOURS
or an explicit due_at on create.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Optional

from django.conf import settings
from django.utils import timezone

from .models import SupportTicket

# Practical v1 defaults (wall-clock hours from created_on / now).
DEFAULT_SLA_HOURS = {
    SupportTicket.Priority.CRITICAL: 4,
    SupportTicket.Priority.HIGH: 8,
    SupportTicket.Priority.MEDIUM: 24,
    SupportTicket.Priority.LOW: 72,
}

ACTIVE_SLA_STATUSES = (
    SupportTicket.Status.OPEN,
    SupportTicket.Status.IN_PROGRESS,
)


def sla_hours_for_priority(priority: str) -> int:
    configured = getattr(settings, "SUPPORT_SLA_HOURS", None) or {}
    key = (priority or SupportTicket.Priority.MEDIUM).upper()
    if key in configured:
        try:
            return max(1, int(configured[key]))
        except (TypeError, ValueError):
            pass
    return DEFAULT_SLA_HOURS.get(key, DEFAULT_SLA_HOURS[SupportTicket.Priority.MEDIUM])


def compute_due_at(*, priority: str, from_dt=None):
    base = from_dt or timezone.now()
    return base + timedelta(hours=sla_hours_for_priority(priority))


def ticket_is_overdue(ticket, *, now=None) -> bool:
    """Active tickets past due_at are overdue; terminal statuses are not."""
    if not ticket or not getattr(ticket, "due_at", None):
        return False
    if ticket.status not in ACTIVE_SLA_STATUSES:
        return False
    ref = now or timezone.now()
    return ticket.due_at < ref


def ticket_is_approaching_due(ticket, *, now=None, within_hours: Optional[float] = None) -> bool:
    """
    True when due_at is in the future but within the warning window.
    Default window: min(2h, 25% of SLA hours for priority).
    """
    if not ticket or not getattr(ticket, "due_at", None):
        return False
    if ticket.status not in ACTIVE_SLA_STATUSES:
        return False
    ref = now or timezone.now()
    if ticket.due_at <= ref:
        return False
    if within_hours is None:
        hours = sla_hours_for_priority(ticket.priority)
        within_hours = min(2.0, max(0.5, hours * 0.25))
    return ticket.due_at <= ref + timedelta(hours=within_hours)
