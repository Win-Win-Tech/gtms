"""Support ticket Excel / PDF export helpers.

One row per ticket. Assignment history is concatenated from TicketAssignment
(including hops recorded when refer-back clears current_assignee).
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
from xml.sax.saxutils import escape

from django.db.models import Prefetch
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

from .models import TicketActivityLog, TicketAssignment
from .sla import ticket_is_overdue

SUPPORT_REPORT_HEADERS = [
    "Ticket #",
    "Subject",
    "Description",
    "Status",
    "Priority",
    "Organisation",
    "Site",
    "Category",
    "Current Assignee",
    "Created By",
    "Created On",
    "Status Entered At",
    "Due At",
    "Resolved At",
    "Referred Back At",
    "Closed At",
    "Assignment History",
    "Time Diffs",
    "Last Updated By",
    "Last Update",
    "Resolution",
    "Refer Back Reason",
    "Closure Note",
    "Auto Closed",
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


def _pdf_text(value):
    return escape(str(value or "—")).replace("\n", "<br/>")


def _person(user):
    if not user:
        return ""
    return (getattr(user, "name", None) or getattr(user, "email", None) or "").strip()


def prepare_export_queryset(queryset):
    """Prefetch assignment hops and activity so history survives a cleared assignee."""
    return queryset.prefetch_related(
        Prefetch(
            "assignments",
            queryset=TicketAssignment.objects.select_related(
                "from_user", "to_user", "assigned_by"
            ).order_by("created_on"),
        ),
        Prefetch(
            "activity_logs",
            queryset=TicketActivityLog.objects.select_related("actor").order_by(
                "created_on"
            ),
        ),
    )


def format_assignment_history(ticket, tz):
    parts = []
    for row in ticket.assignments.all():
        left = _person(row.from_user) or "Unassigned"
        right = _person(row.to_user) or "Unassigned"
        stamp = _fmt_dt(row.created_on, tz)
        bit = f"{left} → {right} [{row.action} {stamp}]".strip()
        note = (row.comment or "").strip()
        if note:
            bit += f" ({note})"
        parts.append(bit)
    return " | ".join(parts)


def _closure_note(activity_rows):
    note = ""
    for row in activity_rows or []:
        if str(getattr(row, "action", "") or "").upper() in ("CLOSED", "AUTO_CLOSED"):
            text = (row.comment or "").strip()
            if text:
                note = text
    return note


def _last_update(activity_rows, tz):
    if not activity_rows:
        return "", ""
    last = activity_rows[-1]
    who = _person(getattr(last, "actor", None)) or "System"
    action = str(getattr(last, "action", "") or "").replace("_", " ")
    when = _fmt_dt(getattr(last, "created_on", None), tz)
    return who, f"{action} {when}".strip()


def _time_diffs(ticket, activity_rows):
    from .services import compute_ticket_durations

    durations = compute_ticket_durations(ticket, activity_rows)
    parts = []
    if durations.get("open_to_assign"):
        parts.append(f"Open→Assign {durations['open_to_assign']}")
    if durations.get("assign_to_resolve"):
        label = durations.get("assign_to_resolve_label") or "Assign→Resolve"
        parts.append(f"{label} {durations['assign_to_resolve']}")
    if durations.get("resolve_to_close"):
        parts.append(f"Resolve→Close {durations['resolve_to_close']}")
    if durations.get("total"):
        parts.append(f"Total {durations['total']}")
    return "; ".join(parts)


def _ticket_row(ticket, tz):
    activity_rows = list(ticket.activity_logs.all())
    who, last_update = _last_update(activity_rows, tz)
    refer_reason = (
        getattr(ticket, "referred_back_reason", None) or ticket.invalidation_reason or ""
    )
    return [
        ticket.ticket_number or "",
        ticket.subject or "",
        ticket.description or "",
        ticket.status or "",
        ticket.priority or "",
        ticket.organisation.name if ticket.organisation_id else "",
        ticket.site.name if ticket.site_id else "",
        ticket.category.name if ticket.category_id else "",
        _person(ticket.current_assignee),
        _person(ticket.created_by),
        _fmt_dt(ticket.created_on, tz),
        _fmt_dt(getattr(ticket, "status_entered_at", None), tz),
        _fmt_dt(ticket.due_at, tz),
        _fmt_dt(ticket.resolved_at, tz),
        _fmt_dt(getattr(ticket, "referred_back_at", None), tz),
        _fmt_dt(ticket.closed_at, tz),
        format_assignment_history(ticket, tz),
        _time_diffs(ticket, activity_rows),
        who,
        last_update,
        ticket.resolution_summary or "",
        refer_reason,
        _closure_note(activity_rows),
        "Yes" if getattr(ticket, "auto_closed", False) else "No",
        "Yes" if ticket_is_overdue(ticket) else "No",
    ]


def generate_support_excel(queryset, request):
    tz = get_user_timezone_from_request(request)
    queryset = prepare_export_queryset(queryset)
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

    widths = [
        16, 28, 36, 16, 12, 18, 16, 16, 18, 18,
        20, 20, 20, 20, 20, 20, 48, 36, 18, 28,
        28, 28, 28, 12, 10,
    ]
    for i, width in enumerate(widths, start=1):
        ws.column_dimensions[get_column_letter(i)].width = width

    ws.auto_filter.ref = f"A1:{get_column_letter(len(SUPPORT_REPORT_HEADERS))}1"
    ws.freeze_panes = "A2"
    ws.row_dimensions[1].height = 30

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
    queryset = prepare_export_queryset(queryset)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0.25 * inch,
        rightMargin=0.25 * inch,
        topMargin=1.15 * inch,
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
        fontSize=6.5,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        leading=8,
    )

    pdf_headers = [
        "Ticket",
        "Status",
        "Description",
        "Assignment history",
        "Status times",
        "Notes",
    ]
    data = [[Paragraph(h, header_style) for h in pdf_headers]]
    for ticket in queryset:
        row = _ticket_row(ticket, tz)
        # row indexes follow SUPPORT_REPORT_HEADERS
        status_times = (
            f"Created: {_pdf_text(row[10] or '—')}<br/>"
            f"Entered: {_pdf_text(row[11] or '—')}<br/>"
            f"Resolved: {_pdf_text(row[13] or '—')}<br/>"
            f"Referred: {_pdf_text(row[14] or '—')}<br/>"
            f"Closed: {_pdf_text(row[15] or '—')}<br/>"
            f"{_pdf_text(row[17] or '')}"
        )
        notes = (
            f"Assignee: {_pdf_text(row[8] or 'Unassigned')}<br/>"
            f"Updated by: {_pdf_text(row[18] or '—')} {_pdf_text(row[19] or '')}<br/>"
            f"Refer back: {_pdf_text(row[21] or '—')}<br/>"
            f"Closure: {_pdf_text(row[22] or '—')}<br/>"
            f"Auto closed: {_pdf_text(row[23])}"
        )
        slim = [
            f"{_pdf_text(row[0])}<br/>{_pdf_text(row[1])}",
            f"{_pdf_text(row[3])}<br/>{_pdf_text(row[4])}",
            _pdf_text(row[2] or "—"),
            _pdf_text(row[16] or "—"),
            status_times,
            notes,
        ]
        data.append([Paragraph(v, cell_style) for v in slim])

    if len(data) == 1:
        data.append(
            [Paragraph("No tickets found", cell_style)] + [""] * (len(pdf_headers) - 1)
        )

    ratios = [0.14, 0.1, 0.2, 0.22, 0.17, 0.17]
    col_widths = [doc.width * r for r in ratios]
    table = Table(data, colWidths=col_widths, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDE9FE")),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("GRID", (0, 0), (-1, -1), 0.25, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ("LEFTPADDING", (0, 0), (-1, -1), 3),
                ("RIGHTPADDING", (0, 0), (-1, -1), 3),
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
