"""
Site + shift + role max headcount helpers (Phase 2).

Fast path for later punch/assign gates:
- Process-local cache of caps for (site_id, shift_id)
- Empty caps => unlimited (no COUNT)
- At most one COUNT when a role cap exists

Not wired into checkin/assign yet (Phase 3+).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from typing import Any, Dict, Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
# key: (site_id_str, shift_id_str) -> (expires_at_monotonic, roles_dict)
_CAPS_CACHE: Dict[Tuple[str, str], Tuple[float, Dict[str, int]]] = {}
_CAPS_TTL_SECONDS = 60.0


class SiteShiftRoleCapExceeded(Exception):
    def __init__(
        self,
        *,
        role: str,
        current: int,
        max_count: int,
        site_id: str,
        shift_id: str,
        on_date: date,
        message: Optional[str] = None,
    ):
        self.role = (role or "").strip().lower()
        self.current = int(current)
        self.max_count = int(max_count)
        self.site_id = str(site_id)
        self.shift_id = str(shift_id)
        self.on_date = on_date
        self.message = message or (
            f"{self.role.upper()} headcount full for this site and shift "
            f"({self.current}/{self.max_count})"
        )
        super().__init__(self.message)

    def as_dict(self) -> dict:
        return {
            "code": "site_shift_role_cap_exceeded",
            "error": self.message,
            "message": self.message,
            "role": self.role,
            "current": self.current,
            "max": self.max_count,
            "site_id": self.site_id,
            "shift_id": self.shift_id,
            "date": self.on_date.isoformat() if self.on_date else None,
        }

    def as_serializer_error(self) -> dict:
        """Single field so DRF / bulk UI stay readable."""
        return {"error": self.message}


def _cache_key(site_id, shift_id) -> Tuple[str, str]:
    return (str(site_id), str(shift_id))


def invalidate_caps_cache(site_id=None, shift_id=None) -> None:
    """Clear cached caps. Call after PUT config. None clears all."""
    with _LOCK:
        if site_id is None and shift_id is None:
            _CAPS_CACHE.clear()
            return
        if site_id is not None and shift_id is not None:
            _CAPS_CACHE.pop(_cache_key(site_id, shift_id), None)
            return
        site_s = str(site_id) if site_id is not None else None
        shift_s = str(shift_id) if shift_id is not None else None
        for key in list(_CAPS_CACHE.keys()):
            if site_s is not None and key[0] != site_s:
                continue
            if shift_s is not None and key[1] != shift_s:
                continue
            _CAPS_CACHE.pop(key, None)


def _load_caps_from_db(site_id, shift_id) -> Dict[str, int]:
    from scheduler.models import SiteShiftRoleCap

    rows = (
        SiteShiftRoleCap.objects.filter(site_id=site_id, shift_id=shift_id)
        .only("role", "max_count")
        .iterator()
    )
    out: Dict[str, int] = {}
    for row in rows:
        role = (row.role or "").strip().lower()
        if not role:
            continue
        out[role] = int(row.max_count)
    return out


def get_caps_cached(site_id, shift_id) -> Dict[str, int]:
    """
    Return {role: max_count} for site+shift.
    Empty dict means unlimited (no caps configured).
    """
    if not site_id or not shift_id:
        return {}
    key = _cache_key(site_id, shift_id)
    now = time.monotonic()
    with _LOCK:
        hit = _CAPS_CACHE.get(key)
        if hit is not None and hit[0] > now:
            return dict(hit[1])
    roles = _load_caps_from_db(site_id, shift_id)
    with _LOCK:
        _CAPS_CACHE[key] = (now + _CAPS_TTL_SECONDS, dict(roles))
    return roles


def has_any_caps(site_id, shift_id) -> bool:
    return bool(get_caps_cached(site_id, shift_id))


def _as_date(value) -> Optional[date]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except Exception:
        return None


def count_role_assigned(
    *,
    site_id,
    shift_id,
    on_date,
    role: str,
    exclude_guard_id=None,
) -> int:
    """
    One COUNT: active users with role, assignment covering date for shift,
    posted to site via AssignmentDailySite.
    """
    from scheduler.models import Assignment, AssignmentDailySite

    role_norm = (role or "").strip().lower()
    day = _as_date(on_date)
    if not site_id or not shift_id or not day or not role_norm:
        return 0

    guard_ids = AssignmentDailySite.objects.filter(
        site_id=site_id,
        date=day,
    ).values_list("guard_id", flat=True)

    qs = Assignment.objects.filter(
        is_deleted=False,
        shift_id=shift_id,
        start_date__lte=day,
        end_date__gte=day,
        guard_id__in=guard_ids,
        guard__role=role_norm,
        guard__is_deleted=False,
        guard__is_active=True,
    )
    if exclude_guard_id:
        qs = qs.exclude(guard_id=exclude_guard_id)
    return int(qs.values("guard_id").distinct().count())


def assert_headcount_allowed(
    *,
    site_id,
    shift_id,
    on_date,
    role: str,
    extra: int = 1,
    exclude_guard_id=None,
) -> Dict[str, Any]:
    """
    Raise SiteShiftRoleCapExceeded when adding `extra` would exceed the role cap.
    Returns a small timing/info dict for callers (always cheap when no caps).
    """
    day = _as_date(on_date)
    role_norm = (role or "").strip().lower()
    info: Dict[str, Any] = {
        "headcount_checked": False,
        "headcount_skipped": "missing_input",
    }
    if not site_id or not shift_id or not day or not role_norm:
        return info

    caps = get_caps_cached(site_id, shift_id)
    if not caps:
        info["headcount_skipped"] = "no_caps"
        return info

    max_count = caps.get(role_norm)
    if max_count is None:
        # Other roles capped; this role unlimited
        info["headcount_skipped"] = "role_uncapped"
        info["caps_total"] = sum(caps.values())
        return info

    current = count_role_assigned(
        site_id=site_id,
        shift_id=shift_id,
        on_date=day,
        role=role_norm,
        exclude_guard_id=exclude_guard_id,
    )
    info.update(
        {
            "headcount_checked": True,
            "headcount_skipped": None,
            "role": role_norm,
            "current": current,
            "max": int(max_count),
            "caps_total": sum(caps.values()),
        }
    )
    if current + int(extra) > int(max_count):
        raise SiteShiftRoleCapExceeded(
            role=role_norm,
            current=current,
            max_count=int(max_count),
            site_id=str(site_id),
            shift_id=str(shift_id),
            on_date=day,
        )
    return info


def assert_headcount_range(
    *,
    site_id,
    shift_id,
    start_date,
    end_date,
    role: str,
    extra: int = 1,
    exclude_guard_id=None,
    only_date=None,
) -> Dict[str, Any]:
    """
    Check each day in [start_date, end_date] (or only_date when set).
    Early-exits when no caps are configured for the site+shift.
    """
    from datetime import timedelta

    day0 = _as_date(only_date) if only_date is not None else _as_date(start_date)
    day1 = _as_date(only_date) if only_date is not None else _as_date(end_date)
    role_norm = (role or "").strip().lower()
    info: Dict[str, Any] = {
        "headcount_checked": False,
        "headcount_skipped": "missing_input",
        "days_checked": 0,
    }
    if not site_id or not shift_id or not day0 or not day1 or not role_norm:
        return info
    if day1 < day0:
        day0, day1 = day1, day0

    caps = get_caps_cached(site_id, shift_id)
    if not caps:
        info["headcount_skipped"] = "no_caps"
        return info
    if role_norm not in caps:
        info["headcount_skipped"] = "role_uncapped"
        info["caps_total"] = sum(caps.values())
        return info

    days_checked = 0
    last: Dict[str, Any] = {}
    current = day0
    while current <= day1:
        last = assert_headcount_allowed(
            site_id=site_id,
            shift_id=shift_id,
            on_date=current,
            role=role_norm,
            extra=extra,
            exclude_guard_id=exclude_guard_id,
        )
        days_checked += 1
        current += timedelta(days=1)
    last["days_checked"] = days_checked
    return last
