"""Employee Duration Report: one row per employee, total worked minutes in a date range."""

from __future__ import annotations

import logging
from datetime import date
from io import BytesIO
from typing import Any, Dict, List, Optional

from django.db.models import Q, Sum
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authapp.models import User
from dashboard.models import AttendanceCheckin
from dashboard.site_filter import (
    filter_users_queryset_for_site_scope,
    resolve_dashboard_location_id,
)
from patrol_backend.utils.timezone_utils import get_user_today, get_user_timezone_from_request

logger = logging.getLogger(__name__)

_THIN = Border(
    left=Side(style="thin", color="CBD5E1"),
    right=Side(style="thin", color="CBD5E1"),
    top=Side(style="thin", color="CBD5E1"),
    bottom=Side(style="thin", color="CBD5E1"),
)

EXPORT_HEADERS = ["#", "Name", "Emp Code", "Role", "Vendor", "Total Duration"]


def _parse_date(raw) -> Optional[date]:
    if not raw:
        return None
    try:
        return date.fromisoformat(str(raw).strip()[:10])
    except Exception:
        return None


def _parse_hours_to_minutes(raw) -> Optional[int]:
    if raw is None or str(raw).strip() == "":
        return None
    try:
        hours = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if hours < 0:
        return None
    return int(round(hours * 60))


def _fmt_duration(minutes: int) -> str:
    total = max(0, int(minutes or 0))
    return f"{total // 60}h {total % 60}m"


def _is_open_attendance(att) -> bool:
    last_in = att.last_checkin_time or att.checkin_time
    last_out = att.last_checkout_time
    if not last_in:
        return False
    if not last_out:
        return True
    return last_in > last_out


def _attendance_base_qs(request, location_id, start: date, end: date):
    qs = AttendanceCheckin.objects.filter(
        shift_date__gte=start,
        shift_date__lte=end,
        org_location_id=location_id,
    )
    site_id = request.query_params.get("site_id")
    if site_id and str(site_id).lower() not in ("", "all", "null", "undefined"):
        qs = qs.filter(site_id=site_id)
    shift_id = request.query_params.get("shift_id")
    if shift_id and str(shift_id).lower() not in ("", "all", "null", "undefined"):
        qs = qs.filter(shift_id=shift_id)
    return qs


def _employee_base_qs(request, location_id):
    qs = (
        User.objects.filter(
            location_id=location_id,
            is_deleted=False,
            is_active=True,
        )
        .exclude(Q(role__iexact="admin") | Q(is_superuser=True))
        .select_related("vendor")
        .order_by("name", "employee_code")
    )
    qs = filter_users_queryset_for_site_scope(qs, request)

    user_id = request.query_params.get("user") or request.query_params.get("user_id")
    search = (request.query_params.get("search") or "").strip()
    role = (request.query_params.get("role") or "").strip()
    vendor_id = request.query_params.get("vendor_id")

    if user_id and str(user_id).lower() not in ("", "all", "null"):
        qs = qs.filter(id=user_id)
    elif search:
        qs = qs.filter(
            Q(name__icontains=search)
            | Q(employee_code__icontains=search)
            | Q(email__icontains=search)
            | Q(phone__icontains=search)
        )
    if role and role.lower() not in ("", "all"):
        qs = qs.filter(role__iexact=role)
    if vendor_id and str(vendor_id).lower() not in ("", "all"):
        if str(vendor_id).lower() in ("none", "null", "unassigned"):
            qs = qs.filter(vendor_id__isnull=True)
        else:
            qs = qs.filter(vendor_id=vendor_id)
    return qs


