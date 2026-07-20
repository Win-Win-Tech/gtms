"""Visitor Excel export helpers."""
from io import BytesIO

from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook
from openpyxl.styles import Font

from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone


HEADERS = [
    "Visitor Name",
    "IC / Passport",
    "Phone",
    "Visitor Type",
    "Status",
    "Purpose",
    "Vehicle Number",
    "Host",
    "Host Emp Code",
    "Location",
    "Check In",
    "Check Out",
    "Remarks",
]


def _fmt_dt(dt, user_tz):
    if not dt:
        return ""
    local = to_user_timezone(dt, user_tz)
    return local.strftime("%d-%b-%Y %H:%M") if local else ""


def generate_visitor_excel(queryset, request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Visitor Entries"
    ws.append(HEADERS)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for entry in queryset:
        location_id = str(entry.location_id) if entry.location_id else None
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        host = entry.host
        visitor = entry.visitor
        ws.append(
            [
                visitor.visitor_name if visitor else "",
                visitor.ic_passport_number if visitor else "",
                visitor.phone_number if visitor else "",
                entry.visitor_type or "",
                entry.status or "",
                entry.purpose_of_visit or "",
                entry.vehicle_number or "",
                getattr(host, "name", "") if host else "",
                getattr(host, "employee_code", "") if host else "",
                entry.location.name if entry.location else "",
                _fmt_dt(entry.check_in_time, user_tz),
                _fmt_dt(entry.check_out_time, user_tz),
                entry.remarks or "",
            ]
        )

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)

    filename = f"visitor_entries_{timezone.now().strftime('%Y%m%d')}.xlsx"
    response = HttpResponse(
        buf.getvalue(),
        content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
    response["Content-Disposition"] = f'attachment; filename="{filename}"'
    return response
