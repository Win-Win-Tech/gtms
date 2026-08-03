from datetime import datetime

from django.db import transaction
from django.db.models import Q
from django.http import FileResponse, HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.timezone import is_naive
from rest_framework import status
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

import pytz

from authapp.models import User
from notifications.models import NotificationLog
from notifications.services import notify_visitor_host_action, notify_visitor_pending
from scheduler.models import Location

from .exports import generate_visitor_excel, generate_visitor_pdf
from .models import Visitor, VisitorAsset, VisitorEntry
from .serializers import VisitorAssetSerializer, VisitorEntrySerializer, VisitorSerializer
from .utils import (
    apply_checkin_date_filter,
    make_qr_token,
    refresh_entry_qr_and_pass,
    resolve_location_for_request,
)
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    to_user_timezone,
)


def _base_entry_qs():
    return (
        VisitorEntry.objects.filter(is_deleted=False)
        .select_related(
            "visitor", "host", "location", "created_by", "approved_by", "scanned_by"
        )
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


def _latest_prior_asset(visitor, asset_type, exclude_entry_id=None):
    """Most recent asset of this type from earlier visits of the same visitor."""
    qs = (
        VisitorAsset.objects.filter(
            visitor_entry__visitor_id=visitor.id,
            visitor_entry__is_deleted=False,
            asset_type=asset_type,
        )
        .exclude(file="")
        .exclude(file__isnull=True)
        .order_by("-created_on")
    )
    if exclude_entry_id:
        qs = qs.exclude(visitor_entry_id=exclude_entry_id)
    return qs.first()


def _clone_asset_onto_entry(entry, source):
    if not source or not source.file:
        return None
    return VisitorAsset.objects.create(
        visitor_entry=entry,
        asset_type=source.asset_type,
        file=source.file.name,
    )


def _auto_fill_missing_assets_from_history(entry, types):
    """
    For each asset type not already on this entry, clone the latest prior
    visit's file path (new row, same storage object — no re-upload).
    """
    filled = []
    for asset_type in types:
        if entry.assets.filter(asset_type=asset_type).exists():
            continue
        src = _latest_prior_asset(
            entry.visitor, asset_type, exclude_entry_id=entry.id
        )
        row = _clone_asset_onto_entry(entry, src)
        if row:
            filled.append(asset_type)
    return filled


def _entry_has_asset(entry, asset_type):
    return entry.assets.filter(asset_type=asset_type).exists()


def _truthy_form_flag(data, key):
    raw = data.get(key)
    if raw is None:
        return False
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def _clone_all_prior_additional(entry):
    """Attach every additional image from the latest prior visit that had any."""
    if entry.assets.filter(asset_type=VisitorAsset.ASSET_ADDITIONAL).exists():
        return
    prior_entry = (
        VisitorEntry.objects.filter(is_deleted=False, visitor_id=entry.visitor_id)
        .exclude(id=entry.id)
        .filter(assets__asset_type=VisitorAsset.ASSET_ADDITIONAL)
        .order_by("-created_on")
        .distinct()
        .first()
    )
    if not prior_entry:
        return
    for src in prior_entry.assets.filter(asset_type=VisitorAsset.ASSET_ADDITIONAL):
        _clone_asset_onto_entry(entry, src)


def _parse_dt(value, user_tz=None):
    """
    Parse client datetime.
    Naive values (e.g. 2026-07-22T19:00 from the web form) are wall-clock in user_tz,
    then stored as UTC. Aware values are converted to UTC.
    """
    if not value:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        text = str(value).strip().replace("Z", "+00:00")
        dt = parse_datetime(text)
        if not dt:
            try:
                dt = datetime.strptime(text[:16], "%Y-%m-%dT%H:%M")
            except ValueError:
                return None

    if is_naive(dt):
        tz = user_tz or pytz.UTC
        # pytz: prefer localize for zone transitions
        if hasattr(tz, "localize"):
            dt = tz.localize(dt)
        else:
            dt = dt.replace(tzinfo=tz)
    return dt.astimezone(pytz.UTC)


def _parse_date(value):
    if not value:
        return None
    if hasattr(value, "year") and not isinstance(value, datetime):
        return value
    text = str(value).strip()[:10]
    try:
        return datetime.strptime(text, "%Y-%m-%d").date()
    except ValueError:
        return None


def _local_date_for_dt(dt, user_tz):
    if not dt:
        return None
    local = to_user_timezone(dt, user_tz)
    return local.date() if local else None


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

    visitor_type = (
        request.query_params.get("visitor_type")
        or request.query_params.get("visitorType")
        or ""
    ).strip().lower()
    if visitor_type and visitor_type != "all":
        valid_types = {c[0] for c in VisitorEntry.VISITOR_TYPE_CHOICES}
        if visitor_type not in valid_types:
            return None, Response(
                {
                    "error": (
                        "Invalid visitor_type. Choose from: "
                        f"{', '.join(sorted(valid_types))}"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        qs = qs.filter(visitor_type=visitor_type)

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

    # Vehicle filter: only entries with a non-empty vehicle_number
    has_vehicle = (request.query_params.get("has_vehicle") or "").strip().lower()
    if has_vehicle in ("1", "true", "yes"):
        qs = qs.exclude(vehicle_number__isnull=True).exclude(vehicle_number__exact="")

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
            return Response({"found": False, "visitor": None, "last_entry": None})

        last_entry = (
            VisitorEntry.objects.filter(is_deleted=False, visitor=visitor)
            .select_related("host")
            .prefetch_related("assets")
            .order_by("-created_on")
            .first()
        )
        last_entry_data = None
        if last_entry:
            # Prefill helpers — include prior photos; times still captured fresh.
            pref_types = {
                VisitorAsset.ASSET_VISITOR_PHOTO,
                VisitorAsset.ASSET_ID_PROOF,
                VisitorAsset.ASSET_ADDITIONAL,
            }
            photo_assets = [
                a
                for a in last_entry.assets.all()
                if a.asset_type in pref_types and a.file
            ]
            last_entry_data = {
                "id": str(last_entry.id),
                "visitor_type": last_entry.visitor_type,
                "purpose_of_visit": last_entry.purpose_of_visit or "",
                "vehicle_number": last_entry.vehicle_number or "",
                "remarks": last_entry.remarks or "",
                "host_id": str(last_entry.host_id) if last_entry.host_id else None,
                "host_name": getattr(last_entry.host, "name", None) if last_entry.host else None,
                "host_employee_code": (
                    getattr(last_entry.host, "employee_code", None) if last_entry.host else None
                ),
                "assets": VisitorAssetSerializer(
                    photo_assets, many=True, context={"request": request}
                ).data,
            }

        return Response(
            {
                "found": True,
                "visitor": VisitorSerializer(visitor, context={"request": request}).data,
                "last_entry": last_entry_data,
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

        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        visit_date_today = get_user_today(user_tz)
        # Walk-in: visit_date + expected_arrival = create time (now). Ignore client arrival.
        expected_arrival = timezone.now()
        expected_out = _parse_dt(data.get("expected_out_time"), user_tz)

        if expected_out and expected_out <= expected_arrival:
            return Response(
                {"error": "expected_out_time must be after expected_arrival_time"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if expected_out:
            out_day = _local_date_for_dt(expected_out, user_tz)
            # Multi-day stay: out can be later days, but not before visit_date (today)
            if out_day and out_day < visit_date_today:
                return Response(
                    {
                        "error": (
                            "expected_out_time cannot be before visit_date "
                            f"(out={out_day}, visit_date={visit_date_today})"
                        )
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

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
                # Info on the ID card changed — regenerate QR + pass, delete old files
                refresh_entry_qr_and_pass(entry, regenerate_token=False, save=True)
            else:
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
                    qr_token=make_qr_token(),
                    qr_expired=False,
                    created_by=request.user,
                )
                entry.save()
                refresh_entry_qr_and_pass(entry, regenerate_token=False, save=True)
        except ImportError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        _save_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO, request.FILES.get("visitor_photo"))
        _save_asset(entry, VisitorAsset.ASSET_ID_PROOF, request.FILES.get("id_proof"))
        additional = request.FILES.getlist("additional_images") or request.FILES.getlist(
            "additional_image"
        )
        _save_assets_many(entry, VisitorAsset.ASSET_ADDITIONAL, additional)

        # Client only uploads what changed. Missing visitor_photo / id_proof are
        # filled from the visitor's last entry (same file path, new asset rows).
        # Vehicle / extra shots use additional_images only (no vehicle_photo field).
        _auto_fill_missing_assets_from_history(
            entry,
            (
                VisitorAsset.ASSET_VISITOR_PHOTO,
                VisitorAsset.ASSET_ID_PROOF,
            ),
        )

        # Additional: if none uploaded and not explicitly cleared, copy prior visit's.
        if not additional and not _truthy_form_flag(data, "clear_additional"):
            _clone_all_prior_additional(entry)

        if not _entry_has_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO):
            transaction.set_rollback(True)
            return Response(
                {
                    "error": (
                        "visitor_photo is required "
                        "(upload a file, or use a returning visitor who has a prior photo)"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _entry_has_asset(entry, VisitorAsset.ASSET_ID_PROOF):
            transaction.set_rollback(True)
            return Response(
                {
                    "error": (
                        "id_proof is required "
                        "(upload IC/ID copy, or use a returning visitor who has a prior ID image)"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        entry = _base_entry_qs().get(id=entry.id)
        returning_visitor = (
            VisitorEntry.objects.filter(is_deleted=False, visitor=visitor)
            .exclude(id=entry.id)
            .exists()
        )
        notify_visitor_pending(entry)
        payload = VisitorEntrySerializer(entry, context={"request": request}).data
        payload["returning_visitor"] = returning_visitor
        return Response(
            payload,
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
        if entry.status != VisitorEntry.STATUS_PENDING_APPROVAL:
            return Response(
                {"error": f"Cannot approve entry with status={entry.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        _auto_fill_missing_assets_from_history(
            entry,
            (
                VisitorAsset.ASSET_VISITOR_PHOTO,
                VisitorAsset.ASSET_ID_PROOF,
            ),
        )
        if not _entry_has_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO) or not _entry_has_asset(
            entry, VisitorAsset.ASSET_ID_PROOF
        ):
            return Response(
                {
                    "error": (
                        "Cannot approve entry. Mandatory visitor photo or ID proof is missing."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_tz = get_user_timezone_from_request(request, location_id=entry.location_id)
        expected_out = _parse_dt(request.data.get("expected_out_time"), user_tz)
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
        visitor_name = getattr(entry.visitor, "visitor_name", "Visitor")
        notify_visitor_host_action(
            entry,
            NotificationLog.TYPE_VISITOR_APPROVED,
            "Visitor approved",
            f"{visitor_name} was approved and checked in.",
        )
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
        visitor_name = getattr(entry.visitor, "visitor_name", "Visitor")
        notify_visitor_host_action(
            entry,
            NotificationLog.TYPE_VISITOR_REVERTED,
            "Visitor entry reverted",
            f"{visitor_name} needs correction."
            + (f" Reason: {reason}" if reason else ""),
            extra={"revert_reason": reason},
        )
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorCancelView(APIView):
    """POST /visitors/entries/<id>/cancel/ — Reject/Cancel; QR expires."""

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
        visitor_name = getattr(entry.visitor, "visitor_name", "Visitor")
        notify_visitor_host_action(
            entry,
            NotificationLog.TYPE_VISITOR_CANCELLED,
            "Visitor entry cancelled",
            f"{visitor_name} was cancelled / rejected.",
        )
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)


class VisitorPassDownloadView(APIView):
    """
    GET /visitors/entries/<id>/pass/
    Authenticated download of the visitor ID-card pass (falls back to raw QR).
    Avoids browser CORS issues with direct /media/ fetch.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        try:
            entry = _base_entry_qs().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_access_entry(request, entry):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        if entry.qr_expired:
            return Response({"error": "QR is expired"}, status=status.HTTP_400_BAD_REQUEST)

        # Always rebuild pass image so layout/theme updates apply (same QR token).
        try:
            refresh_entry_qr_and_pass(entry, regenerate_token=False, save=True)
            entry = _base_entry_qs().get(id=entry.id)
        except Exception as exc:
            if not (entry.pass_image or entry.qr_image):
                return Response(
                    {"error": f"Pass image not available: {exc}"},
                    status=status.HTTP_500_INTERNAL_SERVER_ERROR,
                )

        image_field = entry.pass_image or entry.qr_image
        if not image_field:
            return Response({"error": "Pass image not available"}, status=status.HTTP_404_NOT_FOUND)

        visitor_name = getattr(entry.visitor, "visitor_name", None) or "visitor"
        safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in visitor_name)[:40]
        filename = f"visitor-pass-{safe or entry.id}.png"
        return FileResponse(
            image_field.open("rb"),
            as_attachment=True,
            filename=filename,
            content_type="image/png",
        )


class VisitorRescheduleView(APIView):
    """
    POST /visitors/entries/<id>/reschedule/
    Body: expected_arrival_time, expected_out_time (required), visit_date (optional).
    Same day → pending_approval; future day → scheduled. Never checks in.
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
                {"error": "Only the host can reschedule this entry"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if entry.status not in (
            VisitorEntry.STATUS_PENDING_APPROVAL,
            VisitorEntry.STATUS_SCHEDULED,
        ):
            return Response(
                {"error": f"Cannot reschedule entry with status={entry.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_tz = get_user_timezone_from_request(request, location_id=entry.location_id)
        expected_arrival = _parse_dt(request.data.get("expected_arrival_time"), user_tz)
        expected_out = _parse_dt(request.data.get("expected_out_time"), user_tz)
        if not expected_arrival:
            return Response(
                {"error": "expected_arrival_time is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not expected_out:
            return Response(
                {"error": "expected_out_time is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if expected_out <= expected_arrival:
            return Response(
                {"error": "expected_out_time must be after expected_arrival_time"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        today = get_user_today(user_tz)
        visit_date = _parse_date(request.data.get("visit_date"))
        if not visit_date:
            visit_date = _local_date_for_dt(expected_arrival, user_tz) or today

        if visit_date < today:
            return Response(
                {"error": "visit_date cannot be in the past"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        arrival_day = _local_date_for_dt(expected_arrival, user_tz)
        out_day = _local_date_for_dt(expected_out, user_tz)
        # Visit may span multiple days (arrive today, leave later). ETA/ETO must be on/after visit_date.
        if arrival_day and arrival_day < visit_date:
            return Response(
                {
                    "error": (
                        "expected_arrival_time cannot be before visit_date "
                        f"(arrival={arrival_day}, visit_date={visit_date})"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if out_day and out_day < visit_date:
            return Response(
                {
                    "error": (
                        "expected_out_time cannot be before visit_date "
                        f"(out={out_day}, visit_date={visit_date})"
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if visit_date == today:
            new_status = VisitorEntry.STATUS_PENDING_APPROVAL
        else:
            new_status = VisitorEntry.STATUS_SCHEDULED

        entry.expected_arrival_time = expected_arrival
        entry.expected_out_time = expected_out
        entry.visit_date = visit_date
        entry.status = new_status
        entry.qr_expired = False
        entry.save(
            update_fields=[
                "expected_arrival_time",
                "expected_out_time",
                "visit_date",
                "status",
                "qr_expired",
                "modified_on",
            ]
        )
        # Visit date / times on the ID card changed — regenerate pass (keep same token)
        try:
            refresh_entry_qr_and_pass(entry, regenerate_token=False, save=True)
        except ImportError as exc:
            return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        entry = _base_entry_qs().get(id=entry.id)
        visitor_name = getattr(entry.visitor, "visitor_name", "Visitor")
        notify_visitor_host_action(
            entry,
            NotificationLog.TYPE_VISITOR_RESCHEDULED,
            "Visitor rescheduled",
            f"{visitor_name} was rescheduled"
            + (f" to {visit_date.isoformat()}." if visit_date else "."),
            extra={
                "visit_date": visit_date.isoformat() if visit_date else "",
                "status": new_status,
            },
        )
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
    - pending_approval → awaiting_approval
    - scheduled on visit day → pending_approval + awaiting_approval
    - scheduled future day → too_early
    - checked_in → checkout
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

        # Scheduled (reschedule to future day): visit-day scan starts host approval — no auto check-in
        if entry.status == VisitorEntry.STATUS_SCHEDULED:
            user_tz = get_user_timezone_from_request(request, location_id=entry.location_id)
            today = get_user_today(user_tz)
            visit_day = entry.visit_date or _local_date_for_dt(entry.expected_arrival_time, user_tz)

            if visit_day and visit_day > today:
                return Response(
                    {
                        "action": "too_early",
                        "message": f"Visit is scheduled for {visit_day.isoformat()}. Too early to check in.",
                        "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )

            _auto_fill_missing_assets_from_history(
                entry,
                (
                    VisitorAsset.ASSET_VISITOR_PHOTO,
                    VisitorAsset.ASSET_ID_PROOF,
                ),
            )
            has_photo = _entry_has_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO)
            has_id = _entry_has_asset(entry, VisitorAsset.ASSET_ID_PROOF)

            if not (has_photo and has_id):
                return Response(
                    {
                        "action": "missing_document",
                        "type": "missing_document",
                        "message": "Visitor photo or ID proof is missing. Redirect to capture form.",
                        "entry": VisitorEntrySerializer(entry, context={"request": request}).data,
                    }
                )

            entry.status = VisitorEntry.STATUS_PENDING_APPROVAL
            entry.qr_expired = False
            entry.scanned_by = request.user
            entry.save(update_fields=["status", "qr_expired", "scanned_by", "modified_on"])
            entry = _base_entry_qs().get(id=entry.id)
            notify_visitor_pending(entry)
            return Response(
                {
                    "action": "awaiting_approval",
                    "message": "Arrival recorded. Waiting for host approval.",
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


class VisitorEntryDetailView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, entry_id):
        try:
            entry = _base_entry_qs().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Entry not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(
            VisitorEntrySerializer(entry, context={"request": request}).data
        )


class VisitorEntryExportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, error_response = _filtered_entries(request)
        if error_response:
            return error_response
        return generate_visitor_excel(qs, request)


class VisitorEntryExportPdfView(APIView):
    """GET /visitors/entries/export-pdf/ — PDF download (same filters as list/Excel)."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, error_response = _filtered_entries(request)
        if error_response:
            return error_response
        try:
            content, filename = generate_visitor_pdf(qs, request)
        except ImportError as exc:
            return Response(
                {"error": str(exc)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class VisitorInviteCreateView(APIView):
    """
    POST /visitors/entries/invite/
    Pre-registration (Invite) creation:
    Mandatory: ic_passport_number, visitor_name, host_id, location_id, visit_date.
    Optional: visitor_type, purpose_of_visit, vehicle_number, remarks, expected_arrival_time, expected_out_time, phone_number.
    Assets (visitor_photo, id_proof, additional_images) are NOT mandatory.
    Creates entry with status=scheduled, entry_source=invitation, generates QR + pass.
    No host notification sent on creation.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

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

        host, host_err = _resolve_host(request, data.get("host_id"), location_id)
        if host_err:
            return Response({"error": host_err}, status=status.HTTP_400_BAD_REQUEST)

        visit_date_raw = data.get("visit_date")
        visit_date = _parse_date(visit_date_raw)
        if not visit_date:
            return Response(
                {"error": "visit_date is required for invitation"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        today = get_user_today(user_tz)
        if visit_date < today:
            return Response(
                {"error": "visit_date cannot be in the past"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        visitor_type = (data.get("visitor_type") or VisitorEntry.TYPE_GUEST).strip()
        valid_types = {c[0] for c in VisitorEntry.VISITOR_TYPE_CHOICES}
        if visitor_type not in valid_types:
            return Response(
                {"error": f"Invalid visitor_type. Choose from: {', '.join(sorted(valid_types))}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        phone_number = (data.get("phone_number") or "").strip()
        purpose = (data.get("purpose_of_visit") or "").strip()
        vehicle_number = (data.get("vehicle_number") or "").strip()
        remarks = (data.get("remarks") or "").strip()

        expected_arrival = _parse_dt(data.get("expected_arrival_time"), user_tz)
        expected_out = _parse_dt(data.get("expected_out_time"), user_tz)

        if expected_arrival and expected_out and expected_out <= expected_arrival:
            return Response(
                {"error": "expected_out_time must be after expected_arrival_time"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        visitor = _upsert_visitor(location, ic_number, visitor_name, phone_number)

        entry = VisitorEntry(
            visitor=visitor,
            host=host,
            location=location,
            entry_source=VisitorEntry.ENTRY_INVITATION,
            visitor_type=visitor_type,
            status=VisitorEntry.STATUS_SCHEDULED,
            purpose_of_visit=purpose,
            vehicle_number=vehicle_number,
            remarks=remarks,
            expected_arrival_time=expected_arrival,
            expected_out_time=expected_out,
            visit_date=visit_date,
            qr_token=make_qr_token(),
            qr_expired=False,
            created_by=request.user,
        )
        entry.save()
        refresh_entry_qr_and_pass(entry, regenerate_token=False, save=True)

        _save_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO, request.FILES.get("visitor_photo"))
        _save_asset(entry, VisitorAsset.ASSET_ID_PROOF, request.FILES.get("id_proof"))
        additional = request.FILES.getlist("additional_images") or request.FILES.getlist(
            "additional_image"
        )
        _save_assets_many(entry, VisitorAsset.ASSET_ADDITIONAL, additional)

        entry = _base_entry_qs().get(id=entry.id)
        return Response(
            VisitorEntrySerializer(entry, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class VisitorCompleteInviteView(APIView):
    """
    POST /visitors/entries/<id>/complete-invite/
    Guard/User captures missing photos or assets for an invited visitor entry.
    Field Edit Protection: Users other than host or entry creator CANNOT modify fields filled during invite creation.
    Saves assets, validates mandatory assets (visitor_photo + id_proof), transitions status to pending_approval, and notifies host.
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    @transaction.atomic
    def post(self, request, entry_id):
        try:
            entry = _base_entry_qs().select_for_update().get(id=entry_id)
        except VisitorEntry.DoesNotExist:
            return Response({"error": "Visitor entry not found"}, status=status.HTTP_404_NOT_FOUND)

        if not _can_access_entry(request, entry):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        if entry.status not in (
            VisitorEntry.STATUS_SCHEDULED,
            VisitorEntry.STATUS_PENDING_APPROVAL,
            VisitorEntry.STATUS_REVERTED,
        ):
            return Response(
                {"error": f"Cannot complete invite for entry with status={entry.status}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        data = request.data
        is_creator_or_host = _is_host_or_super(request, entry) or (
            entry.created_by_id and str(entry.created_by_id) == str(request.user.id)
        )

        if is_creator_or_host:
            ic_number = (data.get("ic_passport_number") or data.get("ic_number") or "").strip()
            visitor_name = (data.get("visitor_name") or "").strip()
            phone_number = (data.get("phone_number") or "").strip()
            if ic_number and visitor_name:
                visitor = _upsert_visitor(entry.location, ic_number, visitor_name, phone_number)
                entry.visitor = visitor

            if data.get("host_id"):
                host, host_err = _resolve_host(request, data.get("host_id"), entry.location_id)
                if not host_err and host:
                    entry.host = host

            if data.get("purpose_of_visit") is not None:
                entry.purpose_of_visit = data.get("purpose_of_visit").strip()
            if data.get("visitor_type"):
                vtype = data.get("visitor_type").strip()
                if vtype in {c[0] for c in VisitorEntry.VISITOR_TYPE_CHOICES}:
                    entry.visitor_type = vtype
            if data.get("remarks") is not None:
                entry.remarks = data.get("remarks").strip()
        else:
            phone_number = (data.get("phone_number") or "").strip()
            if phone_number and not entry.visitor.phone_number:
                entry.visitor.phone_number = phone_number
                entry.visitor.save(update_fields=["phone_number", "modified_on"])

        vehicle_num = (data.get("vehicle_number") or "").strip()
        if vehicle_num:
            entry.vehicle_number = vehicle_num

        _save_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO, request.FILES.get("visitor_photo"))
        _save_asset(entry, VisitorAsset.ASSET_ID_PROOF, request.FILES.get("id_proof"))
        additional = request.FILES.getlist("additional_images") or request.FILES.getlist(
            "additional_image"
        )
        _save_assets_many(entry, VisitorAsset.ASSET_ADDITIONAL, additional)

        _auto_fill_missing_assets_from_history(
            entry,
            (
                VisitorAsset.ASSET_VISITOR_PHOTO,
                VisitorAsset.ASSET_ID_PROOF,
            ),
        )

        if not _entry_has_asset(entry, VisitorAsset.ASSET_VISITOR_PHOTO):
            transaction.set_rollback(True)
            return Response(
                {"error": "visitor_photo is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _entry_has_asset(entry, VisitorAsset.ASSET_ID_PROOF):
            transaction.set_rollback(True)
            return Response(
                {"error": "id_proof is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        entry.status = VisitorEntry.STATUS_PENDING_APPROVAL
        entry.scanned_by = request.user
        entry.qr_expired = False
        entry.save()

        try:
            refresh_entry_qr_and_pass(entry, regenerate_token=False, save=True)
        except ImportError:
            pass

        entry = _base_entry_qs().get(id=entry.id)
        notify_visitor_pending(entry)
        return Response(VisitorEntrySerializer(entry, context={"request": request}).data)

