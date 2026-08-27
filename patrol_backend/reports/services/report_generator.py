"""
Auto report email: has_data pre-check, then generate PDF/Excel only when data exists.
"""

from __future__ import annotations

import logging
import os
import re
from io import BytesIO

from django.conf import settings
from openpyxl import Workbook

from reports.constants import REPORT_CATALOG_BY_CODE
from reports.models import LocationReportEmailItem as Item
from reports.services.schedule_utils import make_fake_request

logger = logging.getLogger(__name__)

MIME_XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_PDF = "application/pdf"


def _safe_name(value: str) -> str:
    return re.sub(r"[^\w\-]+", "_", (value or "report").strip())[:80] or "report"


def _period_dates(period):
    start = period["start_date"].isoformat()
    end = period["end_date"].isoformat()
    return start, end


def _fake_req(user, location_id, period, site_id=None, extra=None):
    start, end = _period_dates(period)
    params = {
        "location_id": str(location_id),
        "location": str(location_id),
        "date_filter": "custom",
        "filter": "custom",
        "start_date": start,
        "end_date": end,
    }
    if site_id:
        params["site_id"] = str(site_id)
    if extra:
        params.update(extra)
    return make_fake_request(user, params)


# ---------------------------------------------------------------------------
# Check-in
# ---------------------------------------------------------------------------

def _checkin_data(location_id, period, site_id, request):
    from dashboard.views import _get_checkin_report_data_v2

    start, end = _period_dates(period)
    return _get_checkin_report_data_v2(
        filter_type="custom",
        start_date_str=start,
        end_date_str=end,
        location_id=str(location_id),
        site_id=str(site_id) if site_id else None,
        request=request,
    )


def _checkin_has_data(location_id, period, site_id, request):
    return len(_checkin_data(location_id, period, site_id, request) or []) > 0


def _checkin_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from dashboard.checkin_report_pdf import generate_checkin_report_pdf

    data = _checkin_data(location_id, period, site_id, request)
    if not data:
        return []

    start, end = _period_dates(period)
    attachments = []
    base = f"qr_scan_patrol_{_safe_name(label)}_{start}"

    if send_excel:
        # Prefer v2 data path: build workbook from already-fetched data
        wb = Workbook()
        ws = wb.active
        ws.title = "QR Scan Patrol Report"
        headers = [
            "Shift Date",
            "Name",
            "Emp Code",
            "Designation",
            "Shift Name",
            "Checkpoint Name",
            "Site",
            "Scheduled",
            "Scanned",
            "Status",
            "Delay (minutes)",
        ]
        ws.append(headers)
        for item in data:
            ws.append(
                [
                    item.get("date") or "",
                    item.get("guard_name") or "",
                    item.get("employee_code") or "",
                    item.get("designation") or "",
                    item.get("shift_name") or "",
                    item.get("checkpoint_name") or "",
                    item.get("site_name") or "",
                    item["expected_time"].strftime("%Y-%m-%d %H:%M") if item.get("expected_time") else "",
                    item["actual_checkin_time"].strftime("%Y-%m-%d %H:%M")
                    if item.get("actual_checkin_time")
                    else "",
                    item.get("status") or "",
                    "" if item.get("delay_minutes") is None else item.get("delay_minutes"),
                ]
            )
        buf = BytesIO()
        wb.save(buf)
        attachments.append(
            {
                "filename": f"{base}.xlsx",
                "content": buf.getvalue(),
                "mime": MIME_XLSX,
                "format": "excel",
                "row_count": len(data),
                "display_name": f"QR Scan Patrol ({label})",
            }
        )

    if send_pdf:
        pdf_bytes, filename = generate_checkin_report_pdf(
            data, request, "custom", start, end, str(location_id)
        )
        attachments.append(
            {
                "filename": filename or f"{base}.pdf",
                "content": pdf_bytes,
                "mime": MIME_PDF,
                "format": "pdf",
                "row_count": len(data),
                "display_name": f"QR Scan Patrol ({label})",
            }
        )

    return attachments


# ---------------------------------------------------------------------------
# Attendance
# ---------------------------------------------------------------------------

