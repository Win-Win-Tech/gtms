"""
Auto-checkout reporting service: query filtering, dashboard serialization,
Excel (.xlsx) generation with openpyxl, and PDF (.pdf) generation with ReportLab.
"""

from __future__ import annotations

import io
import logging
from datetime import datetime, time
from typing import Optional

from django.db.models import Q
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from dashboard.models import CheckInLog
from patrol_backend.utils.timezone_utils import (
    from_user_timezone,
    get_user_timezone,
    to_user_timezone,
)
from .ws_groups import _is_superadmin

logger = logging.getLogger(__name__)


def filter_auto_checkout_queryset(request, user):
    """
    Filter CheckInLog rows where type='checkout' and is_auto_checkout=True.
    Applies organization scoping and user request query parameters.
    """
    qs = (
        CheckInLog.objects.filter(type="checkout", is_auto_checkout=True)
        .select_related("guard", "site", "org_location", "shift", "assignment")
        .order_by("-timestamp")
    )

    is_super = _is_superadmin(user)
    user_location_id = getattr(user, "location_id", None)

    # Org scoping for non-superadmin
    if not is_super:
        if user_location_id:
            qs = qs.filter(org_location_id=user_location_id)
        else:
            return qs.none()

    # Query param filters
    location_id = request.query_params.get("location_id")
    if is_super and location_id and location_id not in ("All", "all", "null"):
        qs = qs.filter(org_location_id=location_id)

    site_id = request.query_params.get("site_id")
    if site_id and site_id not in ("All", "all", "null"):
        qs = qs.filter(site_id=site_id)

    guard_id = (
        request.query_params.get("guard_id")
        or request.query_params.get("user_id")
        or request.query_params.get("user")
    )
    if guard_id and guard_id not in ("All", "all", "null"):
        qs = qs.filter(guard_id=guard_id)

    role = request.query_params.get("role")
    if role and role not in ("All", "all", "null"):
        qs = qs.filter(guard__role__iexact=role)

    search = request.query_params.get("search", "").strip()
    if search:
        qs = qs.filter(
            Q(guard__name__icontains=search)
            | Q(guard__email__icontains=search)
            | Q(guard__phone__icontains=search)
            | Q(guard__employee_code__icontains=search)
            | Q(site__name__icontains=search)
            | Q(auto_checkout_reason__icontains=search)
        )

    # Date range filtering
    start_date_str = request.query_params.get("start_date")
    end_date_str = request.query_params.get("end_date")
    user_tz = get_user_timezone(user)

    if start_date_str:
        try:
            start_d = datetime.strptime(start_date_str.strip(), "%Y-%m-%d").date()
            start_dt = from_user_timezone(datetime.combine(start_d, time.min), user_tz)
            qs = qs.filter(timestamp__gte=start_dt)
        except (ValueError, TypeError):
            pass

    if end_date_str:
        try:
            end_d = datetime.strptime(end_date_str.strip(), "%Y-%m-%d").date()
            end_dt = from_user_timezone(datetime.combine(end_d, time.max), user_tz)
            qs = qs.filter(timestamp__lte=end_dt)
        except (ValueError, TypeError):
            pass

    return qs


def serialize_auto_checkout_log(log: CheckInLog, requesting_user) -> dict:
    """Format CheckInLog into JSON dictionary for web dashboard."""
    guard = log.guard
    site = log.site
    org_loc = log.org_location
    shift = log.shift

    local_dt = to_user_timezone(log.timestamp, requesting_user)
    timestamp_str = local_dt.strftime("%Y-%m-%d %H:%M:%S") if local_dt else ""

    return {
        "id": log.id,
        "timestamp": timestamp_str,
        "timestamp_iso": local_dt.isoformat() if local_dt else "",
        "guard_id": str(guard.id) if guard else None,
        "guard_name": getattr(guard, "name", None) or getattr(guard, "email", "Unknown"),
        "employee_code": getattr(guard, "employee_code", "") or "",
        "guard_email": getattr(guard, "email", ""),
        "guard_phone": getattr(guard, "phone", "") or "",
        "role": getattr(guard, "role", "guard"),
        "site_id": str(site.id) if site else None,
        "site_name": site.name if site else "—",
        "org_location_id": str(org_loc.id) if org_loc else None,
        "org_location_name": org_loc.name if org_loc else "—",
        "shift_name": (getattr(shift, "name", None) or getattr(shift, "shift_name", None) or "—") if shift else "—",
        "latitude": float(log.latitude) if log.latitude is not None else None,
        "longitude": float(log.longitude) if log.longitude is not None else None,
        "is_auto_checkout": bool(log.is_auto_checkout),
        "auto_checkout_reason": log.auto_checkout_reason or "Boundary breach: moved outside site boundary",
    }


