"""Visitor v5 APIs — site_id on creates; site filter on list/export. Live URLs unchanged."""

from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.site_access import assert_caller_can_access_site, get_site_or_error

from .exports import generate_visitor_excel, generate_visitor_pdf
from .serializers_v5 import VisitorEntrySerializerV5
from .site_filter import apply_visitor_site_filter
from .utils import resolve_location_for_request
from .vehicle_movement_report import (
    build_vehicle_movement_report,
    generate_vehicle_movement_excel,
    generate_vehicle_movement_pdf,
)
from .views import (
    VisitorApproveView,
    VisitorCancelView,
    VisitorCheckInView,
    VisitorCheckOutView,
    VisitorCompleteInviteView,
    VisitorInviteCreateView,
    VisitorPassDownloadView,
    VisitorQrScanView,
    VisitorRescheduleView,
    VisitorRevertView,
    VisitorSearchView,
    _base_entry_qs,
    _can_access_entry,
    _filtered_entries,
    _is_host_or_super,
)


def _v5_error(exc):
    if isinstance(exc, ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": detail}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, PermissionDenied):
        return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    raise exc


def _require_site(request, *, required=True):
    site_id = None
    if hasattr(request, "data"):
        try:
            site_id = request.data.get("site_id")
        except Exception:
            site_id = None
    if not site_id:
        site_id = request.query_params.get("site_id")
    if not site_id or str(site_id).lower() in ("all", "null", "undefined"):
        if required:
            raise ValidationError({"site_id": ["This field is required."]})
        return None
    site = get_site_or_error(site_id)
    assert_caller_can_access_site(request.user, site)
    return site


def _inject_location_id(request, location_id):
    """Force location_id from the selected site for live create helpers."""
    data = request.data
    if hasattr(data, "_mutable"):
        was = data._mutable
        data._mutable = True
        data["location_id"] = str(location_id)
        data._mutable = was
    else:
        try:
            data["location_id"] = str(location_id)
        except (TypeError, AttributeError):
            pass


def _inject_location_id_query(request, location_id):
    q = request.query_params.copy()
    q["location_id"] = str(location_id)
    request._request.GET = q


def _v5_entry_payload(entry, request):
    return VisitorEntrySerializerV5(entry, context={"request": request}).data


def _attach_site_from_response(response, request, site):
    """After live create/update, set site and return v5 entry JSON."""
    if response.status_code not in (status.HTTP_200_OK, status.HTTP_201_CREATED):
        # QR scan may return 400 with entry payload — still upgrade entry if present.
        data = getattr(response, "data", None)
        if not isinstance(data, dict) or "entry" not in data:
            return response
    data = response.data
    if not isinstance(data, dict):
        return response

    entry_id = data.get("id")
    if not entry_id and isinstance(data.get("entry"), dict):
        entry_id = data["entry"].get("id")
    if not entry_id:
        return response

    entry = _base_entry_qs().filter(id=entry_id).first()
    if not entry:
        return response

    if site and str(entry.site_id or "") != str(site.id):
        entry.site = site
        entry.location = site.location
        entry.save(update_fields=["site", "location"])
        entry = _base_entry_qs().get(id=entry.id)

    if "entry" in data and isinstance(data["entry"], dict):
        out = dict(data)
        out["entry"] = _v5_entry_payload(entry, request)
        return Response(out, status=response.status_code)

    payload = _v5_entry_payload(entry, request)
    for key, value in data.items():
        if key not in payload:
            payload[key] = value
    return Response(payload, status=response.status_code)


def _assert_entry_site_access(request, entry):
    if not _can_access_entry(request, entry):
        raise PermissionDenied("Forbidden")
    if not entry.site_id:
        return
    if _is_host_or_super(request, entry):
        return
    assert_caller_can_access_site(request.user, entry.site)


# ---------------------------------------------------------------------------
# Create
# ---------------------------------------------------------------------------


class VisitorCheckInViewV5(VisitorCheckInView):
    """POST /visitors/v5/entries/checkin/ — site_id required."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        try:
            site = _require_site(request, required=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        _inject_location_id(request, site.location_id)
        response = super().post(request)
        return _attach_site_from_response(response, request, site)


class VisitorInviteCreateViewV5(VisitorInviteCreateView):
    """POST /visitors/v5/entries/invite/ — site_id required."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request):
        try:
            site = _require_site(request, required=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        _inject_location_id(request, site.location_id)
        response = super().post(request)
        return _attach_site_from_response(response, request, site)


class VisitorCompleteInviteViewV5(VisitorCompleteInviteView):
    """POST /visitors/v5/entries/<id>/complete-invite/."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if not entry:
                return Response(
                    {"error": "Visitor entry not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            _assert_entry_site_access(request, entry)
            if entry.site_id:
                raw = request.data.get("site_id")
                if raw and str(raw).lower() not in ("all", "null", "undefined"):
                    posted = _require_site(request, required=True)
                    if str(posted.id) != str(entry.site_id):
                        return Response(
                            {"error": "Entry does not belong to this site"},
                            status=status.HTTP_403_FORBIDDEN,
                        )
                site = entry.site
            else:
                site = _require_site(request, required=True)
                if str(site.location_id) != str(entry.location_id):
                    return Response(
                        {"error": "Site does not belong to this entry's organisation"},
                        status=status.HTTP_403_FORBIDDEN,
                    )
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        response = super().post(request, entry_id)
        return _attach_site_from_response(response, request, site)


# ---------------------------------------------------------------------------
# List / detail / search / export
# ---------------------------------------------------------------------------


class VisitorEntryListViewV5(APIView):
    """GET /visitors/v5/entries/ — live filters plus site_id."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            qs, err = _filtered_entries(request)
            if err:
                return err
            qs = apply_visitor_site_filter(qs, request)
            serializer = VisitorEntrySerializerV5(qs, many=True, context={"request": request})
            return Response(serializer.data)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class VisitorEntryDetailViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if not entry:
                return Response(
                    {"error": "Visitor entry not found"},
                    status=status.HTTP_404_NOT_FOUND,
                )
            _assert_entry_site_access(request, entry)
            return Response(_v5_entry_payload(entry, request))
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class VisitorSearchViewV5(VisitorSearchView):
    """GET /visitors/v5/search/ — optional site_id forces that site's org for IC lookup."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            site = _require_site(request, required=False)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        if site:
            _inject_location_id_query(request, site.location_id)
        return super().get(request)


class VisitorEntryExportViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            qs, err = _filtered_entries(request)
            if err:
                return err
            qs = apply_visitor_site_filter(qs, request)
            return generate_visitor_excel(qs, request, include_site=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class VisitorEntryExportPdfViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            qs, err = _filtered_entries(request)
            if err:
                return err
            qs = apply_visitor_site_filter(qs, request)
            return generate_visitor_pdf(qs, request, include_site=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


# ---------------------------------------------------------------------------
# Lifecycle (mobile + web)
# ---------------------------------------------------------------------------


class VisitorApproveViewV5(VisitorApproveView):
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if entry:
                _assert_entry_site_access(request, entry)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        response = super().post(request, entry_id)
        return _attach_site_from_response(response, request, site=None)


class VisitorRevertViewV5(VisitorRevertView):
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if entry:
                _assert_entry_site_access(request, entry)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        response = super().post(request, entry_id)
        return _attach_site_from_response(response, request, site=None)


class VisitorCancelViewV5(VisitorCancelView):
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if entry:
                _assert_entry_site_access(request, entry)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        response = super().post(request, entry_id)
        return _attach_site_from_response(response, request, site=None)


class VisitorRescheduleViewV5(VisitorRescheduleView):
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if entry:
                _assert_entry_site_access(request, entry)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        response = super().post(request, entry_id)
        return _attach_site_from_response(response, request, site=None)


class VisitorCheckOutViewV5(VisitorCheckOutView):
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if entry:
                _assert_entry_site_access(request, entry)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        response = super().post(request, entry_id)
        return _attach_site_from_response(response, request, site=None)


class VisitorPassDownloadViewV5(VisitorPassDownloadView):
    def get(self, request, entry_id):
        try:
            entry = _base_entry_qs().filter(id=entry_id).first()
            if entry:
                _assert_entry_site_access(request, entry)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        return super().get(request, entry_id)


class VisitorQrScanViewV5(VisitorQrScanView):
    """POST /visitors/v5/qr-scan/ — entry payload includes site_id / site_name."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request):
        response = super().post(request)
        return _attach_site_from_response(response, request, site=None)


# ---------------------------------------------------------------------------
# Vehicle movement
# ---------------------------------------------------------------------------


def _vehicle_movement_report_from_request_v5(request):
    location_id_param = request.query_params.get("location_id") or request.query_params.get(
        "location"
    )
    location_id, err = resolve_location_for_request(request, location_id_param)
    if err:
        return None, Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

    site = None
    raw_site = request.query_params.get("site_id")
    if raw_site and str(raw_site).lower() not in ("all", "null", "undefined"):
        try:
            site = get_site_or_error(raw_site)
            assert_caller_can_access_site(request.user, site)
            if location_id and str(site.location_id) != str(location_id):
                raise ValidationError({"site_id": "Site does not belong to that organisation."})
            location_id = str(site.location_id)
        except (ValidationError, PermissionDenied) as exc:
            return None, _v5_error(exc)

    if not location_id:
        return None, Response(
            {"error": "location_id is required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    date_filter = request.query_params.get("date_filter") or "today"
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    start_time = request.query_params.get("start_time")
    end_time = request.query_params.get("end_time")

    report_date_raw = request.query_params.get("report_date")
    if report_date_raw and not start_date:
        start_date = report_date_raw
        end_date = report_date_raw
        if not request.query_params.get("date_filter"):
            date_filter = "custom"

    try:
        data = build_vehicle_movement_report(
            request,
            location_id,
            date_filter=date_filter,
            start_date_str=start_date,
            end_date_str=end_date,
            start_time_str=start_time,
            end_time_str=end_time,
            site_id=str(site.id) if site else None,
        )
    except ValueError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    data["site_id"] = str(site.id) if site else None
    data["site_name"] = site.name if site else None
    return data, None


class VehicleMovementReportViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data, error_response = _vehicle_movement_report_from_request_v5(request)
        if error_response:
            return error_response
        return Response(data)


class VehicleMovementReportExportViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data, error_response = _vehicle_movement_report_from_request_v5(request)
        if error_response:
            return error_response
        return generate_vehicle_movement_excel(data)


class VehicleMovementReportExportPdfViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        data, error_response = _vehicle_movement_report_from_request_v5(request)
        if error_response:
            return error_response
        return generate_vehicle_movement_pdf(data, request=request)