def _attendance_queryset(location_id, period, site_id, request):
    from dashboard.models import AttendanceCheckin
    from patrol_backend.utils.timezone_utils import get_user_timezone_from_request

    queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")
    # request retained so callers share fake-request timezone context
    _ = get_user_timezone_from_request(request, location_id=str(location_id))
    start = period["start_date"]
    end = period["end_date"]
    queryset = queryset.filter(shift_date__gte=start, shift_date__lte=end)
    queryset = queryset.filter(org_location_id=location_id)
    if site_id:
        queryset = queryset.filter(site_id=site_id)
    return queryset


def _attendance_has_data(location_id, period, site_id, request):
    return _attendance_queryset(location_id, period, site_id, request).exists()


def _attendance_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from dashboard.attendance_detail_pdf import generate_attendance_v4_pdf_report_internal
    from dashboard.views import generate_attendance_v4_excel_report_internal

    if not _attendance_has_data(location_id, period, site_id, request):
        return []

    start, end = _period_dates(period)
    attachments = []
    base = f"attendance_{_safe_name(label)}_{start}"
    common = dict(
        date_filter="custom",
        start_date=start,
        end_date=end,
        location_id=str(location_id),
        site_id=str(site_id) if site_id else None,
        request=request,
    )

    if send_excel:
        result = generate_attendance_v4_excel_report_internal(**common)
        if result.get("row_count", 0) > 0 and result.get("file_path"):
            with open(result["file_path"], "rb") as fh:
                attachments.append(
                    {
                        "filename": result.get("filename") or f"{base}.xlsx",
                        "content": fh.read(),
                        "mime": MIME_XLSX,
                        "format": "excel",
                        "row_count": result["row_count"],
                        "display_name": f"Attendance ({label})",
                    }
                )
            try:
                os.remove(result["file_path"])
            except OSError:
                pass

    if send_pdf:
        result = generate_attendance_v4_pdf_report_internal(**common)
        content = result.get("pdf_bytes")
        if not content and result.get("file_path"):
            with open(result["file_path"], "rb") as fh:
                content = fh.read()
        if result.get("row_count", 0) > 0 and content:
            attachments.append(
                {
                    "filename": result.get("filename") or f"{base}.pdf",
                    "content": content,
                    "mime": MIME_PDF,
                    "format": "pdf",
                    "row_count": result["row_count"],
                    "display_name": f"Attendance ({label})",
                }
            )
        if result.get("file_path"):
            try:
                os.remove(result["file_path"])
            except OSError:
                pass

    return attachments


# ---------------------------------------------------------------------------
# Roll call
# ---------------------------------------------------------------------------

def _rollcall_queryset(location_id, period, site_id, request):
    from rollcall.models import RollCallSession
    from rollcall.utils import apply_shift_date_filter

    qs = RollCallSession.objects.filter(is_deleted=False, location_id=location_id)
    if site_id:
        qs = qs.filter(site_id=site_id)
    start, end = _period_dates(period)
    qs = apply_shift_date_filter(qs, request, str(location_id), "custom", start, end)
    return qs


def _rollcall_has_data(location_id, period, site_id, request):
    try:
        return _rollcall_queryset(location_id, period, site_id, request).exists()
    except Exception:
        logger.exception("rollcall has_data failed")
        return False


def _rollcall_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from rollcall.exports import generate_rollcall_excel, generate_rollcall_pdf

    qs = _rollcall_queryset(location_id, period, site_id, request)
    if not qs.exists():
        return []
    count = qs.count()
    attachments = []
    start, _ = _period_dates(period)
    base = f"rollcall_{_safe_name(label)}_{start}"
    include_site = bool(site_id)

    if send_excel:
        content, filename = generate_rollcall_excel(qs, request, include_site=include_site)
        attachments.append(
            {
                "filename": filename or f"{base}.xlsx",
                "content": content,
                "mime": MIME_XLSX,
                "format": "excel",
                "row_count": count,
                "display_name": f"Roll Call ({label})",
            }
        )
    if send_pdf:
        result = generate_rollcall_pdf(qs, request, include_site=include_site)
        if isinstance(result, tuple):
            content, filename = result
        else:
            content, filename = result.content, f"{base}.pdf"
        attachments.append(
            {
                "filename": filename if str(filename).endswith(".pdf") else f"{base}.pdf",
                "content": content,
                "mime": MIME_PDF,
                "format": "pdf",
                "row_count": count,
                "display_name": f"Roll Call ({label})",
            }
        )
    return attachments


