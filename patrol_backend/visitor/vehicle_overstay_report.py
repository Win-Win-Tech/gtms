"""Vehicle overstay report: still-inside and completed stays past threshold."""

from __future__ import annotations

from datetime import timedelta
from io import BytesIO

from django.db.models import DurationField, ExpressionWrapper, F, Q
from django.http import HttpResponse
from django.utils import timezone as dj_timezone
from openpyxl import Workbook
from openpyxl.styles import Font

from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    to_user_timezone,
)

from .anpr.gate import normalize_plate
from .lookup_options import resolve_label
from .models import VehicleOverstayWhitelist, VisitorEntry
from .overstay import get_overstay_hours
from .vehicle_movement_report import (
    PDF_FOOTER_TEXT,
    resolve_vehicle_movement_window,
)

OVERSTAY_HEADERS = [
    "S.No",
    "Visit Date",
    "Vehicle Number",
    "Vehicle Type",
    "Visitor Name",
    "Phone",
    "Site",
    "Check In",
    "Check Out",
    "Duration (h)",
    "Status",
]


def _parse_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("1", "true", "yes", "y", "on"):
        return True
    if text in ("0", "false", "no", "n", "off"):
        return False
    return default


def _whitelisted_plates(location_id):
    return set(
        VehicleOverstayWhitelist.objects.filter(
            location_id=location_id,
            is_deleted=False,
        ).values_list("vehicle_number", flat=True)
    )


def _fmt_dt(dt, user_tz):
    if not dt:
        return "—"
    local = to_user_timezone(dt, user_tz)
    return local.strftime("%d/%m/%Y %H:%M") if local else "—"


def _fmt_visit_date(visit_date, check_in_time, user_tz):
    if visit_date:
        return visit_date.strftime("%d/%m/%Y")
    if check_in_time:
        local = to_user_timezone(check_in_time, user_tz)
        return local.strftime("%d/%m/%Y") if local else "—"
    return "—"


def _duration_hours(start, end):
    if not start or not end or end < start:
        return 0.0
    return round((end - start).total_seconds() / 3600.0, 2)


def _vehicle_type_label(code, location_id):
    if location_id and code:
        return resolve_label(location_id, "vehicle_type", code)
    text = (code or "").replace("_", " ").title()
    return text or "—"


def _display_visitor_name(visitor, plate):
    """Omit plate-as-name placeholders (ANPR/CCTV stores plate in visitor_name)."""
    name = (getattr(visitor, "visitor_name", None) or "").strip() if visitor else ""
    if not name:
        return ""
    name_norm = "".join(name.upper().split())
    plate_norm = "".join((plate or "").upper().split())
    if plate_norm and name_norm == plate_norm:
        return ""
    if plate and normalize_plate(name) == plate:
        return ""
    return name


def _row_from_entry(entry, *, location_id, user_tz, wl_plates, status, status_label, end_point=None):
    plate = normalize_plate(entry.vehicle_number) or (entry.vehicle_number or "").strip().upper()
    is_wl = plate in wl_plates
    visitor = entry.visitor
    check_out = entry.check_out_time
    duration_end = end_point if end_point is not None else check_out
    return {
        "id": str(entry.id),
        "vehicle_number": plate,
        "vehicle_type": entry.vehicle_type or "",
        "vehicle_type_label": _vehicle_type_label(entry.vehicle_type, location_id),
        "visitor_name": _display_visitor_name(visitor, plate),
        "phone_number": getattr(visitor, "phone_number", "") or "",
        "site_id": str(entry.site_id) if entry.site_id else None,
        "site_name": getattr(entry.site, "name", None) or "—",
        "visit_date": entry.visit_date.isoformat() if entry.visit_date else None,
        "visit_date_display": _fmt_visit_date(entry.visit_date, entry.check_in_time, user_tz),
        "check_in_time": entry.check_in_time.isoformat() if entry.check_in_time else None,
        "check_out_time": check_out.isoformat() if check_out else None,
        "check_in_display": _fmt_dt(entry.check_in_time, user_tz),
        "check_out_display": _fmt_dt(check_out, user_tz) if check_out else "—",
        "duration_hours": _duration_hours(entry.check_in_time, duration_end),
        "status": status,
        "status_label": status_label,
        "whitelisted": is_wl,
    }


