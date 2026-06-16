import calendar
import ast
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q, Sum
from django.utils.dateparse import parse_date

from dashboard.models import AttendanceCheckin
from patrol_backend.utils.timezone_utils import convert_date_range_to_utc, get_user_today, to_user_timezone


def parse_month(month_str: str):
    """
    Parse YYYY-MM to (year, month, first_day, last_day).
    """
    try:
        year, month = [int(x) for x in month_str.split("-")]
        first_day = date(year, month, 1)
        last_day = date(year, month, calendar.monthrange(year, month)[1])
        return year, month, first_day, last_day
    except Exception as exc:
        raise ValueError("month must be in YYYY-MM format") from exc


def calculate_attendance_from_master(user, month_str, user_tz):
    """
    Current GTMS rule:
    - working_days = full month days
    - present/absent from AttendanceCheckin.pa_status (P/LD/M); OW days do not add to either until closed
    - absent for no-entry days in month
    """
    _, _, month_start, month_end = parse_month(month_str)
    month_days = (month_end - month_start).days + 1

    # Convert local-month date boundaries to UTC to avoid timezone day-boundary drift.
    start_dt, end_dt = convert_date_range_to_utc(month_start, month_end, user_tz)
    records = AttendanceCheckin.objects.filter(guard=user).filter(
        (
            ~Q(checkin_time__isnull=True) &
            Q(checkin_time__gte=start_dt, checkin_time__lte=end_dt)
        ) |
        (
            Q(checkin_time__isnull=True) &
            ~Q(last_checkin_time__isnull=True) &
            Q(last_checkin_time__gte=start_dt, last_checkin_time__lte=end_dt)
        )
    ).order_by("modified_on")

    # Day-level status: pick latest modified master row for each day.
    day_status = {}
    for row in records:
        day_dt = row.checkin_time or row.last_checkin_time
        if not day_dt:
            continue
        local_day_dt = to_user_timezone(day_dt, user_tz)
        d = local_day_dt.date()
        prev = day_status.get(d)
        if prev is None or row.modified_on >= prev["modified_on"]:
            day_status[d] = {
                "pa_status": row.pa_status,
                "modified_on": row.modified_on,
            }

    # If there is no attendance record at all in this month, treat payroll attendance as zeroed.
    # This prevents generating salary/deductions for employees with no attendance data.
    if not day_status:
        return {
            "month_days": month_days,
            "working_days": Decimal("0"),
            "present_days": Decimal("0"),
            "half_days": Decimal("0"),
            "absent_days": Decimal("0"),
            "paid_days": Decimal("0"),
        }

    present_days = Decimal("0")
    absent_days = Decimal("0")

    current = month_start
    while current <= month_end:
        st = (day_status.get(current) or {}).get("pa_status")
        if st == "P":
            present_days += Decimal("1")
        elif st == "OW":
            # Shift not finished in payroll sense — do not count as full present/absent here.
            pass
        elif st in ("LD", "M", "A"):
            # Less duration / missed checkout / legacy absent should be treated as absent for payroll.
            absent_days += Decimal("1")
        else:
            # no master entry/mark -> absent under current business rule
            absent_days += Decimal("1")
        current += timedelta(days=1)

    working_days = Decimal(str(month_days))
    half_days = Decimal("0")
    paid_days = present_days

    return {
        "month_days": month_days,
        "working_days": working_days,
        "present_days": present_days,
        "half_days": half_days,
        "absent_days": absent_days,
        "paid_days": paid_days,
    }


