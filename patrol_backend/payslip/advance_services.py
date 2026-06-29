"""Payroll advance ledger: give, recover, outstanding balance, Quick Pay settlement."""
from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.db import transaction
from django.db.models import Sum

from payslip.models import PayrollAdvance, PayrollAdvanceRecovery, QuickPayDisbursement

ADVANCE_RECOVERY_CAP_PCT = Decimal("50")


def _to_decimal(value, default="0"):
    if value is None:
        return Decimal(default)
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except Exception:
        return Decimal(default)


def settlement_month_from_date(d: date) -> str:
    return d.strftime("%Y-%m")


def get_advance_given_total(user_id, settlement_month: str) -> Decimal:
    total = PayrollAdvance.objects.filter(
        user_id=user_id,
        settlement_month=settlement_month,
        status__in=("OPEN", "CLOSED"),
    ).aggregate(total=Sum("amount"))["total"]
    return _to_decimal(total, "0")


def get_advance_recovered_total(user_id, settlement_month: str) -> Decimal:
    total = PayrollAdvanceRecovery.objects.filter(
        advance__user_id=user_id,
        advance__settlement_month=settlement_month,
        advance__status__in=("OPEN", "CLOSED"),
    ).aggregate(total=Sum("amount"))["total"]
    return _to_decimal(total, "0")


def get_outstanding_advance(user_id, settlement_month: str) -> Decimal:
    given = get_advance_given_total(user_id, settlement_month)
    recovered = get_advance_recovered_total(user_id, settlement_month)
    outstanding = given - recovered
    return outstanding if outstanding > Decimal("0") else Decimal("0")


def get_outstanding_advances_bulk(user_ids, settlement_month: str) -> dict:
    """Return {user_id_str: outstanding} for many users using two aggregate queries."""
    if not user_ids:
        return {}
    uid_list = [str(uid) for uid in user_ids]
    given_by_user = {
        str(row["user_id"]): _to_decimal(row["total"], "0")
        for row in PayrollAdvance.objects.filter(
            user_id__in=uid_list,
            settlement_month=settlement_month,
            status__in=("OPEN", "CLOSED"),
        )
        .values("user_id")
        .annotate(total=Sum("amount"))
    }
    recovered_by_user = {
        str(row["advance__user_id"]): _to_decimal(row["total"], "0")
        for row in PayrollAdvanceRecovery.objects.filter(
            advance__user_id__in=uid_list,
            advance__settlement_month=settlement_month,
            advance__status__in=("OPEN", "CLOSED"),
        )
        .values("advance__user_id")
        .annotate(total=Sum("amount"))
    }
    result = {}
    for uid in uid_list:
        given = given_by_user.get(uid, Decimal("0"))
        recovered = recovered_by_user.get(uid, Decimal("0"))
        outstanding = given - recovered
        result[uid] = (
            outstanding.quantize(Decimal("0.01"))
            if outstanding > Decimal("0")
            else Decimal("0")
        )
    return result


def get_quick_paid_total(user_id, settlement_month: str) -> Decimal:
    total = QuickPayDisbursement.objects.filter(
        user_id=user_id,
        settlement_month=settlement_month,
    ).aggregate(total=Sum("net_paid"))["total"]
    return _to_decimal(total, "0")


def suggest_advance_recovery(outstanding_advance, net_before_advance, cap_pct=None) -> Decimal:
    outstanding = _to_decimal(outstanding_advance, "0")
    net_before = _to_decimal(net_before_advance, "0")
    if outstanding <= Decimal("0") or net_before <= Decimal("0"):
        return Decimal("0")
    pct = _to_decimal(cap_pct or ADVANCE_RECOVERY_CAP_PCT, "50")
    cap_amount = (net_before * pct / Decimal("100")).quantize(Decimal("0.01"))
    return min(outstanding, cap_amount).quantize(Decimal("0.01"))


