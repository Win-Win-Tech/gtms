"""Check-in report PDF — same columns as Excel, dashboard header/footer style."""
from datetime import datetime, timedelta
from io import BytesIO

from django.utils import timezone

from patrol_backend.utils.pdf_report import draw_dashboard_pdf_header_and_footer
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    to_user_timezone,
)

CHECKIN_PDF_HEADERS = [
    "Date",
    "Name",
    "Emp Code",
    "Designation",
    "Shift Name",
    "Checkpoint Name",
    "Site",
    "Expected Time",
    "Actual Check-In Time",
    "Status",
    "Delay (minutes)",
    "Has Checklist",
    "Checklist",
    "Checklist Remarks",
]


def _format_dt(value):
    if not value:
        return ""
    if hasattr(value, "strftime"):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)


def _format_checklist_cell(item):
    answers = item.get("checklist_answers")
    if isinstance(answers, list) and answers:
        lines = []
        for answer in answers:
            label = (answer or {}).get("label") or "Item"
            checked = (answer or {}).get("checked") is True
            lines.append(f"{label} : {'Yes' if checked else 'No'}")
        return "<br/>".join(lines)
    return item.get("checklist_template_name") or ""


def _checkin_pdf_row(item):
    return [
        item.get("date") or "",
        item.get("guard_name") or "",
        item.get("employee_code") or "",
        item.get("designation") or "",
        item.get("shift_name") or "",
        item.get("checkpoint_name") or "",
        item.get("site_name") or "",
        _format_dt(item.get("expected_time")),
        _format_dt(item.get("actual_checkin_time")),
        item.get("status") or "",
        "" if item.get("delay_minutes") is None else item.get("delay_minutes"),
        "Yes" if item.get("has_checklist") else "No",
        _format_checklist_cell(item),
        item.get("checklist_remarks") or "",
    ]


def _resolve_range(request, filter_type, start_date, end_date, location_id):
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    today = get_user_today(user_tz)
    filter_type = (filter_type or "today").lower()
    if filter_type == "custom" and start_date and end_date:
        try:
            return (
                datetime.strptime(start_date, "%Y-%m-%d").date(),
                datetime.strptime(end_date, "%Y-%m-%d").date(),
            )
        except ValueError:
            return today, today
    if filter_type in ("week", "this_week"):
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if filter_type in ("month", "this_month"):
        start = today.replace(day=1)
        return start, today
    return today, today


def _org_name(request, location_id):
    if location_id and location_id != "All":
        from scheduler.models import Location

        loc = Location.objects.filter(id=location_id, is_deleted=False).first()
        if loc:
            return loc.name
    if getattr(request.user, "location", None):
        return request.user.location.name or "—"
    return "—"


def generate_checkin_report_pdf(report_data, request, filter_type, start_date, end_date, location_id):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    range_start, range_end = _resolve_range(request, filter_type, start_date, end_date, location_id)
    org_name = _org_name(request, location_id)

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0.2 * inch,
        rightMargin=0.2 * inch,
        topMargin=1.2 * inch,
        bottomMargin=0.35 * inch,
    )
    doc.report_title = "QR Scan Patrol Report"
    doc.org_name = org_name
    doc.dept_label = org_name
    doc.range_start = range_start
    doc.range_end = range_end
    doc.printed_at = to_user_timezone(timezone.now(), user_tz)

    styles = getSampleStyleSheet()

    def _para_style(name, **kwargs):
        base = dict(
            leftIndent=0,
            rightIndent=0,
            firstLineIndent=0,
            spaceBefore=0,
            spaceAfter=0,
        )
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    cell_style = _para_style(
        "CheckinPdfCell",
        parent=styles["Normal"],
        fontSize=6,
        alignment=TA_LEFT,
        leading=7.5,
    )
    header_style = _para_style(
        "CheckinPdfHeader",
        parent=styles["Normal"],
        fontSize=6,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        leading=7.5,
    )

    data = [[Paragraph(h, header_style) for h in CHECKIN_PDF_HEADERS]]
    for item in report_data or []:
        data.append(
            [
                Paragraph(str(v) if v not in (None, "") else "—", cell_style)
                for v in _checkin_pdf_row(item)
            ]
        )
    if len(data) == 1:
        data.append(
            [Paragraph("No check-in records found", cell_style)]
            + [""] * (len(CHECKIN_PDF_HEADERS) - 1)
        )

    ratios = [0.07, 0.08, 0.06, 0.07, 0.07, 0.09, 0.07, 0.08, 0.09, 0.06, 0.05, 0.05, 0.09, 0.07]
    ratio_sum = sum(ratios)
    col_widths = [doc.width * (r / ratio_sum) for r in ratios]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D9E1F2")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 2),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                ("LEFTPADDING", (0, 0), (-1, -1), 2),
                ("RIGHTPADDING", (0, 0), (-1, -1), 2),
            ]
        )
    )

    doc.build(
        [table],
        onFirstPage=draw_dashboard_pdf_header_and_footer,
        onLaterPages=draw_dashboard_pdf_header_and_footer,
    )
    buffer.seek(0)
    if filter_type == "custom" and start_date and end_date:
        filename = f"checkin_report_{start_date}_{end_date}.pdf"
    else:
        filename = f"checkin_report_{timezone.now().strftime('%Y%m%d')}.pdf"
    return buffer.getvalue(), filename