def resolve_quick_pay_date_range(date_filter, start_date_str, end_date_str, user_tz):
    """Resolve today, this month (1st through today), or custom range (capped at user today)."""
    user_today = get_user_today(user_tz)
    df = (date_filter or "today").strip().lower()
    if df == "today":
        return user_today, user_today
    if df == "month":
        return user_today.replace(day=1), user_today
    if df == "custom":
        start_date = parse_date(start_date_str or "")
        end_date = parse_date(end_date_str or "")
        if not start_date or not end_date:
            raise ValueError("start_date and end_date are required for custom range (YYYY-MM-DD).")
        if start_date > end_date:
            raise ValueError("start_date must be on or before end_date.")
        if end_date > user_today:
            end_date = user_today
        return start_date, end_date
    raise ValueError("date_filter must be 'today', 'month', or 'custom'.")


def resolve_hourly_wage_date_range(date_filter, start_date_str, end_date_str, user_tz):
    """Backward-compatible alias for quick pay date resolution."""
    return resolve_quick_pay_date_range(date_filter, start_date_str, end_date_str, user_tz)


def _format_wage_period_label(start_date, end_date):
    """Display label for WORK DATE column (matches payroll sheet style)."""
    if start_date == end_date:
        return f"{start_date.month}/{start_date.day}/{start_date.year}"
    return f"{start_date.strftime('%d-%b-%Y')} to {end_date.strftime('%d-%b-%Y')}"


def _format_quick_pay_period_label(start_date, end_date, date_filter="today"):
    """Period label for Quick Pay Report Date column."""
    df = (date_filter or "today").strip().lower()
    if df == "month":
        return start_date.strftime("%B %Y")
    return _format_wage_period_label(start_date, end_date)


def _fetch_day_status_for_range(user, start_date, end_date, user_tz):
    start_dt, end_dt = convert_date_range_to_utc(start_date, end_date, user_tz)
    records = AttendanceCheckin.objects.filter(guard=user).filter(
        (
            ~Q(checkin_time__isnull=True)
            & Q(checkin_time__gte=start_dt, checkin_time__lte=end_dt)
        )
        | (
            Q(checkin_time__isnull=True)
            & ~Q(last_checkin_time__isnull=True)
            & Q(last_checkin_time__gte=start_dt, last_checkin_time__lte=end_dt)
        )
    ).order_by("modified_on")

    day_status = {}
    for row in records:
        day_dt = row.checkin_time or row.last_checkin_time
        if not day_dt:
            continue
        local_day_dt = to_user_timezone(day_dt, user_tz)
        d = local_day_dt.date()
        prev = day_status.get(d)
        if prev is None or row.modified_on >= prev["modified_on"]:
            day_status[d] = {
                "pa_status": row.pa_status,
                "modified_on": row.modified_on,
            }
    return day_status


def calculate_attendance_from_master_for_range(user, start_date, end_date, user_tz):
    """Attendance snapshot for an arbitrary date range (monthly quick pay)."""
    period_days = (end_date - start_date).days + 1
    day_status = _fetch_day_status_for_range(user, start_date, end_date, user_tz)

    if not day_status:
        return {
            "month_days": period_days,
            "working_days": Decimal("0"),
            "present_days": Decimal("0"),
            "half_days": Decimal("0"),
            "absent_days": Decimal("0"),
            "paid_days": Decimal("0"),
            "has_attendance": False,
        }

    present_days = Decimal("0")
    absent_days = Decimal("0")
    current = start_date
    while current <= end_date:
        st = (day_status.get(current) or {}).get("pa_status")
        if st == "P":
            present_days += Decimal("1")
        elif st == "OW":
            pass
        elif st in ("LD", "M", "A"):
            absent_days += Decimal("1")
        else:
            absent_days += Decimal("1")
        current += timedelta(days=1)

    working_days = Decimal(str(period_days))
    half_days = Decimal("0")
    paid_days = present_days

    return {
        "month_days": period_days,
        "working_days": working_days,
        "present_days": present_days,
        "half_days": half_days,
        "absent_days": absent_days,
        "paid_days": paid_days,
        "has_attendance": True,
    }