def build_auto_checkout_summary(qs) -> dict:
    """KPI summary cards for dashboard and export headers."""
    total = qs.count()
    unique_guards = qs.values("guard_id").distinct().count()
    unique_sites = qs.values("site_id").exclude(site_id__isnull=True).distinct().count()
    return {
        "total_auto_checkouts": total,
        "unique_guards": unique_guards,
        "unique_sites": unique_sites,
    }


# =============================================================================
# EXCEL GENERATOR (openpyxl)
# =============================================================================

def generate_auto_checkout_excel(queryset, requesting_user, context: Optional[dict] = None) -> io.BytesIO:
    """Produce formatted Excel workbook of auto-checkout events."""
    wb = Workbook()
    ws = wb.active
    ws.title = "Auto-Checkouts"
    ws.views.sheetView[0].showGridLines = True

    # Styling Palette
    brand_fill = PatternFill(start_color="1E293B", end_color="1E293B", fill_type="solid") # Dark slate
    header_fill = PatternFill(start_color="4338CA", end_color="4338CA", fill_type="solid") # Indigo
    zebra_fill = PatternFill(start_color="F8FAFC", end_color="F8FAFC", fill_type="solid")
    total_fill = PatternFill(start_color="EEF2F6", end_color="EEF2F6", fill_type="solid")

    font_title = Font(name="Calibri", size=15, bold=True, color="FFFFFF")
    font_sub = Font(name="Calibri", size=10, italic=True, color="CBD5E1")
    font_header = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    font_data = Font(name="Calibri", size=10)
    font_data_bold = Font(name="Calibri", size=10, bold=True)

    border_thin = Border(
        left=Side(style="thin", color="CBD5E1"),
        right=Side(style="thin", color="CBD5E1"),
        top=Side(style="thin", color="CBD5E1"),
        bottom=Side(style="thin", color="CBD5E1"),
    )

    align_center = Alignment(horizontal="center", vertical="center")
    align_left = Alignment(horizontal="left", vertical="center")
    align_right = Alignment(horizontal="right", vertical="center")

    now_local = to_user_timezone(timezone.now(), requesting_user)
    generated_at_str = now_local.strftime("%d/%m/%Y %H:%M") if now_local else ""

    ctx = context or {}
    loc_name = ctx.get("location_name", "All Locations")
    period_str = ctx.get("period_display", "All Time")

    # Title Banner (Rows 1-2)
    ws.merge_cells("A1:J1")
    ws["A1"] = "SMART PATROL - BOUNDARY AUTO-CHECKOUT REPORT"
    ws["A1"].font = font_title
    ws["A1"].fill = brand_fill
    ws["A1"].alignment = align_center

    ws.merge_cells("A2:J2")
    ws["A2"] = f"Location: {loc_name}  |  Period: {period_str}  |  Generated: {generated_at_str}"
    ws["A2"].font = font_sub
    ws["A2"].fill = brand_fill
    ws["A2"].alignment = align_center

    ws.row_dimensions[1].height = 28
    ws.row_dimensions[2].height = 20

    # Summary Row (Row 4)
    summary = build_auto_checkout_summary(queryset)
    ws["A4"] = "Summary KPIs:"
    ws["A4"].font = font_data_bold
    ws["B4"] = f"Total Auto-Checkouts: {summary['total_auto_checkouts']}"
    ws["B4"].font = font_data_bold
    ws["D4"] = f"Guards Affected: {summary['unique_guards']}"
    ws["D4"].font = font_data_bold
    ws["F4"] = f"Sites Affected: {summary['unique_sites']}"
    ws["F4"].font = font_data_bold

    headers = [
        ("S.No", 8, align_center),
        ("Date & Time", 20, align_center),
        ("Guard Name", 24, align_left),
        ("Emp Code", 16, align_center),
        ("Role", 14, align_center),
        ("Location", 22, align_left),
        ("Site Name", 24, align_left),
        ("Shift", 16, align_left),
        ("Latitude", 14, align_right),
        ("Longitude", 14, align_right),
    ]

    header_row = 6
    ws.row_dimensions[header_row].height = 24

    for col_idx, (title, width, alignment) in enumerate(headers, start=1):
        cell = ws.cell(row=header_row, column=col_idx, value=title)
        cell.font = font_header
        cell.fill = header_fill
        cell.alignment = alignment
        cell.border = border_thin
        ws.column_dimensions[get_column_letter(col_idx)].width = width

    current_row = header_row + 1
    s_no = 1

    for log in queryset:
        local_time = to_user_timezone(log.timestamp, requesting_user)
        time_str = local_time.strftime("%d/%m/%Y %H:%M:%S") if local_time else "—"

        guard = log.guard
        guard_name = getattr(guard, "name", None) or getattr(guard, "email", "Unknown")
        emp_code = getattr(guard, "employee_code", "") or "—"
        role = getattr(guard, "role", "guard").upper()
        location_name = log.org_location.name if log.org_location else "—"
        site_name = log.site.name if log.site else "—"
        shift_name = (getattr(log.shift, "name", None) or getattr(log.shift, "shift_name", None) or "—") if log.shift else "—"
        lat_val = f"{float(log.latitude):.6f}" if log.latitude is not None else "—"
        lng_val = f"{float(log.longitude):.6f}" if log.longitude is not None else "—"

        row_values = [
            (s_no, align_center),
            (time_str, align_center),
            (guard_name, align_left),
            (emp_code, align_center),
            (role, align_center),
            (location_name, align_left),
            (site_name, align_left),
            (shift_name, align_left),
            (lat_val, align_right),
            (lng_val, align_right),
        ]

        fill_color = zebra_fill if s_no % 2 == 0 else None
        ws.row_dimensions[current_row].height = 20

        for col_idx, (val, alignment) in enumerate(row_values, start=1):
            cell = ws.cell(row=current_row, column=col_idx, value=val)
            cell.font = font_data
            cell.alignment = alignment
            cell.border = border_thin
            if fill_color:
                cell.fill = fill_color

        current_row += 1
        s_no += 1

    if s_no == 1:
        # Empty row
        ws.merge_cells(f"A{current_row}:K{current_row}")
        empty_cell = ws.cell(row=current_row, column=1, value="No auto-checkout events found for the selected filters.")
        empty_cell.font = font_data
        empty_cell.alignment = align_center
        empty_cell.border = border_thin

    output = io.BytesIO()
    wb.save(output)
    output.seek(0)
    return output


