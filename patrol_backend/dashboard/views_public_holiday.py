"""Public holiday calendar CRUD, Excel template/upload, and worked-on-holiday report."""

from __future__ import annotations

import json
import logging
from calendar import monthrange
from datetime import date, datetime, time, timedelta
from io import BytesIO

from django.db import transaction
from django.db.models import Q
from django.http import HttpResponse
from django.utils import timezone
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authapp.site_access import allowed_site_qs, assert_caller_can_access_site
from dashboard.attendance_pa_status import PA_STATUS_LABELS
from dashboard.models import AttendanceCheckin, CheckInLog, PublicHoliday
from dashboard.public_holiday import (
    backfill_for_holiday,
    clear_for_holiday,
    parse_holiday_date,
    upsert_public_holiday,
)
from dashboard.site_filter import apply_dashboard_attendance_site_filter, resolve_dashboard_location_id
from scheduler.models import Location, LocationSite, Vendor

logger = logging.getLogger(__name__)

_THIN = Border(
    left=Side(style="thin", color="CBD5E1"),
    right=Side(style="thin", color="CBD5E1"),
    top=Side(style="thin", color="CBD5E1"),
    bottom=Side(style="thin", color="CBD5E1"),
)


def _serialize_holiday(obj):
    return {
        "id": str(obj.id),
        "holiday_date": obj.holiday_date.isoformat() if obj.holiday_date else None,
        "name": obj.name,
        "org_location_id": str(obj.org_location_id) if obj.org_location_id else None,
        "site_id": str(obj.site_id) if obj.site_id else None,
        "site_name": obj.site.name if obj.site else None,
        "vendor_id": str(obj.vendor_id) if obj.vendor_id else None,
        "vendor_name": obj.vendor.name if obj.vendor else None,
        "is_site_wide": obj.vendor_id is None,
    }


def _resolve_org_location_id(request):
    raw = (
        request.query_params.get("location_id")
        or request.query_params.get("location")
        or (getattr(request, "data", None) or {}).get("location_id")
        or (getattr(request, "data", None) or {}).get("location")
    )
    if raw and str(raw).lower() in ("all", "null", "undefined"):
        raw = None
    caller = request.user
    if caller.is_superuser:
        return str(raw) if raw else None
    org_id = str(getattr(caller, "location_id", "") or "") or (str(raw) if raw else None)
    if raw and org_id and str(raw) != org_id:
        return None
    return org_id


def _allowed_sites_for_org(request, location_id):
    qs = allowed_site_qs(request.user).filter(location_id=location_id)
    return qs.order_by("name")


