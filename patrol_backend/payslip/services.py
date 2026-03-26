import calendar
import ast
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.db.models import Q

from dashboard.models import AttendanceCheckin
from patrol_backend.utils.timezone_utils import convert_date_range_to_utc, to_user_timezone


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
    Current GTMS rule (without LMS):
    - working_days = full month days
    - present/half/absent derived from AttendanceCheckin.pa_status
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
    half_days = Decimal("0")
    absent_days = Decimal("0")

    current = month_start
    while current <= month_end:
        st = (day_status.get(current) or {}).get("pa_status")
        if st == "P":
            present_days += Decimal("1")
        elif st == "HA":
            half_days += Decimal("1")
        elif st == "A":
            absent_days += Decimal("1")
        else:
            # no master entry/mark -> absent under current business rule
            absent_days += Decimal("1")
        current += timedelta(days=1)

    working_days = Decimal(str(month_days))
    paid_days = present_days + (half_days * Decimal("0.5"))

    return {
        "month_days": month_days,
        "working_days": working_days,
        "present_days": present_days,
        "half_days": half_days,
        "absent_days": absent_days,
        "paid_days": paid_days,
    }


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


def calculate_salary_fields(gross_salary, field_rows, attendance_snapshot):
    """
    Simple field engine:
    - FIXED: numeric value
    - PERCENTAGE: % of gross_salary
    - FORMULA: python expression referencing computed fields + attendance vars
    """
    gross = _to_decimal(gross_salary, "0")
    context = {
        "gross_salary": gross,
        "month_days": _to_decimal(attendance_snapshot.get("month_days"), "0"),
        "working_days": _to_decimal(attendance_snapshot.get("working_days"), "0"),
        "present_days": _to_decimal(attendance_snapshot.get("present_days"), "0"),
        "half_days": _to_decimal(attendance_snapshot.get("half_days"), "0"),
        "absent_days": _to_decimal(attendance_snapshot.get("absent_days"), "0"),
        "paid_days": _to_decimal(attendance_snapshot.get("paid_days"), "0"),
    }
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

