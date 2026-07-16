from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from scheduler.models import Location, Shift

from .models import RollCallSession
from .serializers import RollCallSessionSerializer
from .utils import (
    apply_shift_date_filter,
    get_shift_date_for_now,
    resolve_location_for_request,
)


def _base_qs():
    return RollCallSession.objects.filter(is_deleted=False).select_related(
        "location",
        "shift",
        "started_by",
        "ended_by",
    )


def _parse_bool_param(value):
    if value is None:
        return False
    return str(value).strip().lower() in ("1", "true", "yes")


def _list_rollcall_sessions(request):
    """
    Shared queryset builder for GET /rollcall/sessions/ and /rollcall/dashboard/filter/.
    Returns (queryset, error_message, http_status) — queryset is None on error.
    """
    location_id_param = request.query_params.get("location_id") or request.query_params.get(
        "location"
    )
    shift_id = request.query_params.get("shift_id") or request.query_params.get("shift")
    status_filter = (request.query_params.get("status") or "all").strip().lower()
    date_filter = request.query_params.get("date_filter", "today")
    start_date = request.query_params.get("start_date")
    end_date = request.query_params.get("end_date")
    user_id = request.query_params.get("user_id") or request.query_params.get("participant_id")
    filter_open_by_date = _parse_bool_param(
        request.query_params.get("filter_open_by_date")
    )

    if not getattr(request.user, "is_superuser", False):
        location_id, err = resolve_location_for_request(request, location_id_param)
        if err:
            return None, err, status.HTTP_400_BAD_REQUEST
        if not location_id:
            return None, "location_id is required", status.HTTP_400_BAD_REQUEST
        location_id_param = location_id

    if shift_id and location_id_param and location_id_param != "All":
        shift = Shift.objects.filter(
            id=shift_id, location_id=location_id_param, is_deleted=False
        ).first()
        if not shift:
            return None, "Shift not found for this location", status.HTTP_404_NOT_FOUND

    qs = _base_qs()

    if location_id_param and location_id_param != "All":
        qs = qs.filter(location_id=location_id_param)

    if shift_id:
        qs = qs.filter(shift_id=shift_id)

    if status_filter in (RollCallSession.STATUS_OPEN, RollCallSession.STATUS_CLOSED):
        qs = qs.filter(status=status_filter)

    if user_id:
        qs = qs.filter(Q(started_by_id=user_id) | Q(ended_by_id=user_id))

    apply_date = status_filter != RollCallSession.STATUS_OPEN or filter_open_by_date
    if apply_date:
        try:
            qs = apply_shift_date_filter(
                qs,
                request,
                location_id_param if location_id_param and location_id_param != "All" else None,
                date_filter,
                start_date,
                end_date,
            )
        except ValueError as exc:
            return None, str(exc), status.HTTP_400_BAD_REQUEST

    return qs, None, None


class RollCallSessionListView(APIView):
    """
    GET /rollcall/sessions/
    Unified list for mobile and admin.

    Query params:
      status: open | closed | all (default all)
      shift_id, location_id
      date_filter: today | week | month | custom (+ start_date, end_date)
      user_id: filter sessions where user started OR ended the session
      filter_open_by_date: when status=open, apply date_filter (admin open tab)
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        qs, err, err_status = _list_rollcall_sessions(request)
        if err:
            return Response({"error": err}, status=err_status)
        serializer = RollCallSessionSerializer(qs, many=True, context={"request": request})
        return Response(serializer.data)


class RollCallStartSessionView(APIView):
    """POST /rollcall/sessions/start/ — multipart: shift_id, start_photo."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        shift_id = request.data.get("shift_id")
        start_photo = request.FILES.get("start_photo") or request.FILES.get("image")
        if not shift_id:
            return Response(
                {"error": "shift_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not start_photo:
            return Response(
                {"error": "start_photo is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        location_id, err = resolve_location_for_request(
            request, request.data.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        location = Location.objects.filter(id=location_id, is_deleted=False).first()
        if not location:
            return Response(
                {"error": "Location not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        shift = Shift.objects.filter(
            id=shift_id, location_id=location_id, is_deleted=False
        ).first()
        if not shift:
            return Response(
                {"error": "Shift not found for this location"},
                status=status.HTTP_404_NOT_FOUND,
            )

        shift_date, _ = get_shift_date_for_now(request, location_id, shift)
        session = RollCallSession(
            location=location,
            shift=shift,
            shift_date=shift_date,
            status=RollCallSession.STATUS_OPEN,
            started_by=request.user,
            started_at=timezone.now(),
        )
        session.start_photo = start_photo
        session.save()

        serializer = RollCallSessionSerializer(session, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RollCallEndSessionView(APIView):
    """POST /rollcall/sessions/<id>/end/ — multipart: end_photo."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, session_id):
        end_photo = request.FILES.get("end_photo") or request.FILES.get("image")
        if not end_photo:
            return Response(
                {"error": "end_photo is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        location_id, err = resolve_location_for_request(
            request, request.data.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session = _base_qs().filter(id=session_id).first()
        if not session:
            return Response(
                {"error": "Session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if str(session.location_id) != str(location_id):
            return Response(
                {"error": "Session does not belong to your location"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if session.status != RollCallSession.STATUS_OPEN:
            return Response(
                {"error": "Session is already closed"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        session.end_photo = end_photo
        session.ended_at = timezone.now()
        session.ended_by = request.user
        session.status = RollCallSession.STATUS_CLOSED
        session.save()

        serializer = RollCallSessionSerializer(session, context={"request": request})
        return Response(serializer.data)


class RollCallDashboardFilterView(RollCallSessionListView):
    """Backward-compatible alias for GET /rollcall/dashboard/filter/."""

    pass