def build_vehicle_overstay_report(
    request,
    location_id,
    *,
    date_filter="today",
    start_date_str=None,
    end_date_str=None,
    start_time_str=None,
    end_time_str=None,
    site_id=None,
    include_whitelist=False,
    search=None,
    status_filter=None,
    vehicle_type=None,
):
    """
    Rows where stay duration >= vehicle_overstay_hours:
    - still checked_in (no checkout), duration until now
    - checked_out, duration check_out - check_in
    """
    from scheduler.models import Location

    location = Location.objects.filter(id=location_id).first()
    location_name = location.name if location else ""

    # Date-only custom range: full calendar days (no time pickers on this report).
    if (date_filter or "").lower() == "custom":
        start_time_str = None
        end_time_str = None

    window = resolve_vehicle_movement_window(
        request,
        location_id,
        date_filter,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        start_time_str=start_time_str,
        end_time_str=end_time_str,
    )
    start_utc = window.get("start_utc")
    end_utc = window.get("end_utc")
    now = dj_timezone.now()
    as_of = end_utc if end_utc and end_utc < now else now

    hours = get_overstay_hours(location_id)
    threshold = timedelta(hours=hours)
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    wl_plates = _whitelisted_plates(location_id)

    status_key = (status_filter or "all").strip().lower()
    if status_key not in ("all", "still_inside", "checked_out"):
        status_key = "all"
    vehicle_type_key = (vehicle_type or "").strip()
    if vehicle_type_key.lower() in ("", "all", "all_vehicle_types"):
        vehicle_type_key = ""
    search_q = (search or "").strip()

    base = (
        VisitorEntry.objects.filter(
            location_id=location_id,
            is_deleted=False,
            check_in_time__isnull=False,
        )
        .exclude(Q(vehicle_number__isnull=True) | Q(vehicle_number=""))
        .select_related("visitor", "site")
    )
    if site_id:
        base = base.filter(site_id=site_id)
    if vehicle_type_key:
        base = base.filter(vehicle_type=vehicle_type_key)
    if search_q:
        base = base.filter(
            Q(vehicle_number__icontains=search_q)
            | Q(visitor__visitor_name__icontains=search_q)
            | Q(visitor__phone_number__icontains=search_q)
        )

    still_rows = []
    if status_key in ("all", "still_inside"):
        still_qs = base.filter(
            status=VisitorEntry.STATUS_CHECKED_IN,
            check_out_time__isnull=True,
            check_in_time__lte=as_of,
        )
        if end_utc:
            still_qs = still_qs.filter(check_in_time__lte=end_utc)

        for entry in still_qs.iterator(chunk_size=200):
            end_point = as_of
            if entry.check_in_time > end_point:
                continue
            if end_point - entry.check_in_time < threshold:
                continue
            plate = normalize_plate(entry.vehicle_number) or (entry.vehicle_number or "").strip().upper()
            is_wl = plate in wl_plates
            if is_wl and not include_whitelist:
                continue
            still_rows.append(
                _row_from_entry(
                    entry,
                    location_id=location_id,
                    user_tz=user_tz,
                    wl_plates=wl_plates,
                    status="still_inside",
                    status_label="Still inside",
                    end_point=end_point,
                )
            )

    done_rows = []
    if status_key in ("all", "checked_out"):
        done_qs = base.filter(
            check_out_time__isnull=False,
        ).annotate(
            stay=ExpressionWrapper(
                F("check_out_time") - F("check_in_time"),
                output_field=DurationField(),
            )
        ).filter(stay__gte=threshold)

        if start_utc and end_utc:
            done_qs = done_qs.filter(
                check_in_time__lte=end_utc,
                check_out_time__gte=start_utc,
            )
        elif end_utc:
            done_qs = done_qs.filter(check_in_time__lte=end_utc)
        elif start_utc:
            done_qs = done_qs.filter(check_out_time__gte=start_utc)

        for entry in done_qs.iterator(chunk_size=200):
            plate = normalize_plate(entry.vehicle_number) or (entry.vehicle_number or "").strip().upper()
            is_wl = plate in wl_plates
            if is_wl and not include_whitelist:
                continue
            done_rows.append(
                _row_from_entry(
                    entry,
                    location_id=location_id,
                    user_tz=user_tz,
                    wl_plates=wl_plates,
                    status="checked_out",
                    status_label="Checked out",
                )
            )

    rows = still_rows + done_rows
    rows.sort(
        key=lambda r: r.get("check_in_time") or "",
        reverse=True,
    )

    return {
        "location_id": str(location_id),
        "location_name": location_name,
        "overstay_hours": hours,
        "include_whitelist": bool(include_whitelist),
        "search": search_q,
        "status_filter": status_key,
        "vehicle_type": vehicle_type_key or None,
        "date_filter": window["date_filter"],
        "date_range_display": window["date_range_display"],
        "time_from": window["time_from"],
        "time_to": window["time_to"],
        "show_time": False,
        "start_date": window.get("start_date"),
        "end_date": window.get("end_date"),
        "rows": rows,
        "total": len(rows),
        "total_still_inside": len(still_rows),
        "total_checked_out": len(done_rows),
    }


