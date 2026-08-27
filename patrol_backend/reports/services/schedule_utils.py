"""Timezone / schedule matching helpers for org report emails."""

from __future__ import annotations

import calendar
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
    # Snap configured time to the cron window so :37 still maps to the :30 slot.
    send_mins = send_time.hour * 60 + send_time.minute
    send_mins = (send_mins // window_minutes) * window_minutes
    now_mins = local_dt.hour * 60 + local_dt.minute
    send_slot = send_mins // window_minutes
    now_slot = now_mins // window_minutes
    return now_slot == send_slot


def snap_send_time(send_time: time, window_minutes: int = 15) -> time:
    """Snap a clock time down to the nearest window boundary (e.g. 11:37 → 11:30)."""
    if not send_time:
        return time(8, 0)
    total = send_time.hour * 60 + send_time.minute
    snapped = (total // window_minutes) * window_minutes
    return time(hour=snapped // 60, minute=snapped % 60)


def day_of_month_matches(local_date: date, days_list) -> bool:
    """
    True if local_date.day matches any configured day-of-month.

    Days greater than the length of the month are clamped to the last day
    (e.g. configured 30 in February matches Feb 28/29).
    """
    if not days_list:
        return False
    last_day = calendar.monthrange(local_date.year, local_date.month)[1]
    for raw in days_list:
        try:
            configured = int(raw)
        except (TypeError, ValueError):
            continue
        if configured < 1:
            continue
        if local_date.day == min(configured, last_day):
            return True
    return False


def _item_attr(item, name, default=None):
    if item is None:
        return default
    return getattr(item, name, default)


def schedule_matches_today(schedule_type: str, local_dt: datetime, item=None) -> bool:
    st = Item.normalize_schedule_type(schedule_type)
    today = local_dt.date()

    if st == Item.SCHEDULE_DAILY:
        return True

    if st == Item.SCHEDULE_WEEKLY:
        raw_weekday = _item_attr(item, "weekly_weekday", 6)
        try:
            weekday = int(6 if raw_weekday is None else raw_weekday)
        except (TypeError, ValueError):
            weekday = 6
        if weekday < 0 or weekday > 6:
            weekday = 6
        return local_dt.weekday() == weekday

    if st == Item.SCHEDULE_MONTHLY:
        days = _item_attr(item, "monthly_send_days", None)
        if not days:
            days = [1]
        return day_of_month_matches(today, days)

    if st == Item.SCHEDULE_LAST_N_DAYS:
        days = _item_attr(item, "last_n_days_send_days", None)
        if not days:
            days = [7, 14, 21, 28]
        return day_of_month_matches(today, days)

    return False


def matching_schedule_types(schedule_types, local_dt: datetime, item=None) -> list:
    """Return which of the configured schedule types match today."""
    types = schedule_types or []
    if isinstance(types, str):
        types = [types]
    matched = []
    for t in types:
        nt = Item.normalize_schedule_type(t)
        if nt in matched:
            continue
        if schedule_matches_today(nt, local_dt, item):
            matched.append(nt)
    return matched


def build_schedule_key(local_date: date, schedule_type: str) -> str:
    return f"{local_date.isoformat()}-{Item.normalize_schedule_type(schedule_type)}"


def resolve_period(schedule_type: str, daily_period: str, local_dt: datetime, item=None):
    """
    Return dict describing the data window for a report.
    """
    today = local_dt.date()
    st = Item.normalize_schedule_type(schedule_type)

    if st == Item.SCHEDULE_DAILY:
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

    if st == Item.SCHEDULE_WEEKLY:
        include = bool(_item_attr(item, "weekly_include_current_day", False))
        end = today if include else today - timedelta(days=1)
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

    if st == Item.SCHEDULE_MONTHLY:
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

    if st == Item.SCHEDULE_LAST_N_DAYS:
        raw_n = _item_attr(item, "last_n_days_count", 7)
        try:
            n = int(7 if raw_n is None else raw_n)
        except (TypeError, ValueError):
            n = 7
        n = max(1, min(90, n))
        include = bool(_item_attr(item, "last_n_days_include_current_day", False))
        end = today if include else today - timedelta(days=1)
        start = end - timedelta(days=n - 1)
        return {
            "kind": "range",
            "start_date": start,
            "end_date": end,
            "month_str": None,
            "year": None,
            "month": None,
            "label": f"Last {n} days {start.isoformat()} to {end.isoformat()}",
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