def _apply_today_open_adjustments(
    minutes_by_user: Dict[Any, int],
    request,
    location_id,
    start: date,
    end: date,
) -> Dict[Any, int]:
    """Replace today's stored minutes with live shift-today pairing for open sessions."""
    user_tz = get_user_timezone_from_request(request)
    today = get_user_today(user_tz)
    if not (start <= today <= end):
        return minutes_by_user

    from dashboard.views import (
        _attendance_v3_compute_from_logs,
        _attendance_v3_shift_window_utc,
    )

    guard_ids = list(minutes_by_user.keys())
    if not guard_ids:
        return minutes_by_user

    today_qs = (
        _attendance_base_qs(request, location_id, today, today)
        .filter(guard_id__in=guard_ids)
        .select_related("guard", "assignment", "shift", "org_location")
    )

    for att in today_qs.iterator(chunk_size=200):
        if not _is_open_attendance(att):
            continue
        guard = att.guard
        shift = att.shift
        assignment = att.assignment
        org_location = att.org_location
        if not (guard and shift and assignment and org_location and att.shift_date):
            continue
        try:
            search_start_utc, search_end_utc, _, _, _ = _attendance_v3_shift_window_utc(
                att.shift_date,
                shift,
                user_tz,
                location_id=getattr(org_location, "id", None),
                site_id=getattr(att, "site_id", None),
            )
            summary = _attendance_v3_compute_from_logs(
                guard, assignment, shift, org_location, search_start_utc, search_end_utc
            )
            live = int(summary.get("duration_minutes") or 0)
        except Exception:
            logger.exception(
                "employee_duration today open recompute failed user=%s att=%s",
                getattr(guard, "id", "?"),
                getattr(att, "id", "?"),
            )
            continue
        stored = int(att.duration_minutes or 0)
        uid = att.guard_id
        current = int(minutes_by_user.get(uid, 0))
        minutes_by_user[uid] = max(0, current - stored + live)
    return minutes_by_user


def build_employee_duration_rows(request, *, paginate: bool = True) -> Dict[str, Any]:
    location_id = resolve_dashboard_location_id(request)
    if not location_id:
        return {"count": 0, "results": [], "error": "location_id is required"}

    start = _parse_date(request.query_params.get("start_date"))
    end = _parse_date(request.query_params.get("end_date"))
    if not start or not end:
        return {"count": 0, "results": [], "error": "start_date and end_date are required"}
    if end < start:
        start, end = end, start

    users_qs = _employee_base_qs(request, location_id)
    users = list(users_qs)
    if not users:
        return {"count": 0, "results": []}

    user_ids = [u.id for u in users]
    att_qs = _attendance_base_qs(request, location_id, start, end).filter(guard_id__in=user_ids)
    agg = (
        att_qs.values("guard_id")
        .annotate(total_minutes=Sum("duration_minutes"))
    )
    minutes_by_user: Dict[Any, int] = {
        row["guard_id"]: int(row["total_minutes"] or 0) for row in agg
    }
    for uid in user_ids:
        minutes_by_user.setdefault(uid, 0)

    minutes_by_user = _apply_today_open_adjustments(
        minutes_by_user, request, location_id, start, end
    )

    min_minutes = _parse_hours_to_minutes(request.query_params.get("min_duration_hours"))
    max_minutes = _parse_hours_to_minutes(request.query_params.get("max_duration_hours"))
    ignore_zero = str(
        request.query_params.get("ignore_zero_duration")
        or request.query_params.get("exclude_zero")
        or ""
    ).strip().lower() in ("1", "true", "yes", "on")

    rows: List[Dict[str, Any]] = []
    for user in users:
        total = int(minutes_by_user.get(user.id, 0) or 0)
        if ignore_zero and total <= 0:
            continue
        if min_minutes is not None and total < min_minutes:
            continue
        if max_minutes is not None and total > max_minutes:
            continue
        rows.append(
            {
                "user_id": str(user.id),
                "name": user.name or "",
                "employee_code": user.employee_code or "",
                "role": (user.role or "").strip(),
                "vendor_id": str(user.vendor_id) if user.vendor_id else None,
                "vendor_name": getattr(getattr(user, "vendor", None), "name", None) or "",
                "total_duration_minutes": total,
                "total_duration_display": _fmt_duration(total),
            }
        )

    # Stable sort: duration desc then name
    rows.sort(key=lambda r: (-r["total_duration_minutes"], (r["name"] or "").lower()))

    count = len(rows)
    if not paginate:
        return {"count": count, "results": rows}

    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = min(250, max(1, int(request.query_params.get("page_size") or 25)))
    except (TypeError, ValueError):
        page_size = 25
    start_idx = (page - 1) * page_size
    end_idx = start_idx + page_size
    return {"count": count, "results": rows[start_idx:end_idx]}