def generate_vehicle_overstay_excel(report_data):
    wb = Workbook()
    ws = wb.active
    ws.title = "Vehicle Overstay"

    ws.append(OVERSTAY_HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for idx, row in enumerate(report_data.get("rows") or [], start=1):
        ws.append(
            [
                idx,
                row.get("visit_date_display") or "",
                row.get("vehicle_number") or "",
                row.get("vehicle_type_label") or "",
                row.get("visitor_name") or "",
                row.get("phone_number") or "",
                row.get("site_name") or "",
                row.get("check_in_display") or "",
                row.get("check_out_display") or "",
                row.get("duration_hours") or 0,
                row.get("status_label") or "",
            ]
        )

    for col_idx, width in enumerate([8, 12, 16, 14, 20, 14, 16, 18, 18, 12, 14], start=1):
        ws.column_dimensions[chr(64 + col_idx)].width = width

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    date_slug = (report_data.get("start_date") or "report").replace("-", "")
    filename = f"vehicle_overstay_{date_slug}.xlsx"
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def _draw_overstay_pdf_header_and_footer(canvas, doc):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, Table, TableStyle

    canvas.saveState()
    canvas.setFont("Helvetica-Oblique", 8)
    canvas.setFillColor(colors.HexColor("#64748b"))
    canvas.drawCentredString(
        doc.leftMargin + doc.width / 2.0,
        0.15 * inch,
        PDF_FOOTER_TEXT,
    )
    canvas.drawRightString(
        doc.leftMargin + doc.width,
        0.15 * inch,
        f"Page {canvas.getPageNumber()}",
    )

    styles = getSampleStyleSheet()

    def _para_style(name, **kwargs):
        base = dict(leftIndent=0, rightIndent=0, firstLineIndent=0, spaceBefore=0, spaceAfter=0)
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    title_style = _para_style(
        "OsHeaderTitle",
        parent=styles["Normal"],
        fontSize=11,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )
    meta_style = _para_style(
        "OsMeta",
        parent=styles["Normal"],
        fontSize=8,
        alignment=TA_LEFT,
    )
    meta_right = _para_style(
        "OsMetaR",
        parent=styles["Normal"],
        fontSize=8,
        alignment=TA_RIGHT,
    )

    org_name = getattr(doc, "org_name", "—")
    range_disp = getattr(doc, "date_range_display", "") or ""
    hours = getattr(doc, "overstay_hours", "")
    printed_at = getattr(doc, "printed_at", None)
    printed = printed_at.strftime("%d/%m/%Y %H:%M") if printed_at else "—"

    top_y = doc.height + doc.bottomMargin + doc.topMargin
    title_p = Paragraph("Vehicle Overstay Report", title_style)
    title_p.wrap(doc.width, doc.topMargin)
    title_p.drawOn(canvas, doc.leftMargin, top_y - 16)

    left = Paragraph(
        f"<b>Organisation:</b> {org_name}<br/>"
        f"<b>Period:</b> {range_disp}<br/>"
        f"<b>Threshold:</b> {hours}h",
        meta_style,
    )
    right = Paragraph(f"<b>Printed:</b> {printed}", meta_right)
    header_meta = Table([[left, right]], colWidths=[doc.width * 0.65, doc.width * 0.35])
    header_meta.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )
    header_meta.wrap(doc.width, doc.topMargin)
    header_meta.drawOn(canvas, doc.leftMargin, top_y - 70)

    canvas.setStrokeColor(colors.HexColor("#cbd5e1"))
    canvas.setLineWidth(0.75)
    canvas.line(doc.leftMargin, top_y - 76, doc.leftMargin + doc.width, top_y - 76)
    canvas.restoreState()


