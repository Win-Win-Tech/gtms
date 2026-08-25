"""Timezone / schedule matching helpers for org report emails."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from types import SimpleNamespace

import pytz
from django.utils import timezone as django_timezone

from authapp.models import User
from reports.models import LocationReportEmailItem as Item


def get_location_timezone(location):
    """Resolve org timezone from location admin user (fallback Asia/Kolkata)."""
    admin = (
        User.objects.filter(
            location=location,
            role="admin",
            is_deleted=False,
            is_active=True,
        )
        .exclude(timezone__isnull=True)
        .exclude(timezone="")
        .first()
    )
    if admin and admin.timezone:
        try:
            return pytz.timezone(admin.timezone)
        except Exception:
            pass

    any_user = (
        User.objects.filter(location=location, is_deleted=False)
        .exclude(timezone__isnull=True)
        .exclude(timezone="")
        .first()
    )
    if any_user and any_user.timezone:
        try:
            return pytz.timezone(any_user.timezone)
        except Exception:
            pass

    return pytz.timezone("Asia/Kolkata")


def get_location_admin_user(location):
    return (
        User.objects.filter(
            location=location,
            role="admin",
            is_deleted=False,
            is_active=True,
        ).first()
        or User.objects.filter(location=location, is_deleted=False, is_active=True).first()
    )


def local_now(location_tz):
    return django_timezone.now().astimezone(location_tz)


def send_time_matches(local_dt, send_time: time, window_minutes: int = 15) -> bool:
    """True if local clock is in the same window_minutes slot as send_time."""
    if not send_time:
        return False
    send_mins = send_time.hour * 60 + send_time.minute
    now_mins = local_dt.hour * 60 + local_dt.minute
    send_slot = send_mins // window_minutes
    now_slot = now_mins // window_minutes
    return now_slot == send_slot


def schedule_matches_today(schedule_type: str, local_dt: datetime) -> bool:
    if schedule_type == Item.SCHEDULE_DAILY:
        return True
    if schedule_type == Item.SCHEDULE_WEEKLY_SUNDAY:
        return local_dt.weekday() == 6  # Sunday
    if schedule_type == Item.SCHEDULE_MONTHLY_START:
        return local_dt.day == 1
    return False


def build_schedule_key(local_date: date, schedule_type: str) -> str:
    return f"{local_date.isoformat()}-{schedule_type}"


def resolve_period(schedule_type: str, daily_period: str, local_dt: datetime):
    """
    Return dict describing the data window for a report.
    """
    today = local_dt.date()

    if schedule_type == Item.SCHEDULE_DAILY:
        if daily_period == Item.PERIOD_TODAY:
            target = today
            label = f"Today ({target.isoformat()})"
        else:
            target = today - timedelta(days=1)
            label = f"Previous day ({target.isoformat()})"
        return {
            "kind": "day",
            "start_date": target,
            "end_date": target,
            "month_str": None,
            "year": target.year,
            "month": target.month,
            "label": label,
            "filter_type": "custom",
            "date_filter": "custom",
        }

    if schedule_type == Item.SCHEDULE_WEEKLY_SUNDAY:
        # On Sunday, previous week = last Mon .. last Sun (yesterday).
        end = today - timedelta(days=1)
        start = end - timedelta(days=6)
        return {
            "kind": "range",
            "start_date": start,
            "end_date": end,
            "month_str": None,
            "year": None,
            "month": None,
            "label": f"Week {start.isoformat()} to {end.isoformat()}",
            "filter_type": "custom",
            "date_filter": "custom",
        }

    if schedule_type == Item.SCHEDULE_MONTHLY_START:
        first_this = today.replace(day=1)
        last_prev = first_this - timedelta(days=1)
        start = last_prev.replace(day=1)
        end = last_prev
        return {
            "kind": "month",
            "start_date": start,
            "end_date": end,
            "month_str": f"{start.year}-{start.month:02d}",
            "year": start.year,
            "month": start.month,
            "label": start.strftime("%B %Y"),
            "filter_type": "custom",
            "date_filter": "custom",
        }

    raise ValueError(f"Unknown schedule_type: {schedule_type}")


def make_fake_request(user, query_params=None):
    """Minimal request-like object for export helpers that expect request.query_params / user."""
    params = query_params or {}
    return SimpleNamespace(
        user=user,
        query_params=params,
        data={},
        GET=params,
    )