def generate_employee_duration_excel(rows: List[Dict[str, Any]], label: str = "Employee Duration") -> BytesIO:
    wb = Workbook()
    ws = wb.active
    ws.title = "Employee Duration"
    headers = EXPORT_HEADERS
    ws.append(headers)
    header_font = Font(name="Calibri", size=10, bold=True)
    header_fill = PatternFill(start_color="D9E1F2", end_color="D9E1F2", fill_type="solid")
    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    ws.row_dimensions[1].height = 24
    for col_idx in range(1, len(headers) + 1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = align_center
        cell.border = _THIN

    for idx, row in enumerate(rows, start=1):
        values = [
            idx,
            row.get("name") or "",
            row.get("employee_code") or "",
            (row.get("role") or "").upper(),
            row.get("vendor_name") or "",
            row.get("total_duration_display") or _fmt_duration(row.get("total_duration_minutes") or 0),
        ]
        ws.append(values)
        for col_idx, _ in enumerate(values, start=1):
            cell = ws.cell(row=idx + 1, column=col_idx)
            cell.border = _THIN
            cell.alignment = align_center if col_idx in (1, 6) else align_left

    widths = [6, 28, 14, 14, 22, 14]
    for i, w in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = w

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def generate_employee_duration_pdf(rows: List[Dict[str, Any]], label: str = "Employee Duration") -> BytesIO:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=28, bottomMargin=28)
    styles = getSampleStyleSheet()
    story = [
        Paragraph(label, styles["Heading1"]),
        Spacer(1, 8),
    ]
    data = [EXPORT_HEADERS]
    for idx, row in enumerate(rows, start=1):
        data.append(
            [
                str(idx),
                row.get("name") or "",
                row.get("employee_code") or "",
                (row.get("role") or "").upper(),
                row.get("vendor_name") or "",
                row.get("total_duration_display") or _fmt_duration(row.get("total_duration_minutes") or 0),
            ]
        )
    table = Table(data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E1F2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 8),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#CBD5E1")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ALIGN", (0, 0), (0, -1), "CENTER"),
                ("ALIGN", (5, 0), (5, -1), "CENTER"),
            ]
        )
    )
    story.append(table)
    doc.build(story)
    buf.seek(0)
    return buf


class EmployeeDurationViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]

    def list(self, request):
        data = build_employee_duration_rows(request, paginate=True)
        if data.get("error"):
            return Response({"error": data["error"]}, status=status.HTTP_400_BAD_REQUEST)
        return Response({"count": data["count"], "results": data["results"]})

    @action(detail=False, methods=["get"], url_path="export-excel")
    def export_excel(self, request):
        data = build_employee_duration_rows(request, paginate=False)
        if data.get("error"):
            return Response({"error": data["error"]}, status=status.HTTP_400_BAD_REQUEST)
        buf = generate_employee_duration_excel(data["results"])
        resp = HttpResponse(
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        resp["Content-Disposition"] = (
            f'attachment; filename="employee_duration_{timezone.now().date().isoformat()}.xlsx"'
        )
        return resp

    @action(detail=False, methods=["get"], url_path="export-pdf")
    def export_pdf(self, request):
        data = build_employee_duration_rows(request, paginate=False)
        if data.get("error"):
            return Response({"error": data["error"]}, status=status.HTTP_400_BAD_REQUEST)
        buf = generate_employee_duration_pdf(data["results"])
        resp = HttpResponse(buf.getvalue(), content_type="application/pdf")
        resp["Content-Disposition"] = (
            f'attachment; filename="employee_duration_{timezone.now().date().isoformat()}.pdf"'
        )
        return resp
