"""Support ticket Excel / PDF export helpers."""

from __future__ import annotations

from datetime import datetime
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

SUPPORT_REPORT_HEADERS = [
    "Ticket #",
    "Subject",
    "Status",
    "Priority",
    "Organisation",
    "Site",
    "Category",
    "Assignee",
    "Created By",
    "Created On",
    "Due At",
    "Resolved At",
    "Closed At",
    "Overdue",
]


def _fmt_dt(value, tz):
    if not value:
        return ""
    local = to_user_timezone(value, tz) if tz else value
    try:
        return local.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return str(value)


def _ticket_row(ticket, tz):
    from .sla import ticket_is_overdue

    return [
        ticket.ticket_number or "",
        ticket.subject or "",
        ticket.status or "",
        ticket.priority or "",
        ticket.organisation.name if ticket.organisation_id else "",
        ticket.site.name if ticket.site_id else "",
        ticket.category.name if ticket.category_id else "",
        (
            ticket.current_assignee.name
            or ticket.current_assignee.email
            if ticket.current_assignee_id
            else ""
        ),
        (
            ticket.created_by.name or ticket.created_by.email
            if ticket.created_by_id
            else ""
        ),
        _fmt_dt(ticket.created_on, tz),
        _fmt_dt(ticket.due_at, tz),
        _fmt_dt(ticket.resolved_at, tz),
        _fmt_dt(ticket.closed_at, tz),
        "Yes" if ticket_is_overdue(ticket) else "No",
    ]


def generate_support_excel(queryset, request):
    tz = get_user_timezone_from_request(request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Support Tickets"

    header_fill = PatternFill("solid", fgColor="5B21B6")
    header_font = Font(color="FFFFFF", bold=True, size=10)
    thin = Border(
        left=Side(style="thin", color="D1D5DB"),
        right=Side(style="thin", color="D1D5DB"),
        top=Side(style="thin", color="D1D5DB"),
        bottom=Side(style="thin", color="D1D5DB"),
    )

    for col, header in enumerate(SUPPORT_REPORT_HEADERS, start=1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.fill = header_fill
        cell.font = header_font
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = thin

    for row_idx, ticket in enumerate(queryset, start=2):
        for col_idx, value in enumerate(_ticket_row(ticket, tz), start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.border = thin
            cell.alignment = Alignment(vertical="top", wrap_text=True)

    widths = [14, 28, 12, 10, 18, 16, 16, 16, 16, 18, 18, 18, 18, 10]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    ws.auto_filter.ref = f"A1:{get_column_letter(len(SUPPORT_REPORT_HEADERS))}1"
    ws.freeze_panes = "A2"

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    stamp = datetime.now().strftime("%Y%m%d")
    filename = f"support_tickets_{stamp}.xlsx"
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response


def generate_support_pdf(queryset, request):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    from patrol_backend.utils.pdf_report import draw_dashboard_pdf_header_and_footer

    tz = get_user_timezone_from_request(request)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0.2 * inch,
        rightMargin=0.2 * inch,
        topMargin=1.2 * inch,
        bottomMargin=0.35 * inch,
    )
    doc.report_title = "Support Ticket Report"
    doc.org_name = "Smart Patrol"
    doc.dept_label = "Support"
    doc.range_start = None
    doc.range_end = None
    doc.printed_at = to_user_timezone(timezone.now(), tz)

    styles = getSampleStyleSheet()
    cell_style = ParagraphStyle(
        "SupportPdfCell",
        parent=styles["Normal"],
        fontSize=6,
        alignment=TA_LEFT,
        leading=7.5,
    )
    header_style = ParagraphStyle(
        "SupportPdfHeader",
        parent=styles["Normal"],
        fontSize=6,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        leading=7.5,
    )

    # Slimmer PDF columns for readability
    pdf_headers = [
        "Ticket #",
        "Subject",
        "Status",
        "Priority",
        "Org",
        "Site",
        "Assignee",
        "Created",
        "Due",
        "Overdue",
    ]
    data = [[Paragraph(h, header_style) for h in pdf_headers]]
    for ticket in queryset:
        row = _ticket_row(ticket, tz)
        slim = [
            row[0],
            row[1],
            row[2],
            row[3],
            row[4],
            row[5],
            row[7],
            row[9],
            row[10],
            row[13],
        ]
        data.append([Paragraph(str(v) if v not in (None, "") else "—", cell_style) for v in slim])

    if len(data) == 1:
        data.append(
            [Paragraph("No tickets found", cell_style)] + [""] * (len(pdf_headers) - 1)
        )

    ratios = [0.1, 0.18, 0.08, 0.07, 0.12, 0.1, 0.1, 0.1, 0.1, 0.05]
    col_widths = [doc.width * r for r in ratios]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
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
    stamp = datetime.now().strftime("%Y%m%d")
    filename = f"support_tickets_{stamp}.pdf"
    return buffer.getvalue(), filename