def _site_or_error(request, site_id, location_id):
    site = LocationSite.objects.filter(id=site_id, is_active=True).select_related("location").first()
    if not site:
        return None, Response({"error": "Site not found or inactive."}, status=status.HTTP_400_BAD_REQUEST)
    if str(site.location_id) != str(location_id):
        return None, Response({"error": "Site does not belong to that organisation."}, status=status.HTTP_400_BAD_REQUEST)
    try:
        assert_caller_can_access_site(request.user, site)
    except PermissionDenied as exc:
        return None, Response({"error": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)
    return site, None


def _as_bool(raw, default=None):
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _parse_vendor_ids(raw):
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return [text]
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    out = []
    for item in raw:
        if item in (None, "", "null", "none", "all"):
            continue
        out.append(str(item))
    return out


class PublicHolidayViewSet(viewsets.ViewSet):
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def list(self, request):
        location_id = _resolve_org_location_id(request)
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        allowed = _allowed_sites_for_org(request, location_id)
        allowed_ids = list(allowed.values_list("id", flat=True))
        qs = (
            PublicHoliday.objects.filter(org_location_id=location_id, site_id__in=allowed_ids)
            .select_related("site", "vendor", "org_location")
            .order_by("holiday_date", "site__name", "vendor__name")
        )

        month = request.query_params.get("month")
        start = request.query_params.get("start_date")
        end = request.query_params.get("end_date")
        site_id = request.query_params.get("site_id")
        if month:
            try:
                year, month_num = map(int, str(month).split("-"))
                start_d = date(year, month_num, 1)
                end_d = date(year, month_num, monthrange(year, month_num)[1])
                qs = qs.filter(holiday_date__gte=start_d, holiday_date__lte=end_d)
            except (ValueError, TypeError):
                return Response({"error": "month must be YYYY-MM"}, status=status.HTTP_400_BAD_REQUEST)
        else:
            if start:
                parsed = parse_holiday_date(start)
                if parsed:
                    qs = qs.filter(holiday_date__gte=parsed)
            if end:
                parsed = parse_holiday_date(end)
                if parsed:
                    qs = qs.filter(holiday_date__lte=parsed)
        if site_id and str(site_id).lower() not in ("all", "null", ""):
            qs = qs.filter(site_id=site_id)

        return Response({"results": [_serialize_holiday(row) for row in qs]}, status=status.HTTP_200_OK)

    def _get_holiday(self, request, pk):
        obj = PublicHoliday.objects.select_related("site", "vendor").filter(id=pk).first()
        if not obj:
            return None, Response({"error": "Holiday not found"}, status=status.HTTP_404_NOT_FOUND)
        try:
            assert_caller_can_access_site(request.user, obj.site)
        except PermissionDenied as exc:
            return None, Response({"error": str(exc.detail)}, status=status.HTTP_403_FORBIDDEN)
        return obj, None

    def retrieve(self, request, pk=None):
        obj, err = self._get_holiday(request, pk)
        if err:
            return err
        return Response(_serialize_holiday(obj))

    def create(self, request):
        return self._bulk_save(request)

    def partial_update(self, request, pk=None):
        obj, err = self._get_holiday(request, pk)
        if err:
            return err
        name = (request.data.get("name") or obj.name or "").strip()
        if not name:
            return Response({"error": "name is required"}, status=status.HTTP_400_BAD_REQUEST)

        has_scope = any(key in request.data for key in ("apply_all_users", "vendor_ids", "vendor_id"))
        if not has_scope:
            obj.name = name
            obj.modified_by = request.user
            obj.save(update_fields=["name", "modified_by", "modified_on"])
            backfill_for_holiday(obj)
            return Response(_serialize_holiday(obj))

        apply_all = _as_bool(request.data.get("apply_all_users"), default=None)
        vendor_ids = _parse_vendor_ids(request.data.get("vendor_ids") or request.data.get("vendor_id"))
        if apply_all is True or (apply_all is not False and not vendor_ids):
            vendor_targets = [None]
        else:
            if not vendor_ids:
                return Response(
                    {"error": "Pick at least one vendor, or choose All users."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            vendors = list(
                Vendor.objects.filter(
                    id__in=vendor_ids,
                    location_id=obj.org_location_id,
                    is_deleted=False,
                )
            )
            found = {str(v.id) for v in vendors}
            missing = [vid for vid in vendor_ids if vid not in found]
            if missing:
                return Response(
                    {"error": f"Unknown vendor(s) for this organisation: {', '.join(missing)}"},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            vendor_targets = vendors

        original_name = obj.name
        site = obj.site
        holiday_date = obj.holiday_date
        location_id = obj.org_location_id
        original_pk = obj.pk
        saved = []
        with transaction.atomic():
            for vendor in vendor_targets:
                row, _created = upsert_public_holiday(
                    org_location_id=location_id,
                    site=site,
                    vendor=vendor,
                    holiday_date=holiday_date,
                    name=name,
                    user=request.user,
                )
                saved.append(row)
            kept_ids = {row.pk for row in saved}
            stale = list(
                PublicHoliday.objects.filter(site_id=site.id, holiday_date=holiday_date)
                .filter(Q(pk=original_pk) | Q(name=original_name))
                .exclude(pk__in=kept_ids)
            )
            for old in stale:
                old.delete()
                clear_for_holiday(old)
            for row in saved:
                backfill_for_holiday(row)

        payload = _serialize_holiday(saved[0]) if saved else _serialize_holiday(obj)
        payload["results"] = [_serialize_holiday(row) for row in saved]
        return Response(payload)

    def destroy(self, request, pk=None):
        obj, err = self._get_holiday(request, pk)
        if err:
            return err
        snapshot = obj
        obj.delete()
        clear_for_holiday(snapshot)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=["post"], url_path="bulk")
    def bulk(self, request):
        return self._bulk_save(request)

    def _bulk_save(self, request):
        location_id = _resolve_org_location_id(request)
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        loc = Location.objects.filter(id=location_id, is_deleted=False).first()
        if not loc:
            return Response({"error": "Organisation not found"}, status=status.HTTP_404_NOT_FOUND)

        holidays = request.data.get("holidays") or request.data.get("dates") or []
        sites_payload = request.data.get("sites") or request.data.get("assignments") or []
        if not holidays:
            return Response({"error": "holidays is required (list of {date, name})"}, status=status.HTTP_400_BAD_REQUEST)
        if not sites_payload:
            return Response({"error": "sites is required (list of {site_id, vendor_ids})"}, status=status.HTTP_400_BAD_REQUEST)

        parsed_holidays = []
        errors = []
        for idx, item in enumerate(holidays, start=1):
            if not isinstance(item, dict):
                errors.append({"row": idx, "error": "Each holiday must be an object with date and name"})
                continue
            d = parse_holiday_date(item.get("date") or item.get("holiday_date"))
            name = (item.get("name") or item.get("holiday_name") or "").strip()
            if not d:
                errors.append({"row": idx, "error": "Invalid or missing date"})
                continue
            if not name:
                errors.append({"row": idx, "error": "Holiday name is required"})
                continue
            parsed_holidays.append((d, name))

        if not parsed_holidays:
            return Response({"error": "No valid holiday rows", "row_errors": errors}, status=status.HTTP_400_BAD_REQUEST)

        created = 0
        updated = 0
        saved = []
        with transaction.atomic():
            for site_row in sites_payload:
                if not isinstance(site_row, dict):
                    continue
                site_id = site_row.get("site_id") or site_row.get("id")
                site, err = _site_or_error(request, site_id, location_id)
                if err:
                    return err
                vendor_ids = _parse_vendor_ids(site_row.get("vendor_ids"))
                vendors = []
                if vendor_ids:
                    vendors = list(
                        Vendor.objects.filter(
                            id__in=vendor_ids,
                            location_id=location_id,
                            is_deleted=False,
                        )
                    )
                    found = {str(v.id) for v in vendors}
                    missing = [vid for vid in vendor_ids if vid not in found]
                    if missing:
                        return Response(
                            {"error": f"Unknown vendor(s) for this organisation: {', '.join(missing)}"},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    vendor_targets = vendors
                else:
                    vendor_targets = [None]

                for holiday_date, name in parsed_holidays:
                    for vendor in vendor_targets:
                        obj, was_created = upsert_public_holiday(
                            org_location_id=location_id,
                            site=site,
                            vendor=vendor,
                            holiday_date=holiday_date,
                            name=name,
                            user=request.user,
                        )
                        if was_created:
                            created += 1
                        else:
                            updated += 1
                        saved.append(obj)

        for obj in saved:
            backfill_for_holiday(obj)

        return Response(
            {
                "created": created,
                "updated": updated,
                "count": created + updated,
                "row_errors": errors,
                "results": [_serialize_holiday(o) for o in saved],
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )

    @action(detail=False, methods=["get"], url_path="template")
    def template(self, request):
        buf = _build_sample_excel()
        response = HttpResponse(
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = 'attachment; filename="public_holiday_sample.xlsx"'
        return response

    @action(detail=False, methods=["post"], url_path="upload")
    def upload(self, request):
        location_id = _resolve_org_location_id(request)
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        upload = request.FILES.get("file") or request.FILES.get("excel")
        if not upload:
            return Response({"error": "Excel file is required"}, status=status.HTTP_400_BAD_REQUEST)

        sites_raw = request.data.get("sites")
        if isinstance(sites_raw, str):
            try:
                sites_raw = json.loads(sites_raw)
            except json.JSONDecodeError:
                return Response({"error": "sites must be JSON"}, status=status.HTTP_400_BAD_REQUEST)
        if not sites_raw:
            return Response({"error": "Select at least one site before uploading"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            wb = load_workbook(upload, data_only=True)
        except Exception:
            return Response({"error": "Could not read Excel file"}, status=status.HTTP_400_BAD_REQUEST)

        ws = wb["Holidays"] if "Holidays" in wb.sheetnames else wb.active
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            return Response({"error": "Excel sheet is empty"}, status=status.HTTP_400_BAD_REQUEST)

        header = [str(c or "").strip().lower() for c in rows[0]]
        date_idx = _header_index(header, ("date", "holiday date", "holiday_date"))
        name_idx = _header_index(header, ("holiday name", "name", "holiday"))
        if date_idx is None or name_idx is None:
            return Response(
                {"error": "Excel must have columns Date and Holiday Name. Download the sample template."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        holidays = []
        row_errors = []
        for i, row in enumerate(rows[1:], start=2):
            if not row or all(c is None or str(c).strip() == "" for c in row):
                continue
            d = parse_holiday_date(row[date_idx] if date_idx < len(row) else None)
            name = str(row[name_idx] if name_idx < len(row) else "" or "").strip()
            if not d:
                row_errors.append({"row": i, "error": "Invalid or missing date (use YYYY-MM-DD)"})
                continue
            if not name:
                row_errors.append({"row": i, "error": "Holiday name is required"})
                continue
            holidays.append({"date": d.isoformat(), "name": name})

        if not holidays:
            return Response(
                {"error": "No valid holiday rows in the file", "row_errors": row_errors},
                status=status.HTTP_400_BAD_REQUEST,
            )

        fake = _RequestDataProxy(
            request,
            {
                "location_id": location_id,
                "holidays": holidays,
                "sites": sites_raw,
            },
        )
        return self._bulk_save(fake)

    @action(detail=False, methods=["get"], url_path="worked")
    def worked(self, request):
        qs = _worked_queryset(request)
        try:
            page = max(1, int(request.query_params.get("page", 1)))
            page_size = min(500, max(1, int(request.query_params.get("page_size", 25))))
        except (ValueError, TypeError):
            page, page_size = 1, 25
        total = qs.count()
        start = (page - 1) * page_size
        page_items = list(qs[start : start + page_size])
        rows = _serialize_worked_rows_with_pairs(page_items, request)
        return Response(
            {
                "count": total,
                "page": page,
                "page_size": page_size,
                "total_pages": (total + page_size - 1) // page_size if page_size else 1,
                "results": rows,
            }
        )

    @action(detail=False, methods=["get"], url_path=r"worked/(?P<attendance_id>[^/.]+)")
    def worked_detail(self, request, attendance_id=None):
        qs = (
            AttendanceCheckin.objects.filter(worked_on_public_holiday=True, pk=attendance_id)
            .select_related("guard", "guard__vendor", "site", "shift", "org_location")
        )
        location_id = resolve_dashboard_location_id(request)
        if location_id:
            qs = qs.filter(org_location_id=location_id)
        qs = apply_dashboard_attendance_site_filter(qs, request)
        att = qs.first()
        if not att:
            return Response({"error": "Record not found"}, status=status.HTTP_404_NOT_FOUND)
        rows = _serialize_worked_rows_with_pairs([att], request)
        return Response(rows[0] if rows else {"error": "Record not found"})

    @action(detail=False, methods=["get"], url_path="worked/export-excel")
    def worked_export_excel(self, request):
        qs = _worked_queryset(request)
        buf = generate_worked_excel(qs, request)
        start = request.query_params.get("start_date") or ""
        end = request.query_params.get("end_date") or ""
        filename = (
            f"public_holiday_worked_{start}_{end}.xlsx"
            if start and end
            else f"public_holiday_worked_{timezone.now().strftime('%Y%m%d')}.xlsx"
        )
        response = HttpResponse(
            buf.getvalue(),
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response

    @action(detail=False, methods=["get"], url_path="worked/export-pdf")
    def worked_export_pdf(self, request):
        qs = _worked_queryset(request)
        buf = generate_worked_pdf(qs, request)
        start = request.query_params.get("start_date") or ""
        end = request.query_params.get("end_date") or ""
        filename = (
            f"public_holiday_worked_{start}_{end}.pdf"
            if start and end
            else f"public_holiday_worked_{timezone.now().strftime('%Y%m%d')}.pdf"
        )
        response = HttpResponse(buf.getvalue(), content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class _RequestDataProxy:
    """Minimal request stand-in so bulk save can reuse parsed Excel rows."""

    def __init__(self, request, data):
        self.user = request.user
        self.data = data
        self.query_params = request.query_params
        self.FILES = getattr(request, "FILES", None)


def _header_index(header, names):
    for i, col in enumerate(header):
        if col in names:
            return i
    return None


def _build_sample_excel():
    wb = Workbook()
    inst = wb.active
    inst.title = "Instructions"
    inst["A1"] = "Public Holiday Excel — how to fill this file"
    inst["A1"].font = Font(name="Calibri", size=14, bold=True, color="5B21B6")
    instructions = [
        "",
        "1. Use the Holidays sheet only. Do not rename the header row.",
        "2. Date format: YYYY-MM-DD (example: 2026-01-26). DD-MM-YYYY is also accepted.",
        "3. Holiday Name is required on every row (example: Republic Day).",
        "4. Sites and vendors are NOT in this file. Select them in the app when you upload.",
        "5. Delete the sample rows and add your holidays, then save as .xlsx.",
        "6. One file can list many dates; they will be applied to the sites/vendors you selected.",
    ]
    for i, line in enumerate(instructions, start=2):
        inst[f"A{i}"] = line
        inst[f"A{i}"].font = Font(name="Calibri", size=11)
    inst.column_dimensions["A"].width = 110

    ws = wb.create_sheet("Holidays")
    headers = ["Date", "Holiday Name"]
    ws.append(headers)
    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="7C3AED", end_color="7C3AED", fill_type="solid")
    sample_fill = PatternFill(start_color="F5F3FF", end_color="F5F3FF", fill_type="solid")
    for col_idx, _ in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", vertical="center")
        cell.border = _THIN
    samples = [
        ("2026-01-26", "Republic Day"),
        ("2026-08-15", "Independence Day"),
        ("2026-10-02", "Gandhi Jayanti"),
    ]
    for row_idx, (d, name) in enumerate(samples, start=2):
        ws.cell(row=row_idx, column=1, value=d)
        ws.cell(row=row_idx, column=2, value=name)
        for col_idx in (1, 2):
            cell = ws.cell(row=row_idx, column=col_idx)
            cell.font = Font(name="Calibri", size=11)
            cell.fill = sample_fill
            cell.border = _THIN
            cell.alignment = Alignment(horizontal="left", vertical="center")
    ws.column_dimensions["A"].width = 18
    ws.column_dimensions["B"].width = 28
    ws.row_dimensions[1].height = 22
    note = ws.cell(row=6, column=1, value="(These three rows are examples — replace them with your holidays.)")
    note.font = Font(name="Calibri", size=9, italic=True, color="64748B")
    ws.merge_cells("A6:B6")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def _worked_queryset(request):
    qs = (
        AttendanceCheckin.objects.filter(worked_on_public_holiday=True)
        .select_related("guard", "guard__vendor", "site", "shift", "org_location")
        .order_by("-shift_date", "guard__name")
    )
    location_id = resolve_dashboard_location_id(request)
    if location_id:
        qs = qs.filter(org_location_id=location_id)
    qs = apply_dashboard_attendance_site_filter(qs, request)

    start = parse_holiday_date(request.query_params.get("start_date"))
    end = parse_holiday_date(request.query_params.get("end_date"))
    if start:
        qs = qs.filter(shift_date__gte=start)
    if end:
        qs = qs.filter(shift_date__lte=end)

    user_id = request.query_params.get("user") or request.query_params.get("user_id")
    search = (request.query_params.get("search") or "").strip()
    role = (request.query_params.get("role") or "").strip()
    vendor_id = request.query_params.get("vendor_id")
    if user_id:
        qs = qs.filter(guard_id=user_id)
    elif search:
        from django.db.models import Q

        qs = qs.filter(
            Q(guard__name__icontains=search)
            | Q(guard__employee_code__icontains=search)
            | Q(public_holiday_name__icontains=search)
        )
    if role and role.lower() not in ("", "all"):
        qs = qs.filter(guard__role__iexact=role)
    if vendor_id and str(vendor_id).lower() not in ("", "all"):
        if str(vendor_id).lower() in ("none", "null"):
            qs = qs.filter(guard__vendor_id__isnull=True)
        else:
            qs = qs.filter(guard__vendor_id=vendor_id)
    return qs


def _iso_local(dt, request):
    if not dt:
        return None
    from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

    user_tz = get_user_timezone_from_request(request)
    local = to_user_timezone(dt, user_tz)
    return local.isoformat() if local else None


def _prefetched_logs_for_attendance(page_items):
    """One CheckInLog query for the current page, grouped by guard (same idea as attendance v4)."""
    if not page_items:
        return {}
    guard_ids = {obj.guard_id for obj in page_items if obj.guard_id}
    if not guard_ids:
        return {}
    dates = [obj.shift_date for obj in page_items if obj.shift_date]
    if dates:
        tz = timezone.get_current_timezone()
        start = timezone.make_aware(datetime.combine(min(dates) - timedelta(days=1), time.min), tz)
        end = timezone.make_aware(datetime.combine(max(dates) + timedelta(days=2), time.min), tz)
    else:
        stamps = [obj.checkin_time or obj.created_on for obj in page_items if (obj.checkin_time or obj.created_on)]
        if not stamps:
            return {}
        start = min(stamps) - timedelta(days=1)
        end = max(stamps) + timedelta(days=2)
    logs = CheckInLog.objects.filter(
        guard_id__in=guard_ids,
        timestamp__gte=start,
        timestamp__lt=end,
    ).order_by("timestamp")
    grouped = {}
    for log in logs:
        grouped.setdefault(log.guard_id, []).append(log)
    return grouped


def _serialize_worked_rows_with_pairs(page_items, request):
    from dashboard.serializers import AttendanceCheckinDashboardV4Serializer

    rows = [_serialize_worked_row(att, request) for att in page_items]
    if not page_items:
        return rows
    punch_data = AttendanceCheckinDashboardV4Serializer(
        page_items,
        many=True,
        context={"request": request, "prefetched_logs": _prefetched_logs_for_attendance(page_items)},
    ).data
    pairs_by_id = {str(item.get("id")): item.get("log_pairs") or [] for item in punch_data}
    for row in rows:
        row["log_pairs"] = pairs_by_id.get(str(row["id"])) or []
    return rows


def _serialize_worked_row(att, request):
    guard = att.guard
    pa = att.pa_status or ""
    last_in = att.last_checkin_time or att.checkin_time
    last_out = att.last_checkout_time
    return {
        "id": str(att.id),
        "shift_date": att.shift_date.isoformat() if att.shift_date else None,
        "public_holiday_name": att.public_holiday_name or "",
        "guard_id": str(guard.id) if guard else None,
        "guard_name": getattr(guard, "name", None) or "",
        "employee_code": getattr(guard, "employee_code", None) or "",
        "role": getattr(guard, "role", None) or "",
        "vendor_id": str(getattr(guard, "vendor_id", "") or "") or None,
        "vendor_name": getattr(getattr(guard, "vendor", None), "name", None) or "",
        "site_id": str(att.site_id) if att.site_id else None,
        "site_name": att.site.name if att.site else "",
        "org_location_name": att.org_location.name if att.org_location else "",
        "shift_name": att.shift.name if att.shift else "",
        "checkin_time": _iso_local(last_in, request),
        "checkout_time": _iso_local(last_out, request),
        "duration_minutes": att.duration_minutes,
        "checkin_count": int(att.checkin_count or 0),
        "checkout_count": int(att.checkout_count or 0),
        "live_state": "checked_in" if (
            last_in and (not last_out or last_in > last_out)
        ) else "checked_out",
        "pa_status": pa,
        "pa_status_label": PA_STATUS_LABELS.get(pa, pa),
    }


WORKED_EXPORT_HEADERS = [
    "#",
    "Date",
    "Holiday",
    "Guard",
    "Emp Code",
    "Role",
    "Vendor",
    "Site",
    "Shift",
    "Check-In",
    "Check-Out",
    "Duration",
    "Status",
]


def _fmt_time(iso_str):
    if not iso_str:
        return "—"
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M")
    except Exception:
        return iso_str


def _fmt_duration(minutes):
    if minutes is None:
        return "—"
    try:
        total = int(minutes)
    except (TypeError, ValueError):
        return "—"
    return f"{total // 60}h {total % 60}m"


def generate_worked_excel(qs, request):
    wb = Workbook()
    ws = wb.active
    ws.title = "Public Holiday Worked"
    headers = WORKED_EXPORT_HEADERS
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
    font_data = Font(name="Calibri", size=10)
    for idx, att in enumerate(qs, start=1):
        row = _serialize_worked_row(att, request)
        values = [
            idx,
            row["shift_date"] or "—",
            row["public_holiday_name"] or "—",
            row["guard_name"] or "—",
            row["employee_code"] or "—",
            (row["role"] or "—").upper(),
            row["vendor_name"] or "—",
            row["site_name"] or "—",
            row["shift_name"] or "—",
            _fmt_time(row["checkin_time"]),
            _fmt_time(row["checkout_time"]),
            _fmt_duration(row["duration_minutes"]),
            row["pa_status_label"] or row["pa_status"] or "—",
        ]
        ws.append(values)
        current = ws.max_row
        ws.row_dimensions[current].height = 20
        for col_idx in range(1, len(values) + 1):
            cell = ws.cell(row=current, column=col_idx)
            cell.font = font_data
            cell.border = _THIN
            cell.alignment = align_center if col_idx in (1, 2, 5, 6, 10, 11, 12, 13) else align_left
    for col in ws.columns:
        letter = get_column_letter(col[0].column)
        max_len = max((len(str(c.value or "")) for c in col), default=12)
        ws.column_dimensions[letter].width = max(max_len + 4, 12)
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def generate_worked_pdf(qs, request):
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Table, TableStyle

    from patrol_backend.utils.pdf_report import draw_dashboard_pdf_header_and_footer
    from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(A4),
        leftMargin=0.2 * inch,
        rightMargin=0.2 * inch,
        topMargin=1.2 * inch,
        bottomMargin=0.35 * inch,
    )
    location_id = resolve_dashboard_location_id(request)
    org_name = "—"
    if location_id:
        loc = Location.objects.filter(id=location_id).first()
        if loc:
            org_name = loc.name
    elif getattr(request.user, "location", None):
        org_name = request.user.location.name or "—"
    user_tz = get_user_timezone_from_request(request)
    start = parse_holiday_date(request.query_params.get("start_date"))
    end = parse_holiday_date(request.query_params.get("end_date"))
    doc.report_title = "Public Holiday Worked Report"
    doc.org_name = org_name
    doc.dept_label = org_name
    doc.range_start = start
    doc.range_end = end
    doc.printed_at = to_user_timezone(timezone.now(), user_tz)

    styles = getSampleStyleSheet()

    def _para_style(name, **kwargs):
        base = dict(leftIndent=0, rightIndent=0, firstLineIndent=0, spaceBefore=0, spaceAfter=0)
        base.update(kwargs)
        return ParagraphStyle(name, **base)

    cell_style = _para_style("PhPdfCell", parent=styles["Normal"], fontSize=6.5, alignment=TA_LEFT, leading=8)
    cell_center = _para_style("PhPdfCellCenter", parent=styles["Normal"], fontSize=6.5, alignment=TA_CENTER, leading=8)
    header_style = _para_style(
        "PhPdfHeader", parent=styles["Normal"], fontSize=6.5, fontName="Helvetica-Bold", alignment=TA_CENTER, leading=8
    )
    data = [[Paragraph(h, header_style) for h in WORKED_EXPORT_HEADERS]]
    rows = list(qs)
    for idx, att in enumerate(rows, start=1):
        row = _serialize_worked_row(att, request)
        data.append(
            [
                Paragraph(str(idx), cell_center),
                Paragraph(row["shift_date"] or "—", cell_center),
                Paragraph(row["public_holiday_name"] or "—", cell_style),
                Paragraph(row["guard_name"] or "—", cell_style),
                Paragraph(row["employee_code"] or "—", cell_center),
                Paragraph((row["role"] or "—").upper(), cell_center),
                Paragraph(row["vendor_name"] or "—", cell_style),
                Paragraph(row["site_name"] or "—", cell_style),
                Paragraph(row["shift_name"] or "—", cell_style),
                Paragraph(_fmt_time(row["checkin_time"]), cell_center),
                Paragraph(_fmt_time(row["checkout_time"]), cell_center),
                Paragraph(_fmt_duration(row["duration_minutes"]), cell_center),
                Paragraph(row["pa_status_label"] or "—", cell_center),
            ]
        )
    if len(data) == 1:
        data.append([Paragraph("No public-holiday work records found", cell_style)] + [""] * 12)

    ratios = [0.04, 0.08, 0.10, 0.11, 0.07, 0.06, 0.08, 0.10, 0.08, 0.08, 0.08, 0.06, 0.06]
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
    doc.build([table], onFirstPage=draw_dashboard_pdf_header_and_footer, onLaterPages=draw_dashboard_pdf_header_and_footer)
    buffer.seek(0)
    return buffer