def validate_advance_recovery(outstanding_advance, net_before_advance, recovery_amount) -> Decimal:
    outstanding = _to_decimal(outstanding_advance, "0")
    net_before = _to_decimal(net_before_advance, "0")
    recovery = _to_decimal(recovery_amount, "0")
    if recovery < Decimal("0"):
        raise ValueError("Advance recovery cannot be negative.")
    if net_before <= Decimal("0"):
        if recovery > Decimal("0"):
            raise ValueError("Cannot recover advance when net pay is zero or negative for this period.")
        return Decimal("0")
    if recovery > outstanding:
        raise ValueError("Advance recovery cannot exceed outstanding advance.")
    if recovery > net_before:
        raise ValueError("Advance recovery cannot exceed net pay for this period.")
    return recovery.quantize(Decimal("0.01"))


def _refresh_advance_status(advance: PayrollAdvance):
    recovered = advance.recoveries.aggregate(total=Sum("amount"))["total"] or Decimal("0")
    recovered = _to_decimal(recovered, "0")
    if recovered >= _to_decimal(advance.amount, "0"):
        advance.status = "CLOSED"
    elif advance.status == "CLOSED" and recovered < _to_decimal(advance.amount, "0"):
        advance.status = "OPEN"
    advance.save(update_fields=["status", "modified_on"])


@transaction.atomic
def apply_advance_recoveries_fifo(
    *,
    user_id,
    settlement_month: str,
    recovery_amount: Decimal,
    source_type: str,
    source_id,
    recovery_date: date,
    acting_user,
    notes=None,
):
    """Apply recovery amount against oldest OPEN advances (FIFO)."""
    amount_left = _to_decimal(recovery_amount, "0")
    if amount_left <= Decimal("0"):
        return []

    advances = (
        PayrollAdvance.objects.select_for_update()
        .filter(
            user_id=user_id,
            settlement_month=settlement_month,
            status="OPEN",
        )
        .order_by("advance_date", "created_on")
    )

    created = []
    for advance in advances:
        if amount_left <= Decimal("0"):
            break
        already = advance.recoveries.aggregate(total=Sum("amount"))["total"] or Decimal("0")
        already = _to_decimal(already, "0")
        remaining_on_advance = _to_decimal(advance.amount, "0") - already
        if remaining_on_advance <= Decimal("0"):
            _refresh_advance_status(advance)
            continue
        chunk = min(amount_left, remaining_on_advance).quantize(Decimal("0.01"))
        rec = PayrollAdvanceRecovery.objects.create(
            advance=advance,
            amount=chunk,
            recovery_date=recovery_date,
            source_type=source_type,
            source_id=source_id,
            notes=notes,
            created_by=acting_user,
        )
        created.append(rec)
        amount_left -= chunk
        _refresh_advance_status(advance)

    if amount_left > Decimal("0"):
        raise ValueError("Could not apply full recovery amount against open advances.")

    return created


@transaction.atomic
def create_payroll_advance(
    *,
    user,
    location,
    amount,
    advance_date,
    settlement_month=None,
    payment_mode="cash",
    reference_no=None,
    notes=None,
    acting_user=None,
):
    settlement_month = settlement_month or settlement_month_from_date(advance_date)
    amt = _to_decimal(amount, "0")
    if amt <= Decimal("0"):
        raise ValueError("Advance amount must be greater than zero.")
    return PayrollAdvance.objects.create(
        user=user,
        location=location,
        settlement_month=settlement_month,
        amount=amt,
        advance_date=advance_date,
        payment_mode=payment_mode or "cash",
        reference_no=reference_no or "",
        notes=notes or "",
        status="OPEN",
        created_by=acting_user,
    )


def _field_amount(field_values, *codes):
    for code in codes:
        val = field_values.get(code)
        if val is not None and str(val).strip() != "":
            return _to_decimal(val, "0")
    return Decimal("0")


