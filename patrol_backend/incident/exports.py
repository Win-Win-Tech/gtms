"""Incident Excel / PDF export — shared columns and row values."""
from datetime import datetime, timedelta
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from rest_framework import status
from rest_framework.response import Response

from patrol_backend.utils.timezone_utils import (
    convert_date_range_to_utc,
    get_user_now,
    get_user_timezone_from_request,
    to_user_timezone,
)

from .models import incidentreport

INCIDENT_HEADERS = [
    "Ticket Number",
    "Severity",
    "Status",
    "Location",
    "Checkpoint",
    "Created On",
    "Created By",
    "Assigned On",
    "Assigned By",
    "Assigned To",
    "Closed On",
    "Closed By",
    "Time Difference (Created-Assigned)",
    "Time Difference (Assigned-Closed)",
    "Time Difference (Created-Closed)",
    "Description",
    "Closure Description",
    "SLA Status",
]


def _format_time_difference(start_time, end_time):
    if not start_time or not end_time:
        return ""
    diff = end_time - start_time
    total_seconds = int(diff.total_seconds())
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    if hours > 0:
        return f"{hours}h {minutes}m"
    return f"{minutes}m"


def _calculate_sla_status(incident):
    if not incident.resolved_on or not incident.created_on:
        return "N/A"
    sla_thresholds = {"High": 8, "Medium": 16, "Low": 24}
    threshold_hours = sla_thresholds.get(incident.severity, 8)
    hours_taken = (incident.resolved_on - incident.created_on).total_seconds() / 3600
    return "Met" if hours_taken <= threshold_hours else "Not Met"


def _incident_export_row(incident, request):
    incident_location_id = str(incident.location.id) if incident.location else None
    incident_tz = get_user_timezone_from_request(request, location_id=incident_location_id)
    created_on_tz = to_user_timezone(incident.created_on, incident_tz) if incident.created_on else None
    assigned_on_tz = to_user_timezone(incident.assigned_on, incident_tz) if incident.assigned_on else None
    closed_on_tz = to_user_timezone(incident.resolved_on, incident_tz) if incident.resolved_on else None
    return [
        incident.ticket_number or "",
        incident.severity or "",
        incident.status or "",
        incident.location.name if incident.location else "",
        incident.checkpoint.label if incident.checkpoint else "",
        created_on_tz.strftime("%Y-%m-%d %H:%M:%S") if created_on_tz else "",
        incident.created_by.name if incident.created_by else "",
        assigned_on_tz.strftime("%Y-%m-%d %H:%M:%S") if assigned_on_tz else "",
        incident.assigned_by.name if incident.assigned_by else "",
        incident.assigned_to.name if incident.assigned_to else "",
        closed_on_tz.strftime("%Y-%m-%d %H:%M:%S") if closed_on_tz else "",
        incident.resolved_by.name if incident.resolved_by else "",
        _format_time_difference(incident.created_on, incident.assigned_on),
        _format_time_difference(incident.assigned_on, incident.resolved_on),
        _format_time_difference(incident.created_on, incident.resolved_on),
        incident.incident_description or "",
        incident.closure_description or "",
        _calculate_sla_status(incident),
    ]


def get_incident_export_queryset(request, assigned_to_user=None):
    date_filter = request.query_params.get("date_filter", "").lower()
    status_filter = request.query_params.get("status", "").title()
    severity_filter = request.query_params.get("severity", "").capitalize()
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    location_id = request.query_params.get("location_id")
    assigned_to_id = request.query_params.get("assigned_to")
    created_by_id = request.query_params.get("created_by")
    checkpoint_id = request.query_params.get("checkpoint_id")

    queryset = incidentreport.objects.select_related(
        "created_by",
        "assigned_by",
        "assigned_to",
        "resolved_by",
        "location",
        "checkpoint",
    )
    if assigned_to_user is not None:
        queryset = queryset.filter(assigned_to=assigned_to_user)

    if status_filter in ["Open", "In-Progress", "Closed"]:
        queryset = queryset.filter(status=status_filter)
    if severity_filter in ["Low", "Medium", "High"]:
        queryset = queryset.filter(severity=severity_filter)

    filter_location_id = location_id if location_id else None
    user_tz = get_user_timezone_from_request(request, location_id=filter_location_id)
    user_today = get_user_now(user_tz).date()

    if date_filter == "today":
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
        queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
    elif date_filter == "this_week":
        start_of_week = user_today - timedelta(days=user_today.weekday())
        end_of_week = start_of_week + timedelta(days=6)
        start_utc, end_utc = convert_date_range_to_utc(start_of_week, end_of_week, user_tz)
        queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
    elif date_filter == "this_month":
        start_of_month = user_today.replace(day=1)
        if user_today.month == 12:
            end_of_month = user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end_of_month = user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1)
        start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
        queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
    elif date_filter == "custom" and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
            queryset = queryset.filter(created_on__gte=start_utc, created_on__lt=end_utc + timedelta(days=1))
        except ValueError:
            return None, Response(
                {"error": "Invalid date format. Use YYYY-MM-DD."},
                status=status.HTTP_400_BAD_REQUEST,
            )

    if location_id:
        queryset = queryset.filter(location_id=location_id)
    if assigned_to_id:
        queryset = queryset.filter(assigned_to_id=assigned_to_id)
    if created_by_id:
        queryset = queryset.filter(created_by_id=created_by_id)
    if checkpoint_id:
        queryset = queryset.filter(checkpoint_id=checkpoint_id)

    return queryset, None