# =============================================================================
# PDF GENERATOR (ReportLab)
# =============================================================================

def generate_auto_checkout_pdf(queryset, requesting_user, context: Optional[dict] = None) -> io.BytesIO:
    """Produce formatted landscape PDF report of auto-checkout events."""
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=landscape(A4),
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        topMargin=0.4 * inch,
        bottomMargin=0.4 * inch,
    )

    ctx = context or {}
    loc_name = ctx.get("location_name", "All Locations")
    period_str = ctx.get("period_display", "All Time")
    summary = build_auto_checkout_summary(queryset)

    now_local = to_user_timezone(timezone.now(), requesting_user)
    generated_at_str = now_local.strftime("%d/%m/%Y %H:%M") if now_local else ""

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "AutoTitle",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=15,
        leading=18,
        textColor=colors.HexColor("#1e293b"),
        alignment=TA_LEFT,
    )

    sub_style = ParagraphStyle(
        "AutoSub",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=9,
        leading=12,
        textColor=colors.HexColor("#64748b"),
        alignment=TA_LEFT,
    )

    kpi_style = ParagraphStyle(
        "AutoKPI",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#1e293b"),
        alignment=TA_CENTER,
    )

    th_style = ParagraphStyle(
        "AutoTH",
        parent=styles["Normal"],
        fontName="Helvetica-Bold",
        fontSize=8,
        leading=10,
        textColor=colors.white,
        alignment=TA_CENTER,
    )

    td_style = ParagraphStyle(
        "AutoTD",
        parent=styles["Normal"],
        fontName="Helvetica",
        fontSize=7.5,
        leading=9.5,
        textColor=colors.HexColor("#334155"),
        alignment=TA_LEFT,
    )

    td_center = ParagraphStyle(
        "AutoTDCenter",
        parent=td_style,
        alignment=TA_CENTER,
    )

    story = []

    # Title & Metadata Header
    header_data = [
        [
            Paragraph("<b>SMART PATROL — BOUNDARY AUTO-CHECKOUT REPORT</b>", title_style),
            Paragraph(f"<b>Printed:</b> {generated_at_str}", ParagraphStyle("PRT", parent=sub_style, alignment=TA_RIGHT)),
        ],
        [
            Paragraph(f"<b>Location:</b> {loc_name}  |  <b>Period:</b> {period_str}", sub_style),
            Paragraph(f"<b>Total Events:</b> {summary['total_auto_checkouts']}", ParagraphStyle("TOT", parent=sub_style, alignment=TA_RIGHT)),
        ],
    ]
    header_table = Table(header_data, colWidths=[doc.width * 0.7, doc.width * 0.3])
    header_table.setStyle(
        TableStyle([
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
            ("TOPPADDING", (0, 0), (-1, -1), 1),
            ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ])
    )
    story.append(header_table)
    story.append(Spacer(1, 8))

    # KPI Banner Table
    kpi_data = [
        [
            Paragraph(f"<b>TOTAL AUTO-CHECKOUTS:</b> {summary['total_auto_checkouts']}", kpi_style),
            Paragraph(f"<b>GUARDS AFFECTED:</b> {summary['unique_guards']}", kpi_style),
            Paragraph(f"<b>SITES AFFECTED:</b> {summary['unique_sites']}", kpi_style),
        ]
    ]
    kpi_table = Table(kpi_data, colWidths=[doc.width / 3.0] * 3)
    kpi_table.setStyle(
        TableStyle([
            ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
            ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ])
    )
    story.append(kpi_table)
    story.append(Spacer(1, 10))

    # Data Table
    table_headers = [
        Paragraph("<b>#</b>", th_style),
        Paragraph("<b>Date & Time</b>", th_style),
        Paragraph("<b>Guard</b>", th_style),
        Paragraph("<b>Role</b>", th_style),
        Paragraph("<b>Location</b>", th_style),
        Paragraph("<b>Site Name</b>", th_style),
        Paragraph("<b>Shift</b>", th_style),
        Paragraph("<b>Coordinates</b>", th_style),
    ]

    col_widths = [
        0.40 * inch,  # #
        1.40 * inch,  # Date & Time
        2.00 * inch,  # Guard
        0.90 * inch,  # Role
        1.80 * inch,  # Location
        1.99 * inch,  # Site
        1.20 * inch,  # Shift
        1.20 * inch,  # Coordinates
    ]

    table_data = [table_headers]
    s_no = 1

    for log in queryset:
        local_time = to_user_timezone(log.timestamp, requesting_user)
        time_str = local_time.strftime("%d/%m/%Y %H:%M") if local_time else "—"

        guard = log.guard
        guard_name = getattr(guard, "name", None) or getattr(guard, "email", "Unknown")
        emp_code = getattr(guard, "employee_code", "") or "—"
        guard_html = f"<b>{guard_name}</b><br/><font color='#64748b' size=6.5>{emp_code}</font>"
        role = getattr(guard, "role", "guard").upper()
        location_name = log.org_location.name if log.org_location else "—"
        site_name = log.site.name if log.site else "—"
        shift_name = (getattr(log.shift, "name", None) or getattr(log.shift, "shift_name", None) or "—") if log.shift else "—"

        coords = "—"
        if log.latitude is not None and log.longitude is not None:
            coords = f"{float(log.latitude):.4f}, {float(log.longitude):.4f}"

        table_data.append([
            Paragraph(str(s_no), td_center),
            Paragraph(time_str, td_center),
            Paragraph(guard_html, td_style),
            Paragraph(role, td_center),
            Paragraph(location_name, td_style),
            Paragraph(site_name, td_style),
            Paragraph(shift_name, td_style),
            Paragraph(coords, td_center),
        ])
        s_no += 1

    if s_no == 1:
        table_data.append([
            Paragraph("No auto-checkout events recorded.", td_center),
            "", "", "", "", "", "", ""
        ])

    data_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    table_style = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4338ca")),
        ("ALIGN", (0, 0), (-1, 0), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 3),
        ("RIGHTPADDING", (0, 0), (-1, -1), 3),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
    ]

    for i in range(1, len(table_data)):
        if i % 2 == 0:
            table_style.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#f8fafc")))

    if s_no == 1:
        table_style.append(("SPAN", (0, 1), (-1, 1)))

    data_table.setStyle(TableStyle(table_style))
    story.append(data_table)

    def _add_footer(canvas, d):
        canvas.saveState()
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawString(d.leftMargin, 0.25 * inch, "Smart Patrol — Confidential")
        canvas.drawRightString(d.leftMargin + d.width, 0.25 * inch, f"Page {canvas.getPageNumber()}")
        canvas.restoreState()

    doc.build(story, onFirstPage=_add_footer, onLaterPages=_add_footer)
    buf.seek(0)
    return buf