def build_row_breakdown(calc, outstanding_advance, advance_recovery_amount):
    field_values = calc.get("field_values") or {}
    net_before = _to_decimal(calc.get("net_pay"), "0")
    recovery = _to_decimal(advance_recovery_amount, "0")
    net_paid = (net_before - recovery).quantize(Decimal("0.01"))
    return {
        "gross_earnings": _to_decimal(calc.get("total_earnings"), "0"),
        "total_deductions": _to_decimal(calc.get("total_deductions"), "0"),
        "net_before_advance": net_before,
        "outstanding_advance": _to_decimal(outstanding_advance, "0"),
        "suggested_advance_recovery": suggest_advance_recovery(outstanding_advance, net_before),
        "advance_recovery": recovery,
        "salary": net_paid,
        "net_paid": net_paid,
        "pf": _field_amount(field_values, "PF"),
        "esi": _field_amount(field_values, "ESI"),
        "pt": _field_amount(field_values, "PT"),
        "field_values": field_values,
    }


def _json_safe_mapping(data):
    if not data:
        return {}
    safe = {}
    for key, value in data.items():
        if hasattr(value, "quantize"):
            safe[key] = str(value)
        elif isinstance(value, (dict, list)):
            safe[key] = value
        else:
            safe[key] = value
    return safe


@transaction.atomic
def persist_quick_pay_disbursements(
    *,
    rows,
    location_id,
    settlement_month,
    period_start,
    period_end,
    date_filter,
    export_format,
    acting_user,
    field_config_by_user=None,
    salary_type_by_user=None,
    attendance_by_user=None,
):
    """Save QuickPayDisbursement + advance recoveries for exported rows."""
    field_config_by_user = field_config_by_user or {}
    salary_type_by_user = salary_type_by_user or {}
    attendance_by_user = attendance_by_user or {}
    saved = []

    for row in rows:
        user_id = row.get("user_id")
        recovery = _to_decimal(row.get("advance_recovery"), "0")
        disbursement = QuickPayDisbursement.objects.create(
            user_id=user_id,
            location_id=location_id,
            settlement_month=settlement_month,
            period_start=period_start,
            period_end=period_end,
            date_filter=date_filter,
            salary_type=salary_type_by_user.get(str(user_id), row.get("salary_type") or "monthly"),
            field_config_id=field_config_by_user.get(str(user_id)),
            attendance_snapshot=_json_safe_mapping(
                attendance_by_user.get(str(user_id), row.get("attendance_snapshot"))
            ),
            field_values=_json_safe_mapping(row.get("field_values") or {}),
            gross_earnings=_to_decimal(row.get("gross_earnings"), "0"),
            total_deductions=_to_decimal(row.get("total_deductions"), "0"),
            net_before_advance=_to_decimal(row.get("net_before_advance"), "0"),
            advance_recovery=recovery,
            net_paid=_to_decimal(row.get("net_paid"), "0"),
            export_format=export_format,
            exported_by=acting_user,
        )
        if recovery > Decimal("0"):
            apply_advance_recoveries_fifo(
                user_id=user_id,
                settlement_month=settlement_month,
                recovery_amount=recovery,
                source_type="QUICK_PAY",
                source_id=disbursement.id,
                recovery_date=period_end,
                acting_user=acting_user,
                notes=f"Quick Pay {period_start} to {period_end}",
            )
        saved.append(disbursement)
    return saved


def get_advance_summary(user_id, settlement_month: str) -> dict:
    return {
        "settlement_month": settlement_month,
        "advance_given": str(get_advance_given_total(user_id, settlement_month)),
        "advance_recovered": str(get_advance_recovered_total(user_id, settlement_month)),
        "outstanding_advance": str(get_outstanding_advance(user_id, settlement_month)),
        "quick_paid_total": str(get_quick_paid_total(user_id, settlement_month)),
    }


@transaction.atomic
def reverse_monthly_recoveries(source_id):
    """Remove monthly payslip recoveries before regenerating a record."""
    recoveries = PayrollAdvanceRecovery.objects.filter(
        source_type="MONTHLY_PAYSLIP",
        source_id=source_id,
    ).select_related("advance")
    advances_to_refresh = set()
    for rec in recoveries:
        advances_to_refresh.add(rec.advance_id)
    recoveries.delete()
    for advance_id in advances_to_refresh:
        advance = PayrollAdvance.objects.filter(id=advance_id).first()
        if advance:
            _refresh_advance_status(advance)