def _export_filename(request, prefix, ext):
    date_filter = request.query_params.get("date_filter", "").lower()
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    if date_filter == "custom" and start_date and end_date:
        return f"{prefix}_{start_date}_{end_date}.{ext}"
    if date_filter:
        return f"{prefix}_{date_filter}_{timezone.now().strftime('%Y%m%d')}.{ext}"
    return f"{prefix}_{timezone.now().strftime('%Y%m%d')}.{ext}"


def generate_incident_excel(queryset, request, prefix="incident_report"):
    wb = Workbook()
    ws = wb.active
    ws.title = "Incident Report"
    ws.append(INCIDENT_HEADERS)
    for incident in queryset:
        ws.append(_incident_export_row(incident, request))

    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    response = HttpResponse(
        buffer.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{_export_filename(request, prefix, "xlsx")}"'
    return response


def _resolve_pdf_meta(request):
    date_filter = (request.query_params.get("date_filter") or "today").lower()
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    location_id = request.query_params.get("location_id")
    user_tz = get_user_timezone_from_request(request, location_id=location_id)
    today = get_user_now(user_tz).date()
    range_start, range_end = today, today
    if date_filter == "this_week":
        range_start = today - timedelta(days=today.weekday())
        range_end = range_start + timedelta(days=6)
    elif date_filter == "this_month":
        range_start = today.replace(day=1)
        range_end = today
    elif date_filter == "custom" and start_date and end_date:
        try:
            range_start = datetime.strptime(start_date, "%Y-%m-%d").date()
            range_end = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    org_name = "—"
    if location_id and location_id != "All":
        from scheduler.models import Location

        loc = Location.objects.filter(id=location_id, is_deleted=False).first()
        if loc:
            org_name = loc.name
    elif getattr(request.user, "location", None):
        org_name = request.user.location.name or "—"

    return {
        "org_name": org_name,
        "range_start": range_start,
        "range_end": range_end,
        "printed_at": to_user_timezone(timezone.now(), user_tz),
    }


def generate_incident_pdf(queryset, request, prefix="incident_report", title="Incident Report"):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    from patrol_backend.utils.pdf_report import draw_dashboard_pdf_header_and_footer

    meta = _resolve_pdf_meta(request)
    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0.15 * inch,
        rightMargin=0.15 * inch,
        topMargin=1.2 * inch,
        bottomMargin=0.35 * inch,
    )
    doc.report_title = title
    doc.org_name = meta["org_name"]
    doc.dept_label = meta["org_name"]
    doc.range_start = meta["range_start"]
    doc.range_end = meta["range_end"]
    doc.printed_at = meta["printed_at"]

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
        "IncidentPdfCell",
        parent=styles["Normal"],
        fontSize=5.5,
        alignment=TA_LEFT,
        leading=7,
    )
    header_style = _para_style(
        "IncidentPdfHeader",
        parent=styles["Normal"],
        fontSize=5.5,
        fontName="Helvetica-Bold",
        alignment=TA_CENTER,
        leading=7,
    )

    data = [[Paragraph(h, header_style) for h in INCIDENT_HEADERS]]
    rows = list(queryset)
    for incident in rows:
        data.append(
            [
                Paragraph(str(v) if v not in (None, "") else "—", cell_style)
                for v in _incident_export_row(incident, request)
            ]
        )
    if len(data) == 1:
        data.append(
            [Paragraph("No incidents found", cell_style)] + [""] * (len(INCIDENT_HEADERS) - 1)
        )

    ratios = [
        0.06, 0.045, 0.05, 0.06, 0.055,
        0.065, 0.055, 0.065, 0.055, 0.055,
        0.065, 0.055, 0.055, 0.055, 0.055,
        0.08, 0.07, 0.04,
    ]
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
                ("LEFTPADDING", (0, 0), (-1, -1), 1),
                ("RIGHTPADDING", (0, 0), (-1, -1), 1),
            ]
        )
    )

    doc.build(
        [table],
        onFirstPage=draw_dashboard_pdf_header_and_footer,
        onLaterPages=draw_dashboard_pdf_header_and_footer,
    )
    buffer.seek(0)
    return buffer.getvalue(), _export_filename(request, prefix, "pdf")