def calculate_hourly_attendance_for_range(user, start_date, end_date, user_tz, location_id=None):
    """Hourly attendance snapshot for an arbitrary date range."""
    base = calculate_attendance_from_master_for_range(user, start_date, end_date, user_tz)
    worked_minutes = _sum_worked_minutes_for_user(
        user, start_date, end_date, location_id=location_id
    )
    worked_days = set()
    records = AttendanceCheckin.objects.filter(
        guard=user,
        shift_date__gte=start_date,
        shift_date__lte=end_date,
        duration_minutes__gt=0,
    )
    if location_id:
        records = records.filter(org_location_id=location_id)
    for row in records.only("shift_date"):
        if row.shift_date:
            worked_days.add(row.shift_date)

    paid_hours = (Decimal(worked_minutes) / Decimal("60")).quantize(Decimal("0.01"))
    base.update(
        {
            "worked_minutes": worked_minutes,
            "paid_hours": paid_hours,
            "paid_days": Decimal(str(len(worked_days))),
        }
    )
    return base


def _sum_worked_minutes_for_user(user, start_date, end_date, location_id=None):
    """Sum duration_minutes for hourly wage (shared with monthly hourly payslip logic)."""
    records = AttendanceCheckin.objects.filter(
        guard=user,
        shift_date__gte=start_date,
        shift_date__lte=end_date,
        duration_minutes__gt=0,
    )
    if location_id:
        records = records.filter(org_location_id=location_id)
    worked_minutes = 0
    for row in records.only("duration_minutes"):
        worked_minutes += int(row.duration_minutes or 0)
    return worked_minutes


def gather_hourly_wage_summary_rows(
    location_id,
    start_date,
    end_date,
    search=None,
    role=None,
):
    """
    One aggregated row per hourly employee with worked minutes in [start_date, end_date].
    Skips employees with zero worked minutes in the period.
    """
    from payslip.models import EmployeePayrollProfile
    from scheduler.models import Location

    profiles = EmployeePayrollProfile.objects.filter(
        location_id=location_id,
        is_active=True,
        salary_type="hourly",
        hourly_rate__gt=0,
    ).select_related("user")

    if search and str(search).strip():
        s = str(search).strip()
        profiles = profiles.filter(
            Q(user__name__icontains=s) | Q(user__employee_code__icontains=s)
        )
    if role and str(role).strip().lower() not in ("", "all"):
        profiles = profiles.filter(user__role__iexact=str(role).strip().lower())

    profile_list = list(profiles)
    if not profile_list:
        loc = Location.objects.filter(id=location_id, is_deleted=False).only("name").first()
        return {
            "rows": [],
            "org_name": loc.name if loc else "",
            "period_label": _format_wage_period_label(start_date, end_date),
            "start_date": start_date,
            "end_date": end_date,
        }

    user_ids = [p.user_id for p in profile_list]
    att_agg = (
        AttendanceCheckin.objects.filter(
            guard_id__in=user_ids,
            shift_date__gte=start_date,
            shift_date__lte=end_date,
            duration_minutes__gt=0,
            org_location_id=location_id,
        )
        .values("guard_id")
        .annotate(total_minutes=Sum("duration_minutes"))
    )
    minutes_by_user = {
        row["guard_id"]: int(row["total_minutes"] or 0) for row in att_agg
    }

    period_label = _format_wage_period_label(start_date, end_date)
    rows = []
    for profile in profile_list:
        worked_minutes = minutes_by_user.get(profile.user_id, 0)
        if worked_minutes <= 0:
            continue
        hourly_rate = _to_decimal(profile.hourly_rate, "0")
        paid_hours = (Decimal(worked_minutes) / Decimal("60")).quantize(Decimal("0.01"))
        salary = ((hourly_rate * Decimal(worked_minutes)) / Decimal("60")).quantize(
            Decimal("0.01")
        )
        user = profile.user
        rows.append(
            {
                "user_id": str(profile.user_id),
                "wo_no": getattr(user, "employee_code", None) or "",
                "work_date": period_label,
                "hourly_rate": hourly_rate,
                "paid_hours": paid_hours,
                "worked_minutes": worked_minutes,
                "salary": salary,
                "name": (getattr(user, "name", None) or "").strip().upper(),
            }
        )

    rows.sort(key=lambda r: (str(r.get("wo_no") or "").strip(), r.get("name") or ""))

    loc = Location.objects.filter(id=location_id, is_deleted=False).only("name").first()
    return {
        "rows": rows,
        "org_name": loc.name if loc else "",
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
    }


