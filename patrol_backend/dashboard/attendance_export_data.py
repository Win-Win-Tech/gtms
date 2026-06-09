"""
Shared attendance v4 export data (Excel + daily detail PDF).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timedelta

import pytz

from dashboard.models import AttendanceCheckin, CheckInLog
from patrol_backend.utils.timezone_utils import (
    combine_date_time_in_user_tz,
    get_user_today,
    get_user_timezone_from_request,
    to_user_timezone,
)


def _shift_window_utc_bounds(obj, shift_day, user_tz, grace_minutes):
    s_u = combine_date_time_in_user_tz(shift_day, obj.shift.start_time, user_tz).astimezone(
        pytz.UTC
    ) - timedelta(minutes=grace_minutes)
    if obj.shift.end_time <= obj.shift.start_time:
        e_u = combine_date_time_in_user_tz(
            shift_day + timedelta(days=1), obj.shift.end_time, user_tz
        ).astimezone(pytz.UTC) + timedelta(minutes=grace_minutes)
    else:
        e_u = combine_date_time_in_user_tz(shift_day, obj.shift.end_time, user_tz).astimezone(
            pytz.UTC
        ) + timedelta(minutes=grace_minutes)
    return s_u, e_u


def _resolve_shift_day(obj, user_tz):
    source_dt = obj.checkin_time or obj.created_on
    if not source_dt:
        return obj.shift_date
    shift_day = obj.shift_date or to_user_timezone(source_dt, user_tz).date()
    if obj.shift and obj.shift.end_time <= obj.shift.start_time and obj.shift_date is None:
        if to_user_timezone(source_dt, user_tz).time() < obj.shift.end_time:
            shift_day = shift_day - timedelta(days=1)
    return shift_day


def _format_hhmm(minutes):
    if minutes is None:
        return "00:00"
    m = max(0, int(minutes))
    return f"{m // 60:02}:{m % 60:02}"


def _pa_status_display(pa_status, live_state, has_checkin, has_checkout):
    """Same labels as Attendance Report UI (pa_status chips)."""
    if not has_checkin:
        return "Absent"
    mapping = {
        "P": "Present",
        "OW": "On Work",
        "M": "Missed Checkout",
        "LD": "Less Duration",
        "A": "Absent",
    }
    text = mapping.get(pa_status or "")
    if not text and live_state == "Checked In":
        return "On Work"
    if not text and has_checkin and not has_checkout:
        return "On Work"
    return text or "—"


def _build_punch_records(matched_logs, user_tz):
    parts = []
    for log in matched_logs:
        local = to_user_timezone(log.timestamp, user_tz)
        t = local.strftime("%H:%M")
        kind = "in" if log.type == "checkin" else "out"
        site = getattr(getattr(log, "site", None), "name", None) or "Kiosk"
        parts.append(f"{t}:{kind}({site})")
    return ",".join(parts)


def gather_attendance_v4_export_context(
    *,
    date_filter="today",
    start_date=None,
    end_date=None,
    guard_id=None,
    location_id=None,
    shift_id=None,
    status_filter=None,
    defaulters=False,
    request=None,
    search=None,
    role=None,
    site_id=None,
    apply_status_filter_fn=None,
):
    """Return filtered attendance rows + metadata for export."""
    from django.db.models import F, Q

    from dashboard.views import _apply_attendance_v3_status_filter, _get_site_setting_int

    if apply_status_filter_fn is None:
        apply_status_filter_fn = _apply_attendance_v3_status_filter

    queryset = AttendanceCheckin.objects.select_related(
        "guard", "shift", "org_location", "site", "assignment"
    )

    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        user_tz = pytz.timezone("Asia/Kolkata")
        user_today = get_user_today(user_tz)

    range_start = user_today
    range_end = user_today

    if date_filter == "today":
        queryset = queryset.filter(shift_date=user_today)
    elif date_filter == "week":
        range_start = user_today - timedelta(days=user_today.weekday())
        range_end = min(range_start + timedelta(days=6), user_today)
        queryset = queryset.filter(shift_date__gte=range_start, shift_date__lte=range_end)
    elif date_filter == "month":
        range_start = user_today.replace(day=1)
        range_end = min(
            user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
            if user_today.month != 12
            else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
            user_today,
        )
        queryset = queryset.filter(shift_date__gte=range_start, shift_date__lte=range_end)
    elif date_filter == "custom" and start_date and end_date:
        try:
            range_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            range_end = min(datetime.strptime(end_date, "%Y-%m-%d").date(), user_today)
            queryset = queryset.filter(shift_date__gte=range_start, shift_date__lte=range_end)
        except ValueError as exc:
            raise ValueError("Invalid custom date format. Use YYYY-MM-DD.") from exc

    if guard_id:
        queryset = queryset.filter(guard_id=guard_id)
    if search and str(search).strip():
        s = str(search).strip()
        queryset = queryset.filter(
            Q(guard__name__icontains=s) | Q(guard__employee_code__icontains=s)
        )
    if role and str(role).strip().lower() not in ("", "all"):
        queryset = queryset.filter(guard__role__iexact=str(role).strip().lower())
    if location_id:
        queryset = queryset.filter(org_location_id=location_id)
    if site_id:
        queryset = queryset.filter(site_id=site_id)
    if shift_id:
        queryset = queryset.filter(shift_id=shift_id)
    if status_filter:
        queryset = apply_status_filter_fn(queryset, status_filter)
    if defaulters:
        queryset = queryset.filter(checkin_time__isnull=False).filter(
            (Q(last_checkin_time__isnull=False) & Q(last_checkout_time__isnull=True))
            | Q(last_checkin_time__gt=F("last_checkout_time"))
            | (Q(last_checkin_time__isnull=True) & Q(checkout_time__isnull=True))
        )

    page_items = list(
        queryset.order_by(
            "-shift_date", "-checkin_time", "-last_checkin_time", "-modified_on", "-id"
        )
    )

    grace_minutes = _get_site_setting_int(
        key="shift_grace_time",
        location_id=location_id,
        default_value=30,
    )

    prefetched_logs = {}
    if page_items:
        guard_ids = {obj.guard_id for obj in page_items}
        min_start_utc = None
        max_end_utc = None
        for obj in page_items:
            shift_day = _resolve_shift_day(obj, user_tz)
            if not shift_day or not obj.shift:
                continue
            s_u, e_u = _shift_window_utc_bounds(obj, shift_day, user_tz, grace_minutes)
            if min_start_utc is None or s_u < min_start_utc:
                min_start_utc = s_u
            if max_end_utc is None or e_u > max_end_utc:
                max_end_utc = e_u
        if min_start_utc and max_end_utc:
            for log in CheckInLog.objects.filter(
                guard_id__in=guard_ids,
                timestamp__gte=min_start_utc,
                timestamp__lt=max_end_utc,
            ).select_related("site").order_by("timestamp"):
                prefetched_logs.setdefault(log.guard_id, []).append(log)

    detail_rows = []
    for obj in page_items:
        shift_day = _resolve_shift_day(obj, user_tz)
        guard_logs = prefetched_logs.get(obj.guard_id, [])
        matched_logs = []
        if shift_day and obj.shift:
            s_u, e_u = _shift_window_utc_bounds(obj, shift_day, user_tz, grace_minutes)
            matched_logs = [
                log
                for log in guard_logs
                if log.assignment_id == obj.assignment_id
                and log.shift_id == obj.shift_id
                and log.org_location_id == obj.org_location_id
                and s_u <= log.timestamp < e_u
            ]

        first_checkin = to_user_timezone(obj.checkin_time, user_tz) if obj.checkin_time else None
        last_checkout = None
        if obj.last_checkout_time:
            last_checkout = to_user_timezone(obj.last_checkout_time, user_tz)
        elif obj.checkout_time:
            last_checkout = to_user_timezone(obj.checkout_time, user_tz)

        live_state = (
            "Checked In"
            if (
                obj.last_checkin_time
                and (not obj.last_checkout_time or obj.last_checkin_time > obj.last_checkout_time)
            )
            else "Checked Out"
        )
        has_checkin = bool(first_checkin or obj.last_checkin_time)
        has_checkout = bool(last_checkout)
        work_minutes = int(obj.duration_minutes) if obj.duration_minutes is not None else 0
        if not has_checkin:
            work_minutes = 0

        sched_in = obj.shift.start_time.strftime("%H:%M") if obj.shift else ""
        sched_out = obj.shift.end_time.strftime("%H:%M") if obj.shift else ""

        detail_rows.append(
            {
                "guard_id": obj.guard_id,
                "emp_code": getattr(obj.guard, "employee_code", None) or "",
                "emp_name": obj.guard.name or "",
                "designation": (getattr(obj.guard, "role", None) or "").strip(),
                "att_date": shift_day,
                "att_date_display": shift_day.strftime("%d-%b-%Y") if shift_day else "",
                "in_time": first_checkin.strftime("%H:%M") if first_checkin else "",
                "out_time": last_checkout.strftime("%H:%M") if last_checkout else "",
                "shift_name": obj.shift.name if obj.shift else "",
                "sched_in": sched_in,
                "sched_out": sched_out,
                "work_dur": _format_hhmm(work_minutes),
                "work_minutes": work_minutes,
                "tot_dur": _format_hhmm(work_minutes),
                "status": _pa_status_display(
                    obj.pa_status, live_state, has_checkin, has_checkout
                ),
                "pa_status": obj.pa_status,
                "punch_records": _build_punch_records(matched_logs, user_tz),
                "location_name": obj.org_location.name if obj.org_location else "",
                "site_name": obj.site.name if obj.site else "",
            }
        )

    from scheduler.models import Location, LocationSite

    org_name = ""
    dept_name = ""
    if location_id and page_items:
        org_name = page_items[0].org_location.name if page_items[0].org_location else ""
    elif location_id:
        loc = Location.objects.filter(id=location_id, is_deleted=False).first()
        org_name = loc.name if loc else ""
    if site_id:
        site = LocationSite.objects.filter(id=site_id).first()
        dept_name = site.name if site else ""
    elif page_items and page_items[0].site:
        dept_name = page_items[0].site.name

    return {
        "detail_rows": detail_rows,
        "user_tz": user_tz,
        "range_start": range_start,
        "range_end": range_end,
        "org_name": org_name,
        "dept_name": dept_name,
        "printed_at": to_user_timezone(datetime.now(pytz.UTC), user_tz),
        "row_count": len(page_items),
    }


def group_detail_rows_by_employee(detail_rows):
    """Group export rows by employee, sorted by emp code then name."""
    grouped = defaultdict(list)
    meta = {}
    for row in detail_rows:
        gid = row["guard_id"]
        grouped[gid].append(row)
        meta[gid] = {
            "emp_code": row["emp_code"],
            "emp_name": row["emp_name"],
            "designation": row["designation"],
        }
    order = sorted(
        grouped.keys(),
        key=lambda g: (
            str(meta[g]["emp_code"] or "").lower(),
            str(meta[g]["emp_name"] or "").lower(),
        ),
    )
    result = []
    for gid in order:
        rows = sorted(grouped[gid], key=lambda r: r.get("att_date") or date.min)
        total_minutes = sum(int(r.get("work_minutes") or 0) for r in rows)
        present_days = sum(1 for r in rows if r.get("status") == "Present")
        absent_days = sum(1 for r in rows if r.get("status") == "Absent")
        result.append(
            {
                "guard_id": gid,
                "emp_code": meta[gid]["emp_code"],
                "emp_name": meta[gid]["emp_name"],
                "designation": meta[gid]["designation"],
                "rows": rows,
                "total_duration": _format_hhmm(total_minutes),
                "present_days": present_days,
                "absent_days": absent_days,
            }
        )
    return result
