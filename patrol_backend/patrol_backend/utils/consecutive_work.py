"""
Labour-law limits: consecutive working days and monthly hours.

One master switch (`labour_law_limit_enabled`) turns both on.
A day is *worked* only when the person actually checked in:
  P, LD (present, less duration), OW, M, half-day codes, or a check-in with no pa yet.
A day is *rest* when there is no check-in, week-off, no row, or explicit Absent (A).
"""
from __future__ import annotations

from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Optional, Sequence, Set, Tuple

from django.db.models import Q, Sum, Value
from django.db.models.functions import Coalesce

SETTING_ENABLED_KEY = "labour_law_limit_enabled"
SETTING_ENABLED_KEY_LEGACY = "consecutive_work_limit_enabled"
SETTING_MAX_DAYS_KEY = "max_consecutive_work_days"
SETTING_MAX_HOURS_KEY = "max_monthly_work_hours"
DEFAULT_ENABLED = False
DEFAULT_MAX_DAYS = 6
DEFAULT_MAX_HOURS = 208
MAX_DAYS_CAP = 31
MAX_HOURS_CAP = 744

# Present-like PA marks. LD is present with less duration. A is rest (incl. future celery absent).
WORKED_PA_STATUSES = frozenset({"P", "LD", "OW", "M", "HD", "HP", "H"})
REST_PA_STATUSES = frozenset({"A"})


class ConsecutiveWorkLimitExceeded(Exception):
    REASON_WORKED = "worked"
    REASON_ASSIGNMENT = "assignment"

    def __init__(
        self,
        *,
        consecutive: int,
        max_days: int,
        blocked_date: Optional[date] = None,
        reason: str = "worked",
    ):
        self.consecutive = consecutive
        self.max_days = max_days
        self.blocked_date = blocked_date
        self.reason = reason or self.REASON_WORKED
        if self.reason == self.REASON_ASSIGNMENT:
            when = f" on or before {blocked_date.isoformat()}" if blocked_date else ""
            self.message = (
                f"Cannot assign {consecutive} consecutive days. "
                f"Maximum is {max_days}. A rest day is required{when}."
            )
        else:
            self.message = (
                f"This person has already worked {max_days} consecutive days. "
                "A rest day is required before the next shift."
            )
        super().__init__(self.message)

    def as_dict(self) -> dict:
        payload = {
            "code": "consecutive_work_limit",
            "error": self.message,
            "message": self.message,
            "reason": self.reason,
            "consecutive_days": self.consecutive,
            "max_consecutive_work_days": self.max_days,
        }
        if self.reason == self.REASON_WORKED:
            payload["consecutive_present_days"] = self.consecutive
        if self.blocked_date:
            payload["blocked_date"] = self.blocked_date.isoformat()
        return payload

    def as_serializer_error(self) -> dict:
        """Single field so DRF does not dump code/days into the bulk UI."""
        return {"error": self.message}


class MonthlyHourLimitExceeded(Exception):
    def __init__(
        self,
        *,
        worked_minutes: int,
        extra_minutes: int = 0,
        max_hours: int,
        blocked_date: Optional[date] = None,
    ):
        self.worked_minutes = int(worked_minutes or 0)
        self.extra_minutes = int(extra_minutes or 0)
        self.max_hours = max_hours
        self.blocked_date = blocked_date
        worked_hours = round(self.worked_minutes / 60.0, 1)
        self.message = (
            f"Monthly limit of {max_hours} hours reached. "
            f"This person has already worked {worked_hours:g} hours this month."
        )
        super().__init__(self.message)

    def as_dict(self) -> dict:
        payload = {
            "code": "monthly_hour_limit",
            "error": self.message,
            "message": self.message,
            "worked_hours": round(self.worked_minutes / 60.0, 2),
            "max_monthly_work_hours": self.max_hours,
        }
        if self.blocked_date:
            payload["blocked_date"] = self.blocked_date.isoformat()
        return payload

    def as_serializer_error(self) -> dict:
        return {"error": self.message}