def gather_quick_pay_report_context(
    location_id,
    start_date,
    end_date,
    user_tz,
    acting_user,
    search=None,
    role=None,
    date_filter="today",
    user_ids=None,
    field_config_overrides=None,
):
    """
    Quick Pay Report: hourly + monthly sections using payslip formula engine.
    Does not create PayslipRecord rows.
    """
    from payslip.models import EmployeePayrollProfile, PayslipField
    from payslip.payroll_bootstrap import resolve_profile_field_config
    from scheduler.models import Location

    profiles = EmployeePayrollProfile.objects.filter(
        location_id=location_id,
        is_active=True,
    ).select_related("user")

    if search and str(search).strip():
        s = str(search).strip()
        profiles = profiles.filter(
            Q(user__name__icontains=s) | Q(user__employee_code__icontains=s)
        )
    if role and str(role).strip().lower() not in ("", "all"):
        profiles = profiles.filter(user__role__iexact=str(role).strip().lower())
    if user_ids:
        normalized_ids = [str(uid).strip() for uid in user_ids if str(uid).strip()]
        if normalized_ids:
            profiles = profiles.filter(user_id__in=normalized_ids)

    profile_list = list(profiles)
    overrides = field_config_overrides or {}
    period_label = _format_quick_pay_period_label(start_date, end_date, date_filter)
    loc = Location.objects.filter(id=location_id, is_deleted=False).only("name").first()
    org_name = loc.name if loc else ""

    if not profile_list:
        return {
            "hourly_rows": [],
            "monthly_rows": [],
            "org_name": org_name,
            "period_label": period_label,
            "start_date": start_date,
            "end_date": end_date,
        }

    hourly_rows = []
    monthly_rows = []

    for profile in profile_list:
        salary_type = profile.salary_type or "monthly"
        user = profile.user
        employee_code = getattr(user, "employee_code", None) or ""
        name = (getattr(user, "name", None) or "").strip().upper()

        field_config = resolve_profile_field_config(
            profile,
            acting_user,
            field_config_id=overrides.get(str(profile.user_id)),
        )
        fields = PayslipField.objects.filter(
            field_config=field_config,
            is_deleted=False,
            is_visible=True,
        ).order_by("display_order")

        if salary_type == "hourly":
            if not _to_decimal(profile.hourly_rate, "0") > Decimal("0"):
                continue
            attendance_snapshot = calculate_hourly_attendance_for_range(
                user,
                start_date,
                end_date,
                user_tz,
                location_id=location_id,
            )
            if int(attendance_snapshot.get("worked_minutes") or 0) <= 0:
                continue
            calc = calculate_salary_fields(
                profile.gross_salary,
                fields,
                attendance_snapshot,
                salary_type="hourly",
                hourly_rate=profile.hourly_rate,
            )
            hourly_rows.append(
                {
                    "user_id": str(profile.user_id),
                    "employee_code": employee_code,
                    "date_label": period_label,
                    "name": name,
                    "rate": _to_decimal(profile.hourly_rate, "0"),
                    "worked_hours": _to_decimal(attendance_snapshot.get("paid_hours"), "0"),
                    "salary": calc["net_pay"],
                }
            )
        else:
            attendance_snapshot = calculate_attendance_from_master_for_range(
                user, start_date, end_date, user_tz
            )
            if not attendance_snapshot.get("has_attendance"):
                continue
            calc = calculate_salary_fields(
                profile.gross_salary,
                fields,
                attendance_snapshot,
                salary_type="monthly",
                hourly_rate=profile.hourly_rate,
            )
            monthly_rows.append(
                {
                    "user_id": str(profile.user_id),
                    "employee_code": employee_code,
                    "date_label": period_label,
                    "name": name,
                    "gross_salary": _to_decimal(profile.gross_salary, "0"),
                    "paid_days": _to_decimal(attendance_snapshot.get("paid_days"), "0"),
                    "salary": calc["net_pay"],
                }
            )

    hourly_rows.sort(
        key=lambda r: (str(r.get("employee_code") or "").strip(), r.get("name") or "")
    )
    monthly_rows.sort(
        key=lambda r: (str(r.get("employee_code") or "").strip(), r.get("name") or "")
    )

    return {
        "hourly_rows": hourly_rows,
        "monthly_rows": monthly_rows,
        "org_name": org_name,
        "period_label": period_label,
        "start_date": start_date,
        "end_date": end_date,
    }