# ---------------------------------------------------------------------------
# Incident
# ---------------------------------------------------------------------------

def _incident_queryset(location_id, period, site_id, request):
    from incident.exports import get_incident_export_queryset
    from incident.site_filter import apply_incident_site_filter

    qs, err = get_incident_export_queryset(request)
    if err is not None or qs is None:
        return qs.none() if hasattr(qs, "none") else None
    if site_id:
        # Prefer dedicated site filter if available
        try:
            from types import SimpleNamespace

            req = SimpleNamespace(user=request.user, query_params={**dict(request.query_params), "site_id": str(site_id)})
            qs = apply_incident_site_filter(qs, req)
        except Exception:
            qs = qs.filter(site_id=site_id)
    return qs


def _incident_has_data(location_id, period, site_id, request):
    qs = _incident_queryset(location_id, period, site_id, request)
    return bool(qs is not None and qs.exists())


def _http_to_bytes(response):
    if isinstance(response, tuple):
        return response[0], response[1] if len(response) > 1 else None
    if hasattr(response, "content"):
        return response.content, None
    return response, None


def _incident_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from incident.exports import generate_incident_excel, generate_incident_pdf

    qs = _incident_queryset(location_id, period, site_id, request)
    if qs is None or not qs.exists():
        return []
    count = qs.count()
    attachments = []
    start, _ = _period_dates(period)
    base = f"incident_{_safe_name(label)}_{start}"
    include_site = bool(site_id)

    if send_excel:
        content, _fname = _http_to_bytes(
            generate_incident_excel(qs, request, prefix=base, include_site=include_site)
        )
        attachments.append(
            {
                "filename": f"{base}.xlsx",
                "content": content,
                "mime": MIME_XLSX,
                "format": "excel",
                "row_count": count,
                "display_name": f"Incident ({label})",
            }
        )
    if send_pdf:
        content, fname = generate_incident_pdf(qs, request, prefix=base, include_site=include_site)
        attachments.append(
            {
                "filename": fname or f"{base}.pdf",
                "content": content,
                "mime": MIME_PDF,
                "format": "pdf",
                "row_count": count,
                "display_name": f"Incident ({label})",
            }
        )
    return attachments


# ---------------------------------------------------------------------------
# Visitor entries
# ---------------------------------------------------------------------------

def _visitor_queryset(location_id, period, site_id, request):
    from visitor.views import _filtered_entries

    qs, err = _filtered_entries(request)
    if err is not None or qs is None:
        return None
    if site_id:
        qs = qs.filter(site_id=site_id)
    return qs


def _visitor_has_data(location_id, period, site_id, request):
    qs = _visitor_queryset(location_id, period, site_id, request)
    return bool(qs is not None and qs.exists())


def _visitor_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from visitor.exports import generate_visitor_excel, generate_visitor_pdf

    qs = _visitor_queryset(location_id, period, site_id, request)
    if qs is None or not qs.exists():
        return []
    count = qs.count()
    attachments = []
    start, _ = _period_dates(period)
    base = f"visitors_{_safe_name(label)}_{start}"
    include_site = bool(site_id)

    if send_excel:
        content, _fname = _http_to_bytes(generate_visitor_excel(qs, request, include_site=include_site))
        attachments.append(
            {
                "filename": f"{base}.xlsx",
                "content": content,
                "mime": MIME_XLSX,
                "format": "excel",
                "row_count": count,
                "display_name": f"Visitor Entries ({label})",
            }
        )
    if send_pdf:
        content, fname = generate_visitor_pdf(qs, request, include_site=include_site)
        attachments.append(
            {
                "filename": fname or f"{base}.pdf",
                "content": content,
                "mime": MIME_PDF,
                "format": "pdf",
                "row_count": count,
                "display_name": f"Visitor Entries ({label})",
            }
        )
    return attachments


