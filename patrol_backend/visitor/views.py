from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.models import User
from scheduler.models import Location

from .exports import generate_visitor_excel
from .models import Visitor, VisitorAsset, VisitorEntry
from .serializers import VisitorEntrySerializer, VisitorSerializer
from .utils import (
    apply_checkin_date_filter,
    generate_qr_image_file,
    make_qr_token,
    resolve_location_for_request,
)
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
)


def _base_entry_qs():
    return (
        VisitorEntry.objects.filter(is_deleted=False)
        .select_related("visitor", "host", "location", "created_by", "approved_by")
        .prefetch_related("assets")
    )


def _save_asset(entry, asset_type, uploaded_file):
    if not uploaded_file:
        return None
    return VisitorAsset.objects.create(
        visitor_entry=entry,
        asset_type=asset_type,
        file=uploaded_file,
    )


def _save_assets_many(entry, asset_type, uploaded_files):
    created = []
    for uploaded in uploaded_files or []:
        if uploaded:
            created.append(_save_asset(entry, asset_type, uploaded))
    return created


def _parse_dt(value):
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    dt = parse_datetime(str(value).replace("Z", "+00:00"))
    if dt:
        return dt
    try:
        return datetime.strptime(str(value)[:16], "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _upsert_visitor(location, ic_number, visitor_name, phone_number):
    visitor = (
        Visitor.objects.select_for_update()
        .filter(
            is_deleted=False,
            location_id=location.id,
            ic_passport_number__iexact=ic_number,
        )
        .first()
    )
    if visitor:
        visitor.visitor_name = visitor_name
        if phone_number:
            visitor.phone_number = phone_number
        visitor.save(update_fields=["visitor_name", "phone_number", "modified_on"])
        return visitor
    return Visitor.objects.create(
        location=location,
        ic_passport_number=ic_number,
        visitor_name=visitor_name,
        phone_number=phone_number or "",
    )


def _resolve_host(request, host_id, location_id):
    if not host_id:
        return None, "host_id is required"
    try:
        host = User.objects.get(id=host_id, is_deleted=False)
    except User.DoesNotExist:
        return None, "Host not found"
    if not getattr(request.user, "is_superuser", False):
        if host.location_id and str(host.location_id) != str(location_id):
            return None, "Host must belong to the same location"
    return host, None


def _filtered_entries(request):
    location_id, err = resolve_location_for_request(
        request, request.query_params.get("location_id")
    )
    if err:
        return None, Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

    qs = _base_entry_qs()
    if location_id:
        qs = qs.filter(location_id=location_id)

    # Host inbox: only entries assigned to current user
    mine = (request.query_params.get("mine") or "").lower() in ("1", "true", "yes")
    if mine:
        qs = qs.filter(host_id=request.user.id)

    status_filter = (request.query_params.get("status") or "").strip().lower()
    if status_filter and status_filter != "all":
        qs = qs.filter(status=status_filter)

    search = (request.query_params.get("search") or "").strip()
    if search:
        qs = qs.filter(
            Q(visitor__visitor_name__icontains=search)
            | Q(visitor__ic_passport_number__icontains=search)
            | Q(visitor__phone_number__icontains=search)
            | Q(vehicle_number__icontains=search)
            | Q(purpose_of_visit__icontains=search)
            | Q(host__name__icontains=search)
        )

    date_filter = request.query_params.get("date_filter", "today")
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    try:
        qs = apply_checkin_date_filter(
            qs, request, location_id, date_filter, start_date, end_date
        )
    except ValueError as exc:
        return None, Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    return qs, None


def _can_access_entry(request, entry):
    if getattr(request.user, "is_superuser", False):
        return True
    user_loc = getattr(request.user, "location_id", None)
    if user_loc and str(entry.location_id) != str(user_loc):
        return False
    return True


def _is_host_or_super(request, entry):
    if getattr(request.user, "is_superuser", False):
        return True
    return entry.host_id and str(entry.host_id) == str(request.user.id)


class VisitorSearchView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        ic_number = (
            request.query_params.get("ic_number")
            or request.query_params.get("ic_passport_number")
            or ""
        ).strip()
        if not ic_number:
            return Response({"error": "ic_number is required"}, status=status.HTTP_400_BAD_REQUEST)

        location_id, err = resolve_location_for_request(
            request, request.query_params.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required for superadmin"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        visitor = (
            Visitor.objects.filter(
                is_deleted=False,
                location_id=location_id,
                ic_passport_number__iexact=ic_number,
            )
            .select_related("location")
            .first()
        )
        if not visitor:
            return Response({"found": False, "visitor": None})
        return Response(
            {
                "found": True,
                "visitor": VisitorSerializer(visitor, context={"request": request}).data,
            }
        )


class VisitorCheckInView(APIView):
    """
    POST /visitors/entries/checkin/
    Manual entry: creates pending_approval + QR. Host approval = check-in.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request):
        data = request.data
        location_id, err = resolve_location_for_request(request, data.get("location_id"))
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            location = Location.objects.get(id=location_id, is_deleted=False)
        except Location.DoesNotExist:
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

        ic_number = (data.get("ic_passport_number") or data.get("ic_number") or "").strip()
        visitor_name = (data.get("visitor_name") or "").strip()
        if not ic_number:
            return Response(
                {"error": "ic_passport_number is required"}, status=status.HTTP_400_BAD_REQUEST
            )
        if not visitor_name:
            return Response(
                {"error": "visitor_name is required"}, status=status.HTTP_400_BAD_REQUEST
            )

        visitor_type = (data.get("visitor_type") or VisitorEntry.TYPE_GUEST).strip()
        valid_types = {c[0] for c in VisitorEntry.VISITOR_TYPE_CHOICES}
        if visitor_type not in valid_types:
            return Response(
                {"error": f"Invalid visitor_type. Choose from: {', '.join(sorted(valid_types))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        host, host_err = _resolve_host(request, data.get("host_id"), location_id)
        if host_err:
            return Response({"error": host_err}, status=status.HTTP_400_BAD_REQUEST)

        phone_number = (data.get("phone_number") or "").strip()
        purpose = (data.get("purpose_of_visit") or "").strip()
        vehicle_number = (data.get("vehicle_number") or "").strip()
        remarks = (data.get("remarks") or "").strip()
        expected_arrival = _parse_dt(data.get("expected_arrival_time"))
        expected_out = _parse_dt(data.get("expected_out_time"))

        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        visit_date_today = get_user_today(user_tz)

        visitor = _upsert_visitor(location, ic_number, visitor_name, phone_number)

        # Resubmit after revert: update existing reverted entry if entry_id provided
        entry_id = data.get("entry_id")
        entry = None
        if entry_id:
            try:
                entry = (
                    VisitorEntry.objects.select_for_update()
                    .filter(id=entry_id, is_deleted=False)
                    .first()
                )
            except Exception:
                entry = None
            if entry and entry.status != VisitorEntry.STATUS_REVERTED:
                return Response(
                    {"error": "Only reverted entries can be resubmitted"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        try:
            if entry:
                entry.visitor = visitor
                entry.host = host
                entry.visitor_type = visitor_type
                entry.purpose_of_visit = purpose
                entry.vehicle_number = vehicle_number
                entry.remarks = remarks
                entry.expected_arrival_time = expected_arrival
                entry.expected_out_time = expected_out
                entry.visit_date = visit_date_today
                entry.status = VisitorEntry.STATUS_PENDING_APPROVAL
                entry.revert_reason = ""
                entry.qr_expired = False
                entry.save()
            else:
                token = make_qr_token()
                entry = VisitorEntry(
                    visitor=visitor,
                    host=host,
                    location=location,
                    entry_source=VisitorEntry.ENTRY_MANUAL,
                    visitor_type=visitor_type,
                    status=VisitorEntry.STATUS_PENDING_APPROVAL,
                    purpose_of_visit=purpose,
                    vehicle_number=vehicle_number,
                    remarks=remarks,
                    expected_arrival_time=expected_arrival,
                    expected_out_time=expected_out,
                    visit_date=visit_date_today,
                    qr_token=token,
                    qr_expired=False,
                    created_by=request.user,
                )
                entry.qr_image.save(
                    f"visitor_qr_{token[:12]}.png",
                    generate_qr_image_file(token),
                    save=False,
                )
                entry.save()
        except ImportError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        _save_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO, request.FILES.get("visitor_photo"))
        _save_asset(entry, VisitorAsset.ASSET_ID_PROOF, request.FILES.get("id_proof"))
        _save_asset(entry, VisitorAsset.ASSET_VEHICLE_PHOTO, request.FILES.get("vehicle_photo"))
        additional = request.FILES.getlist("additional_images") or request.FILES.getlist(
            "additional_image"
        )
        _save_assets_many(entry, VisitorAsset.ASSET_ADDITIONAL, additional)

        entry = _base_entry_qs().get(id=entry.id)
        return Response(
            VisitorEntrySerializer(entry, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class VisitorApproveView(APIView):
    """
    POST /visitors/entries/<id>/approve/
    Host approval = check-in for manual entry. Body: expected_out_time (optional).
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @transaction.atomic
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().select_for_update().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_access_entry(request, entry):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        if not _is_host_or_super(request, entry):
            return Response(
                {"error": "Only the host can approve this entry"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if entry.status not in (
            VisitorEntry.STATUS_PENDING_APPROVAL,
            VisitorEntry.STATUS_REVERTED,
        ):
            return Response(
                {"error": f"Cannot approve entry with status={entry.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        expected_out = _parse_dt(request.data.get("expected_out_time"))
        now = timezone.now()
        entry.status = VisitorEntry.STATUS_CHECKED_IN
        entry.check_in_time = now
        entry.approved_by = request.user
        entry.approved_on = now
        entry.revert_reason = ""
        if expected_out:
            entry.expected_out_time = expected_out
        entry.save()

        entry = _base_entry_qs().get(id=entry.id)
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorRevertView(APIView):
    """POST /visitors/entries/<id>/revert/ — host asks guard to correct data."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @transaction.atomic
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().select_for_update().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if not _is_host_or_super(request, entry):
            return Response(
                {"error": "Only the host can revert this entry"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if entry.status != VisitorEntry.STATUS_PENDING_APPROVAL:
            return Response(
                {"error": f"Cannot revert entry with status={entry.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason = (request.data.get("revert_reason") or request.data.get("reason") or "").strip()
        entry.status = VisitorEntry.STATUS_REVERTED
        entry.revert_reason = reason
        entry.save(update_fields=["status", "revert_reason", "modified_on"])

        entry = _base_entry_qs().get(id=entry.id)
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorCancelView(APIView):
    """POST /visitors/entries/<id>/cancel/ — host/guard cancel; QR expires."""

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @transaction.atomic
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().select_for_update().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_access_entry(request, entry):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        if entry.status in (
            VisitorEntry.STATUS_CHECKED_OUT,
            VisitorEntry.STATUS_CANCELLED,
        ):
            return Response(
                {"error": f"Cannot cancel entry with status={entry.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entry.status = VisitorEntry.STATUS_CANCELLED
        entry.qr_expired = True
        entry.save(update_fields=["status", "qr_expired", "modified_on"])

        entry = _base_entry_qs().get(id=entry.id)
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorCheckOutView(APIView):
    """POST /visitors/entries/<id>/checkout/ — exit_photo REQUIRED."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().select_for_update().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_access_entry(request, entry):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        if entry.status != VisitorEntry.STATUS_CHECKED_IN:
            return Response(
                {"error": f"Entry is not checked in (status={entry.status})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        exit_photo = request.FILES.get("exit_photo") or request.FILES.get("checkout_image")
        if not exit_photo:
            return Response(
                {"error": "Checkout image (exit_photo) is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entry.status = VisitorEntry.STATUS_CHECKED_OUT
        entry.check_out_time = timezone.now()
        entry.qr_expired = True
        entry.save(update_fields=["status", "check_out_time", "qr_expired", "modified_on"])
        _save_asset(entry, VisitorAsset.ASSET_EXIT_PHOTO, exit_photo)

        entry = _base_entry_qs().get(id=entry.id)
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorQrScanView(APIView):
    """
    POST /visitors/qr-scan/  body: { qr_token }
    - pending_approval → not approved yet
    - scheduled (invite) → check-in
    - checked_in → return entry for checkout UI
    - checked_out / cancelled / expired → error
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]

    @transaction.atomic
    def post(self, request):
        token = (request.data.get("qr_token") or request.data.get("token") or "").strip()
        if not token:
            return Response({"error": "qr_token is required"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            entry = _base_entry_qs().select_for_update().get(qr_token=token)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Invalid QR"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_access_entry(request, entry):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        if entry.qr_expired or entry.status in (
            VisitorEntry.STATUS_CHECKED_OUT,
            VisitorEntry.STATUS_CANCELLED,
        ):
            return Response(
                {
                    "action": "expired",
                    "message": "QR has expired",
                    "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if entry.status == VisitorEntry.STATUS_PENDING_APPROVAL:
            return Response(
                {
                    "action": "awaiting_approval",
                    "message": "Not approved yet. Waiting for host approval.",
                    "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                }
            )

        if entry.status == VisitorEntry.STATUS_REVERTED:
            return Response(
                {
                    "action": "reverted",
                    "message": "Entry was reverted. Guard must correct and resubmit.",
                    "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                }
            )

        # Invitation: QR scan = check-in (no host approval)
        if entry.status == VisitorEntry.STATUS_SCHEDULED:
            now = timezone.now()
            entry.status = VisitorEntry.STATUS_CHECKED_IN
            entry.check_in_time = now
            entry.approved_by = request.user
            entry.approved_on = now
            entry.save()
            entry = _base_entry_qs().get(id=entry.id)
            return Response(
                {
                    "action": "checked_in",
                    "message": "Visitor checked in via invitation QR",
                    "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                }
            )

        if entry.status == VisitorEntry.STATUS_CHECKED_IN:
            return Response(
                {
                    "action": "checkout",
                    "message": "Visitor is checked in. Proceed to checkout with image.",
                    "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                }
            )

        return Response(
            {"error": f"Unhandled status={entry.status}"},
            status=status.HTTP_400_BAD_REQUEST,
        )


class VisitorEntryListView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, error_response = _filtered_entries(request)
        if error_response:
            return error_response
        return Response(
            VisitorEntrySerializer(qs, many=True, context={"request": request}).data
        )


class VisitorEntryExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, error_response = _filtered_entries(request)
        if error_response:
            return error_response
        return generate_visitor_excel(qs, request)