def calculate_hourly_attendance_from_master(user, month_str, user_tz, location_id=None):
    """
    Hourly payroll uses the master attendance duration already computed from
    check-in/check-out sessions. Salary is calculated from exact minutes so
    partial hours (for example 20 minutes) are paid correctly.
    """
    base = calculate_attendance_from_master(user, month_str, user_tz)
    _, _, month_start, month_end = parse_month(month_str)

    worked_minutes = _sum_worked_minutes_for_user(
        user, month_start, month_end, location_id=location_id
    )
    worked_days = set()
    records = AttendanceCheckin.objects.filter(
        guard=user,
        shift_date__gte=month_start,
        shift_date__lte=month_end,
        duration_minutes__gt=0,
    )
    if location_id:
        records = records.filter(org_location_id=location_id)
    for row in records.only("shift_date"):
        if row.shift_date:
            worked_days.add(row.shift_date)

    paid_hours = (Decimal(worked_minutes) / Decimal("60")).quantize(Decimal("0.01"))
    base.update(
        {
            "worked_minutes": worked_minutes,
            "paid_hours": paid_hours,
            # Hourly employees may still need a day count in existing displays.
            "paid_days": Decimal(str(len(worked_days))),
        }
    )
    return base


def _to_decimal(value, default="0"):
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _eval_formula_safely(expr: str, context: dict) -> Decimal:
    """
    Evaluate payroll formula with strict AST whitelist.
    Allowed:
    - numeric literals
    - names from context
    - +, -, *, /, %, //, **
    - unary +/-
    - Decimal(<literal-or-name>)
    """
    tree = ast.parse(expr, mode="eval")

    def _node(n):
        if isinstance(n, ast.Expression):
            return _node(n.body)
        if isinstance(n, ast.Constant):
            return _to_decimal(n.value, "0")
        if isinstance(n, ast.Name):
            if n.id not in context:
                raise ValueError(f"Unknown symbol '{n.id}'")
            return _to_decimal(context[n.id], "0")
        if isinstance(n, ast.UnaryOp):
            val = _node(n.operand)
            if isinstance(n.op, ast.USub):
                return -val
            if isinstance(n.op, ast.UAdd):
                return val
            raise ValueError("Unsupported unary operator")
        if isinstance(n, ast.BinOp):
            left = _node(n.left)
            right = _node(n.right)
            if isinstance(n.op, ast.Add):
                return left + right
            if isinstance(n.op, ast.Sub):
                return left - right
            if isinstance(n.op, ast.Mult):
                return left * right
            if isinstance(n.op, ast.Div):
                return left / right
            if isinstance(n.op, ast.Mod):
                return left % right
            if isinstance(n.op, ast.FloorDiv):
                return left // right
            if isinstance(n.op, ast.Pow):
                return left ** right
            raise ValueError("Unsupported binary operator")
        if isinstance(n, ast.Call):
            if not isinstance(n.func, ast.Name) or n.func.id != "Decimal":
                raise ValueError("Only Decimal(...) call is allowed")
            if len(n.args) != 1 or n.keywords:
                raise ValueError("Decimal(...) accepts exactly one argument")
            return _to_decimal(_node(n.args[0]), "0")
        raise ValueError("Unsupported expression type")

    return _to_decimal(_node(tree), "0")