def _parse_bool(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_max_days(raw, default: int = DEFAULT_MAX_DAYS) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        return 0
    return min(value, MAX_DAYS_CAP)


def _parse_max_hours(raw, default: int = DEFAULT_MAX_HOURS) -> int:
    try:
        value = int(float(raw))
    except (TypeError, ValueError):
        value = default
    if value <= 0:
        return 0
    return min(value, MAX_HOURS_CAP)


def shift_length_minutes(shift) -> int:
    if not shift or not getattr(shift, "start_time", None) or not getattr(shift, "end_time", None):
        return 0
    base = date(2000, 1, 1)
    start_dt = datetime.combine(base, shift.start_time)
    if shift.end_time <= shift.start_time:
        end_dt = datetime.combine(base + timedelta(days=1), shift.end_time)
    else:
        end_dt = datetime.combine(base, shift.end_time)
    return max(0, int((end_dt - start_dt).total_seconds() // 60))


def is_worked_pa_status(pa_status) -> bool:
    if pa_status in REST_PA_STATUSES:
        return False
    if pa_status in WORKED_PA_STATUSES or pa_status in (None, ""):
        return True
    return False


def consecutive_present_count(
    worked_dates: Set[date],
    target_date: date,
    max_days: int,
) -> int:
    """Count consecutive worked days immediately before target_date (not including it)."""
    if max_days <= 0:
        return 0
    count = 0
    cursor = target_date - timedelta(days=1)
    for _ in range(max_days):
        if cursor in worked_dates:
            count += 1
            cursor -= timedelta(days=1)
        else:
            break
    return count


def resolve_labour_law_settings(
    location_id=None,
    site_id=None,
) -> Tuple[bool, int, int, bool]:
    """Returns (enabled, max_days, max_hours, inherited_from_org_or_global)."""
    from scheduler.models import SiteSetting

    keys = (
        SETTING_ENABLED_KEY,
        SETTING_ENABLED_KEY_LEGACY,
        SETTING_MAX_DAYS_KEY,
        SETTING_MAX_HOURS_KEY,
    )
    qs = SiteSetting.objects.filter(key__in=keys)
    if site_id:
        qs = qs.filter(
            Q(site_id=site_id)
            | Q(location_id=location_id, site_id__isnull=True)
            | Q(location_id__isnull=True, site_id__isnull=True)
        )
    else:
        qs = qs.filter(site_id__isnull=True).filter(
            Q(location_id=location_id) | Q(location_id__isnull=True)
        )

    rows = list(qs.only("key", "value", "location_id", "site_id"))

    def _pick(key: str):
        site_row = None
        org_row = None
        global_row = None
        for row in rows:
            if row.key != key:
                continue
            if site_id and str(row.site_id) == str(site_id):
                site_row = row
            elif row.site_id is None and location_id and str(row.location_id) == str(location_id):
                org_row = row
            elif row.site_id is None and row.location_id is None:
                global_row = row
        if site_row is not None:
            return site_row.value, False
        if org_row is not None:
            return org_row.value, True
        if global_row is not None:
            return global_row.value, True
        return None, True

    enabled_raw, enabled_inherited = _pick(SETTING_ENABLED_KEY)
    if enabled_raw is None:
        enabled_raw, enabled_inherited = _pick(SETTING_ENABLED_KEY_LEGACY)
    max_raw, max_inherited = _pick(SETTING_MAX_DAYS_KEY)
    hours_raw, hours_inherited = _pick(SETTING_MAX_HOURS_KEY)
    enabled = _parse_bool(enabled_raw, DEFAULT_ENABLED)
    max_days = _parse_max_days(max_raw, DEFAULT_MAX_DAYS)
    max_hours = _parse_max_hours(hours_raw, DEFAULT_MAX_HOURS)
    inherited = enabled_inherited and max_inherited and hours_inherited
    return enabled, max_days, max_hours, inherited


def resolve_consecutive_work_settings(
    location_id=None,
    site_id=None,
) -> Tuple[bool, int, bool]:
    enabled, max_days, _max_hours, inherited = resolve_labour_law_settings(location_id, site_id)
    return enabled, max_days, inherited


def bulk_resolve_consecutive_work_settings(location_id, site_ids) -> dict:
    """Map site_id str -> (enabled, max_days, max_hours, inherited)."""
    site_ids = [sid for sid in (site_ids or []) if sid]
    if not site_ids:
        return {}
    enabled, max_days, max_hours, inherited = resolve_labour_law_settings(location_id, site_id=None)
    from scheduler.models import SiteSetting

    overrides = {}
    for row in SiteSetting.objects.filter(
        key__in=(
            SETTING_ENABLED_KEY,
            SETTING_ENABLED_KEY_LEGACY,
            SETTING_MAX_DAYS_KEY,
            SETTING_MAX_HOURS_KEY,
        ),
        site_id__in=site_ids,
    ).only("key", "value", "site_id"):
        bucket = overrides.setdefault(str(row.site_id), {})
        bucket[row.key] = row.value

    result = {}
    for sid in site_ids:
        key = str(sid)
        over = overrides.get(key) or {}
        site_enabled = over.get(SETTING_ENABLED_KEY)
        if site_enabled is None:
            site_enabled = over.get(SETTING_ENABLED_KEY_LEGACY)
        site_max = over.get(SETTING_MAX_DAYS_KEY)
        site_hours = over.get(SETTING_MAX_HOURS_KEY)
        result[key] = (
            _parse_bool(site_enabled, enabled) if site_enabled is not None else enabled,
            _parse_max_days(site_max, max_days) if site_max is not None else max_days,
            _parse_max_hours(site_hours, max_hours) if site_hours is not None else max_hours,
            SETTING_ENABLED_KEY not in over
            and SETTING_ENABLED_KEY_LEGACY not in over
            and SETTING_MAX_DAYS_KEY not in over
            and SETTING_MAX_HOURS_KEY not in over,
        )
    return result


def fetch_worked_dates(user_id, start_date: date, end_date: date) -> Set[date]:
    """Worked shift_dates for this person in [start_date, end_date], any site."""
    if end_date < start_date:
        return set()
    from dashboard.models import AttendanceCheckin

    rows = AttendanceCheckin.objects.filter(
        guard_id=user_id,
        shift_date__gte=start_date,
        shift_date__lte=end_date,
    ).filter(
        Q(checkin_time__isnull=False) | Q(last_checkin_time__isnull=False)
    ).exclude(
        pa_status__in=list(REST_PA_STATUSES)
    ).values_list("shift_date", "pa_status")

    worked: Set[date] = set()
    for shift_date, pa_status in rows:
        if shift_date and is_worked_pa_status(pa_status):
            worked.add(shift_date)
    return worked


def assert_consecutive_work_allowed(
    user,
    target_date: date,
    *,
    location_id=None,
    site_id=None,
    worked_dates: Optional[Set[date]] = None,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
) -> None:
    if enabled is None or max_days is None:
        enabled, max_days, _ = resolve_consecutive_work_settings(location_id, site_id)
    if not enabled or max_days <= 0 or not target_date:
        return

    if worked_dates is None:
        lookback_start = target_date - timedelta(days=max_days)
        worked_dates = fetch_worked_dates(
            getattr(user, "id", user),
            lookback_start,
            target_date - timedelta(days=1),
        )

    consecutive = consecutive_present_count(worked_dates, target_date, max_days)
    if consecutive >= max_days:
        raise ConsecutiveWorkLimitExceeded(
            consecutive=consecutive,
            max_days=max_days,
            blocked_date=target_date,
            reason=ConsecutiveWorkLimitExceeded.REASON_WORKED,
        )


def consecutive_work_block_payload(
    user,
    target_date: date,
    *,
    location_id=None,
    site_id=None,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
) -> Optional[dict]:
    """Return error dict if blocked, else None. For per-row bulk/monthly failures."""
    try:
        assert_consecutive_work_allowed(
            user,
            target_date,
            location_id=location_id,
            site_id=site_id,
            enabled=enabled,
            max_days=max_days,
        )
    except ConsecutiveWorkLimitExceeded as exc:
        return exc.as_dict()
    return None


def fetch_month_worked_minutes(
    user_id,
    month_start: date,
    month_end: date,
    *,
    exclude_date: Optional[date] = None,
) -> int:
    from dashboard.models import AttendanceCheckin

    qs = AttendanceCheckin.objects.filter(
        guard_id=user_id,
        shift_date__gte=month_start,
        shift_date__lte=month_end,
    ).filter(
        Q(checkin_time__isnull=False) | Q(last_checkin_time__isnull=False)
    ).exclude(
        pa_status__in=list(REST_PA_STATUSES)
    )
    if exclude_date:
        qs = qs.exclude(shift_date=exclude_date)
    total = qs.aggregate(total=Coalesce(Sum("duration_minutes"), Value(0)))["total"]
    return int(total or 0)


def assert_monthly_hours_allowed(
    user,
    target_date: date,
    *,
    extra_minutes: int = 0,
    location_id=None,
    site_id=None,
    enabled: Optional[bool] = None,
    max_hours: Optional[int] = None,
    exclude_date: Optional[date] = None,
) -> None:
    if enabled is None or max_hours is None:
        enabled, _days, max_hours, _ = resolve_labour_law_settings(location_id, site_id)
    if not enabled or max_hours <= 0 or not target_date:
        return

    month_start = target_date.replace(day=1)
    month_end = date(target_date.year, target_date.month, monthrange(target_date.year, target_date.month)[1])
    skip_date = target_date if exclude_date is None else exclude_date
    worked = fetch_month_worked_minutes(
        getattr(user, "id", user),
        month_start,
        month_end,
        exclude_date=skip_date,
    )
    extra = max(0, int(extra_minutes or 0))
    max_minutes = int(max_hours) * 60
    if worked >= max_minutes or worked + extra > max_minutes:
        raise MonthlyHourLimitExceeded(
            worked_minutes=worked,
            extra_minutes=extra,
            max_hours=max_hours,
            blocked_date=target_date,
        )


def monthly_hours_block_payload(
    user,
    target_date: date,
    *,
    extra_minutes: int = 0,
    location_id=None,
    site_id=None,
    enabled: Optional[bool] = None,
    max_hours: Optional[int] = None,
) -> Optional[dict]:
    try:
        assert_monthly_hours_allowed(
            user,
            target_date,
            extra_minutes=extra_minutes,
            location_id=location_id,
            site_id=site_id,
            enabled=enabled,
            max_hours=max_hours,
        )
    except MonthlyHourLimitExceeded as exc:
        return exc.as_dict()
    return None


def attendance_write_block_payload(
    user,
    target_date: date,
    *,
    extra_minutes: int = 0,
    location_id=None,
    site_id=None,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
    max_hours: Optional[int] = None,
) -> Optional[dict]:
    """Consecutive-day then monthly-hour gates for bulk/monthly attendance writes."""
    block = consecutive_work_block_payload(
        user,
        target_date,
        location_id=location_id,
        site_id=site_id,
        enabled=enabled,
        max_days=max_days,
    )
    if block:
        return block
    return monthly_hours_block_payload(
        user,
        target_date,
        extra_minutes=extra_minutes,
        location_id=location_id,
        site_id=site_id,
        enabled=enabled,
        max_hours=max_hours,
    )


def _dates_inclusive(start_date: date, end_date: date) -> Set[date]:
    if end_date < start_date:
        start_date, end_date = end_date, start_date
    out: Set[date] = set()
    cursor = start_date
    while cursor <= end_date:
        out.add(cursor)
        cursor += timedelta(days=1)
    return out


def consecutive_scheduled_streak(scheduled: Set[date], target_date: date) -> int:
    """Length of the consecutive scheduled-day run that contains target_date."""
    if target_date not in scheduled:
        return 0
    count = 1
    cursor = target_date - timedelta(days=1)
    while cursor in scheduled:
        count += 1
        cursor -= timedelta(days=1)
    cursor = target_date + timedelta(days=1)
    while cursor in scheduled:
        count += 1
        cursor += timedelta(days=1)
    return count


def fetch_assigned_dates(
    user_id,
    start_date: date,
    end_date: date,
    *,
    exclude_assignment_id=None,
) -> Set[date]:
    """Calendar days covered by non-deleted assignments in [start_date, end_date]."""
    if end_date < start_date:
        return set()
    from scheduler.models import Assignment

    qs = Assignment.objects.filter(
        guard_id=user_id,
        is_deleted=False,
        start_date__lte=end_date,
        end_date__gte=start_date,
    )
    if exclude_assignment_id:
        qs = qs.exclude(id=exclude_assignment_id)
    assigned: Set[date] = set()
    for start, end in qs.values_list("start_date", "end_date"):
        if not start or not end:
            continue
        assigned.update(_dates_inclusive(max(start, start_date), min(end, end_date)))
    return assigned


def fetch_weekoff_dates(user_id, start_date: date, end_date: date) -> Set[date]:
    if end_date < start_date:
        return set()
    from dashboard.models import AttendanceWeekOff

    return set(
        AttendanceWeekOff.objects.filter(
            user_id=user_id,
            weekoff_date__gte=start_date,
            weekoff_date__lte=end_date,
        ).values_list("weekoff_date", flat=True)
    )


def assert_consecutive_work_range(
    user,
    start_date: date,
    end_date: date,
    *,
    location_id=None,
    site_id=None,
    exclude_assignment_id=None,
    assigned_dates: Optional[Set[date]] = None,
    weekoff_dates: Optional[Set[date]] = None,
    worked_dates: Optional[Set[date]] = None,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
) -> None:
    """
    Block scheduler assignment if:
    - the person already worked max_days in a row before a new assigned day, or
    - the assignment (plus neighbouring assignments) is more than max_days with no rest.
    Week-off dates count as rest and split the streak.
    """
    if not start_date or not end_date:
        return
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    if enabled is None or max_days is None:
        enabled, max_days, _ = resolve_consecutive_work_settings(location_id, site_id)
    if not enabled or max_days <= 0:
        return

    window_start = start_date - timedelta(days=max_days)
    window_end = end_date + timedelta(days=max_days)
    user_id = getattr(user, "id", user)

    if assigned_dates is None:
        assigned_dates = fetch_assigned_dates(
            user_id,
            window_start,
            window_end,
            exclude_assignment_id=exclude_assignment_id,
        )
    else:
        assigned_dates = set(assigned_dates)

    if weekoff_dates is None:
        weekoff_dates = fetch_weekoff_dates(user_id, window_start, window_end)
    else:
        weekoff_dates = set(weekoff_dates)

    if worked_dates is None:
        worked_dates = fetch_worked_dates(
            user_id,
            window_start,
            end_date - timedelta(days=1),
        )
    else:
        worked_dates = set(worked_dates)

    new_dates = _dates_inclusive(start_date, end_date)
    cursor = start_date
    while cursor <= end_date:
        if cursor not in weekoff_dates:
            present_streak = consecutive_present_count(worked_dates, cursor, max_days)
            if present_streak >= max_days:
                raise ConsecutiveWorkLimitExceeded(
                    consecutive=present_streak,
                    max_days=max_days,
                    blocked_date=cursor,
                    reason=ConsecutiveWorkLimitExceeded.REASON_WORKED,
                )
        cursor += timedelta(days=1)

    scheduled = (assigned_dates | new_dates) - weekoff_dates
    cursor = start_date
    while cursor <= end_date:
        if cursor in scheduled:
            streak = consecutive_scheduled_streak(scheduled, cursor)
            if streak > max_days:
                streak_start = cursor
                while (streak_start - timedelta(days=1)) in scheduled:
                    streak_start -= timedelta(days=1)
                raise ConsecutiveWorkLimitExceeded(
                    consecutive=streak,
                    max_days=max_days,
                    blocked_date=streak_start + timedelta(days=max_days),
                    reason=ConsecutiveWorkLimitExceeded.REASON_ASSIGNMENT,
                )
        cursor += timedelta(days=1)


def _labour_law_pairs(
    *,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
    max_hours: Optional[int] = None,
) -> Sequence[Tuple[str, str, Optional[str]]]:
    pairs: list[Tuple[str, str, Optional[str]]] = []
    if enabled is not None:
        pairs.append((SETTING_ENABLED_KEY, "true" if enabled else "false", None))
    if max_days is not None:
        pairs.append((SETTING_MAX_DAYS_KEY, str(_parse_max_days(max_days)), "days"))
    if max_hours is not None:
        pairs.append((SETTING_MAX_HOURS_KEY, str(_parse_max_hours(max_hours)), "h"))
    return pairs


def _upsert_labour_law_rows(
    pairs: Sequence[Tuple[str, str, Optional[str]]],
    *,
    location_id=None,
    site_id=None,
    user=None,
) -> None:
    from scheduler.models import SiteSetting

    for key, value, unit in pairs:
        qs = SiteSetting.objects.filter(key=key)
        if site_id:
            qs = qs.filter(site_id=site_id)
        else:
            qs = qs.filter(site_id__isnull=True, location_id=location_id)
        row = qs.first()
        if row:
            row.value = value
            if unit is not None:
                row.unit = unit
            row.modified_by = user
            row.save(update_fields=["value", "unit", "modified_by", "modified_on"])
        else:
            SiteSetting.objects.create(
                key=key,
                value=value,
                unit=unit,
                location_id=None if site_id else location_id,
                site_id=site_id,
                propagate_to_orgs=False,
                created_by=user,
            )


def upsert_site_consecutive_work_settings(
    site,
    *,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
    max_hours: Optional[int] = None,
    user=None,
) -> None:
    """Write per-site overrides. Does not change organisation or global rows."""
    if site is None:
        return
    _upsert_labour_law_rows(
        _labour_law_pairs(enabled=enabled, max_days=max_days, max_hours=max_hours),
        location_id=None,
        site_id=site.id,
        user=user,
    )


def upsert_org_labour_law_settings(
    location_id=None,
    *,
    enabled: Optional[bool] = None,
    max_days: Optional[int] = None,
    max_hours: Optional[int] = None,
    user=None,
) -> None:
    """Write organisation (or global if location_id is None) defaults. Does not change site rows."""
    _upsert_labour_law_rows(
        _labour_law_pairs(enabled=enabled, max_days=max_days, max_hours=max_hours),
        location_id=location_id,
        site_id=None,
        user=user,
    )