# ---------------------------------------------------------------------------
# Vehicle movement
# ---------------------------------------------------------------------------

def _vehicle_data(location_id, period, site_id, request):
    from visitor.vehicle_movement_report import build_vehicle_movement_report

    start, end = _period_dates(period)
    return build_vehicle_movement_report(
        request,
        str(location_id),
        date_filter="custom",
        start_date_str=start,
        end_date_str=end,
        site_id=str(site_id) if site_id else None,
    )


def _vehicle_has_data(location_id, period, site_id, request):
    try:
        data = _vehicle_data(location_id, period, site_id, request)
    except TypeError:
        # Older signature without site_id
        from visitor.vehicle_movement_report import build_vehicle_movement_report

        start, end = _period_dates(period)
        data = build_vehicle_movement_report(
            request,
            str(location_id),
            date_filter="custom",
            start_date_str=start,
            end_date_str=end,
        )
    except Exception:
        logger.exception("vehicle has_data failed")
        return False
    rows = data.get("rows") or []
    total = (data.get("total_in") or 0) + (data.get("total_out") or 0)
    return bool(rows) and total > 0


def _vehicle_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from visitor.vehicle_movement_report import (
        generate_vehicle_movement_excel,
        generate_vehicle_movement_pdf,
    )

    if not _vehicle_has_data(location_id, period, site_id, request):
        return []
    try:
        data = _vehicle_data(location_id, period, site_id, request)
    except TypeError:
        from visitor.vehicle_movement_report import build_vehicle_movement_report

        start, end = _period_dates(period)
        data = build_vehicle_movement_report(
            request,
            str(location_id),
            date_filter="custom",
            start_date_str=start,
            end_date_str=end,
        )

    attachments = []
    start, _ = _period_dates(period)
    base = f"vehicle_movement_{_safe_name(label)}_{start}"
    row_count = (data.get("total_in") or 0) + (data.get("total_out") or 0)

    if send_excel:
        content, _fname = _http_to_bytes(generate_vehicle_movement_excel(data))
        attachments.append(
            {
                "filename": f"{base}.xlsx",
                "content": content,
                "mime": MIME_XLSX,
                "format": "excel",
                "row_count": row_count,
                "display_name": f"Vehicle Movement ({label})",
            }
        )
    if send_pdf:
        content, filename = generate_vehicle_movement_pdf(data, request=request)
        attachments.append(
            {
                "filename": filename if str(filename).endswith(".pdf") else f"{base}.pdf",
                "content": content,
                "mime": MIME_PDF,
                "format": "pdf",
                "row_count": row_count,
                "display_name": f"Vehicle Movement ({label})",
            }
        )
    return attachments


# ---------------------------------------------------------------------------
# Monthly attendance
# ---------------------------------------------------------------------------

def _monthly_att_data(location_id, period, site_id, request):
    from dashboard.views import _get_monthly_attendance_summary_data_v2

    return _get_monthly_attendance_summary_data_v2(
        month=period.get("month_str"),
        start_date_str=None,
        end_date_str=None,
        location_id=str(location_id),
        site_id=str(site_id) if site_id else None,
        request=request,
    )


def _monthly_att_has_data(location_id, period, site_id, request):
    result = _monthly_att_data(location_id, period, site_id, request)
    rows = result.get("summary_data") or []
    return len(rows) > 0


def _monthly_att_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    from dashboard.views import generate_monthly_attendance_summary_excel_internal_v2

    if not _monthly_att_has_data(location_id, period, site_id, request):
        return []
    if not send_excel:
        return []  # PDF not supported

    result = generate_monthly_attendance_summary_excel_internal_v2(
        month=period.get("month_str"),
        location_id=str(location_id),
        site_id=str(site_id) if site_id else None,
        request=request,
        include_location_column=False,
    )
    if result.get("row_count", 0) <= 0 or not result.get("file_path"):
        return []
    with open(result["file_path"], "rb") as fh:
        content = fh.read()
    try:
        os.remove(result["file_path"])
    except OSError:
        pass
    return [
        {
            "filename": result.get("filename")
            or f"monthly_attendance_{_safe_name(label)}_{period.get('month_str')}.xlsx",
            "content": content,
            "mime": MIME_XLSX,
            "format": "excel",
            "row_count": result["row_count"],
            "display_name": f"Monthly Attendance ({label})",
        }
    ]