def calculate_salary_fields(
    gross_salary,
    field_rows,
    attendance_snapshot,
    salary_type="monthly",
    hourly_rate=None,
):
    """
    Simple field engine:
    - FIXED: numeric value
    - PERCENTAGE: % of gross_salary
    - FORMULA: python expression referencing computed fields + attendance vars
    """
    gross = _to_decimal(gross_salary, "0")
    hourly = _to_decimal(hourly_rate, "0")
    worked_minutes = _to_decimal(attendance_snapshot.get("worked_minutes"), "0")
    base_pay = gross
    if salary_type == "hourly":
        base_pay = ((hourly * worked_minutes) / Decimal("60")).quantize(Decimal("0.01"))
    half_days_count = _to_decimal(attendance_snapshot.get("half_days"), "0")
    half_days_paid = half_days_count * Decimal("0.5")
    context = {
        "gross_salary": gross,
        "salary_type": Decimal("1") if salary_type == "hourly" else Decimal("0"),
        "hourly_rate": hourly,
        "worked_minutes": worked_minutes,
        "paid_hours": _to_decimal(attendance_snapshot.get("paid_hours"), "0"),
        "base_pay": base_pay,
        "month_days": _to_decimal(attendance_snapshot.get("month_days"), "0"),
        "working_days": _to_decimal(attendance_snapshot.get("working_days"), "0"),
        "present_days": _to_decimal(attendance_snapshot.get("present_days"), "0"),
        # Keep half_days as paid-day contribution (count * 0.5) so formulas
        # like present_days + half_days behave as users expect.
        "half_days": half_days_paid,
        "half_days_count": half_days_count,
        "half_days_paid": half_days_paid,
        "absent_days": _to_decimal(attendance_snapshot.get("absent_days"), "0"),
        "paid_days": _to_decimal(attendance_snapshot.get("paid_days"), "0"),
    }
    if salary_type == "hourly":
        zero_attendance = context["worked_minutes"] <= Decimal("0")
    else:
        zero_attendance = context["working_days"] <= Decimal("0") or context["paid_days"] <= Decimal("0")

    field_values = {}
    total_earnings = Decimal("0")
    total_deductions = Decimal("0")

    for row in field_rows:
        code = row.field_code
        value_type = row.value_type
        raw_value = row.value
        amount = Decimal("0")

        if zero_attendance and row.field_type in ("EARNING", "DEDUCTION"):
            amount = Decimal("0")
        else:
            if value_type == "FIXED":
                amount = _to_decimal(raw_value, "0")
            elif value_type == "PERCENTAGE":
                pct = _to_decimal(raw_value, "0")
                amount = (gross * pct) / Decimal("100")
            else:  # FORMULA
                expr = str(raw_value or "").strip()
                if expr:
                    try:
                        val = _eval_formula_safely(expr, {**context, **field_values})
                        amount = _to_decimal(val, "0")
                    except Exception:
                        amount = Decimal("0")

        field_values[code] = amount.quantize(Decimal("0.01"))
        context[code] = field_values[code]

        if row.field_type == "EARNING":
            total_earnings += field_values[code]
        elif row.field_type == "DEDUCTION":
            total_deductions += field_values[code]

    net_pay = total_earnings - total_deductions
    return {
        "field_values": {k: str(v) for k, v in field_values.items()},
        "total_earnings": total_earnings.quantize(Decimal("0.01")),
        "total_deductions": total_deductions.quantize(Decimal("0.01")),
        "net_pay": net_pay.quantize(Decimal("0.01")),
    }