def generate_vehicle_overstay_pdf(report_data, request=None):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    org_name = report_data.get("location_name") or "—"
    printed_at = None
    if request is not None:
        user_tz = get_user_timezone_from_request(
            request, location_id=report_data.get("location_id")
        )
        printed_at = to_user_timezone(dj_timezone.now(), user_tz)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0.4 * inch,
        rightMargin=0.4 * inch,
        topMargin=1.15 * inch,
        bottomMargin=0.4 * inch,
    )
    doc.org_name = org_name
    doc.date_range_display = report_data.get("date_range_display") or ""
    doc.overstay_hours = report_data.get("overstay_hours")
    doc.printed_at = printed_at

    styles = getSampleStyleSheet()

    def _para_style(name, **kwargs):
        base = dict(leftIndent=0, rightIndent=0, firstLineIndent=0, spaceBefore=0, spaceAfter=0)
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    cell_style = _para_style("OsCell", parent=styles["Normal"], fontSize=7.5, alignment=TA_LEFT)
    header_style = _para_style(
        "OsHead",
        parent=styles["Normal"],
        fontSize=7.5,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
    )

    data = [[Paragraph(h, header_style) for h in OVERSTAY_HEADERS]]
    for idx, row in enumerate(report_data.get("rows") or [], start=1):
        data.append(
            [
                Paragraph(str(idx), cell_style),
                Paragraph(str(row.get("visit_date_display") or ""), cell_style),
                Paragraph(str(row.get("vehicle_number") or ""), cell_style),
                Paragraph(str(row.get("vehicle_type_label") or ""), cell_style),
                Paragraph(str(row.get("visitor_name") or ""), cell_style),
                Paragraph(str(row.get("phone_number") or ""), cell_style),
                Paragraph(str(row.get("site_name") or ""), cell_style),
                Paragraph(str(row.get("check_in_display") or ""), cell_style),
                Paragraph(str(row.get("check_out_display") or ""), cell_style),
                Paragraph(str(row.get("duration_hours") or ""), cell_style),
                Paragraph(str(row.get("status_label") or ""), cell_style),
            ]
        )

    col_widths = [
        0.45 * inch,
        0.75 * inch,
        0.95 * inch,
        0.8 * inch,
        1.1 * inch,
        0.85 * inch,
        0.9 * inch,
        1.0 * inch,
        1.0 * inch,
        0.65 * inch,
        0.8 * inch,
    ]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#7C3AED")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("FONTSIZE", (0, 0), (-1, -1), 7.5),
                ("ALIGN", (0, 0), (-1, 0), "CENTER"),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#e2e8f0")),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f8fafc")]),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )

    doc.build([table], onFirstPage=_draw_overstay_pdf_header_and_footer, onLaterPages=_draw_overstay_pdf_header_and_footer)
    date_slug = (report_data.get("start_date") or "report").replace("-", "")
    filename = f"vehicle_overstay_{date_slug}.pdf"
    return buffer.getvalue(), filename
