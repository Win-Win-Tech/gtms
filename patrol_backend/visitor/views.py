from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
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


def _base_entry_qs():
    return (
        VisitorEntry.objects.filter(is_deleted=False)
        .select_related("visitor", "host", "location", "created_by")
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
    """Save multiple files of the same asset_type (e.g. additional images)."""
    created = []
    for uploaded in uploaded_files or []:
        if uploaded:
            created.append(_save_asset(entry, asset_type, uploaded))
    return created


def _filtered_entries(request):
    """Shared list/export filtering. Returns (queryset, error_response_or_None)."""
    location_id, err = resolve_location_for_request(
        request, request.query_params.get("location_id")
    )
    if err:
        return None, Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

    qs = _base_entry_qs()
    if location_id:
        qs = qs.filter(location_id=location_id)

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


class VisitorSearchView(APIView):
    """GET /visitors/search/?ic_number= — lookup visitor profile in current location."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        ic_number = (
            request.query_params.get("ic_number")
            or request.query_params.get("ic_passport_number")
            or ""
        ).strip()
        if not ic_number:
            return Response(
                {"error": "ic_number is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

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
    """POST /visitors/entries/checkin/ — walk-in manual check-in (multipart)."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request):
        data = request.data
        location_id, err = resolve_location_for_request(request, data.get("location_id"))
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            location = Location.objects.get(id=location_id, is_deleted=False)
        except Location.DoesNotExist:
            return Response({"error": "Location not found"}, status=status.HTTP_404_NOT_FOUND)

        ic_number = (data.get("ic_passport_number") or data.get("ic_number") or "").strip()
        visitor_name = (data.get("visitor_name") or "").strip()
        if not ic_number:
            return Response(
                {"error": "ic_passport_number is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not visitor_name:
            return Response(
                {"error": "visitor_name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        visitor_type = (data.get("visitor_type") or VisitorEntry.TYPE_GUEST).strip()
        valid_types = {c[0] for c in VisitorEntry.VISITOR_TYPE_CHOICES}
        if visitor_type not in valid_types:
            return Response(
                {"error": f"Invalid visitor_type. Choose from: {', '.join(sorted(valid_types))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        host = None
        host_id = data.get("host_id")
        if host_id:
            try:
                host = User.objects.get(id=host_id, is_deleted=False)
            except User.DoesNotExist:
                return Response({"error": "Host not found"}, status=status.HTTP_400_BAD_REQUEST)
            if not getattr(request.user, "is_superuser", False):
                if host.location_id and str(host.location_id) != str(location_id):
                    return Response(
                        {"error": "Host must belong to the same location"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )

        phone_number = (data.get("phone_number") or "").strip()
        purpose = (data.get("purpose_of_visit") or "").strip()
        vehicle_number = (data.get("vehicle_number") or "").strip()
        remarks = (data.get("remarks") or "").strip()

        visitor = (
            Visitor.objects.select_for_update()
            .filter(
                is_deleted=False,
                location_id=location_id,
                ic_passport_number__iexact=ic_number,
            )
            .first()
        )
        if visitor:
            visitor.visitor_name = visitor_name
            if phone_number:
                visitor.phone_number = phone_number
            visitor.save(
                update_fields=["visitor_name", "phone_number", "modified_on"]
            )
        else:
            visitor = Visitor.objects.create(
                location=location,
                ic_passport_number=ic_number,
                visitor_name=visitor_name,
                phone_number=phone_number,
            )

        open_invite = (
            VisitorEntry.objects.select_for_update()
            .filter(
                is_deleted=False,
                visitor=visitor,
                location_id=location_id,
                status=VisitorEntry.STATUS_OPEN,
            )
            .order_by("-created_on")
            .first()
        )

        now = timezone.now()
        try:
            if open_invite:
                entry = open_invite
                entry.status = VisitorEntry.STATUS_CHECKED_IN
                entry.check_in_time = now
                entry.visitor_type = visitor_type
                entry.purpose_of_visit = purpose or entry.purpose_of_visit
                entry.vehicle_number = vehicle_number or entry.vehicle_number
                entry.remarks = remarks or entry.remarks
                if host:
                    entry.host = host
                if not entry.qr_token:
                    entry.qr_token = make_qr_token()
                if not entry.qr_image:
                    entry.qr_image.save(
                        f"visitor_qr_{entry.qr_token[:12]}.png",
                        generate_qr_image_file(entry.qr_token),
                        save=False,
                    )
                entry.save()
            else:
                token = make_qr_token()
                entry = VisitorEntry(
                    visitor=visitor,
                    host=host,
                    location=location,
                    visitor_type=visitor_type,
                    status=VisitorEntry.STATUS_CHECKED_IN,
                    purpose_of_visit=purpose,
                    vehicle_number=vehicle_number,
                    remarks=remarks,
                    check_in_time=now,
                    qr_token=token,
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

        # Single images
        _save_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO, request.FILES.get("visitor_photo"))
        _save_asset(entry, VisitorAsset.ASSET_ID_PROOF, request.FILES.get("id_proof"))
        _save_asset(entry, VisitorAsset.ASSET_VEHICLE_PHOTO, request.FILES.get("vehicle_photo"))
        # Multiple additional images (repeat field name in multipart)
        additional = request.FILES.getlist("additional_images") or request.FILES.getlist(
            "additional_image"
        )
        _save_assets_many(entry, VisitorAsset.ASSET_ADDITIONAL, additional)

        entry = _base_entry_qs().get(id=entry.id)
        return Response(
            VisitorEntrySerializer(entry, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class VisitorCheckOutView(APIView):
    """POST /visitors/entries/<id>/checkout/ — optional exit_photo."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    @transaction.atomic
    def post(self, request, entry_id):
        location_id, err = resolve_location_for_request(
            request, request.data.get("location_id") or request.query_params.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)

        try:
            entry = _base_entry_qs().select_for_update().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if location_id and str(entry.location_id) != str(location_id):
            return Response(
                {"error": "You can only checkout visitors at your location"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not getattr(request.user, "is_superuser", False):
            user_loc = getattr(request.user, "location_id", None)
            if user_loc and str(entry.location_id) != str(user_loc):
                return Response(
                    {"error": "You can only checkout visitors at your location"},
                    status=status.HTTP_403_FORBIDDEN,
                )

        if entry.status != VisitorEntry.STATUS_CHECKED_IN:
            return Response(
                {"error": f"Entry is not checked in (status={entry.status})"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entry.status = VisitorEntry.STATUS_CHECKED_OUT
        entry.check_out_time = timezone.now()
        entry.save(update_fields=["status", "check_out_time", "modified_on"])

        _save_asset(entry, VisitorAsset.ASSET_EXIT_PHOTO, request.FILES.get("exit_photo"))

        entry = _base_entry_qs().get(id=entry.id)
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorEntryListView(APIView):
    """GET /visitors/entries/ — history with filters."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, error_response = _filtered_entries(request)
        if error_response:
            return error_response
        serializer = VisitorEntrySerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)


class VisitorEntryExportView(APIView):
    """GET /visitors/entries/export/ — Excel export (same filters as list)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, error_response = _filtered_entries(request)
        if error_response:
            return error_response
        return generate_visitor_excel(qs, request)