# ---------------------------------------------------------------------------
# Monthly location
# ---------------------------------------------------------------------------

def _monthly_loc_data(location_id, period, site_id, request):
    from scheduler.views import _get_monthly_location_summary_data_v2

    return _get_monthly_location_summary_data_v2(
        location_id=str(location_id),
        year=period["year"],
        month=period["month"],
        request=request,
        site_id=str(site_id) if site_id else None,
    )


def _monthly_loc_has_data(location_id, period, site_id, request):
    result = _monthly_loc_data(location_id, period, site_id, request)
    rows = result.get("rows") or []
    return len(rows) > 0


def _monthly_loc_generate(location_id, period, site_id, request, send_pdf, send_excel, label):
    if not _monthly_loc_has_data(location_id, period, site_id, request):
        return []
    if not send_excel:
        return []

    res = _monthly_loc_data(location_id, period, site_id, request)
    wb = Workbook()
    ws = wb.active
    ws.title = "Monthly Location"
    ws.append(res["headers"])
    for row in res.get("rows") or []:
        ws.append(
            [row.get("name"), row.get("employee_code"), row.get("designation"), row.get("location")]
            + [row.get(day, "-") for day in res.get("days") or []]
        )
    buf = BytesIO()
    wb.save(buf)
    month_str = period.get("month_str") or f"{period['year']}-{period['month']:02d}"
    return [
        {
            "filename": f"monthly_location_{_safe_name(label)}_{month_str}.xlsx",
            "content": buf.getvalue(),
            "mime": MIME_XLSX,
            "format": "excel",
            "row_count": len(res.get("rows") or []),
            "display_name": f"Monthly Location ({label})",
        }
    ]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_HANDLERS = {
    Item.REPORT_CHECKIN: (_checkin_has_data, _checkin_generate),
    Item.REPORT_ATTENDANCE: (_attendance_has_data, _attendance_generate),
    Item.REPORT_ROLLCALL: (_rollcall_has_data, _rollcall_generate),
    Item.REPORT_INCIDENT: (_incident_has_data, _incident_generate),
    Item.REPORT_VISITOR_ENTRIES: (_visitor_has_data, _visitor_generate),
    Item.REPORT_VEHICLE_MOVEMENT: (_vehicle_has_data, _vehicle_generate),
    Item.REPORT_MONTHLY_ATTENDANCE: (_monthly_att_has_data, _monthly_att_generate),
    Item.REPORT_MONTHLY_LOCATION: (_monthly_loc_has_data, _monthly_loc_generate),
}


def report_has_data(report_code, location, period, site_id=None, user=None):
    handler = _HANDLERS.get(report_code)
    if not handler:
        return False
    has_fn, _ = handler
    request = _fake_req(user, location.id, period, site_id=site_id)
    try:
        return bool(has_fn(location.id, period, site_id, request))
    except Exception:
        logger.exception("report_has_data failed for %s", report_code)
        return False


def report_generate(
    report_code,
    location,
    period,
    site_id=None,
    site_name=None,
    user=None,
    send_pdf=False,
    send_excel=False,
):
    """
    Build attachments only when data exists (has_data checked first).
    Returns list of attachment dicts.
    """
    meta = REPORT_CATALOG_BY_CODE.get(report_code) or {}
    if send_pdf and not meta.get("supports_pdf"):
        send_pdf = False
    if send_excel and not meta.get("supports_excel"):
        send_excel = False
    if not send_pdf and not send_excel:
        return []

    handler = _HANDLERS.get(report_code)
    if not handler:
        return []
    has_fn, gen_fn = handler
    request = _fake_req(user, location.id, period, site_id=site_id)
    label = site_name or location.name

    try:
        if not has_fn(location.id, period, site_id, request):
            return []
        return gen_fn(location.id, period, site_id, request, send_pdf, send_excel, label) or []
    except Exception:
        logger.exception("report_generate failed for %s", report_code)
        raise
