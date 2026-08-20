from django.http import HttpResponse
from django.utils import timezone
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.site_access import assert_caller_can_access_site, get_site_or_error
from scheduler.models import Shift

from .exports import generate_rollcall_excel, generate_rollcall_pdf
from .models import RollCallSession
from .serializers_v5 import RollCallSessionSerializerV5
from .site_filter import apply_rollcall_site_filter
from .utils import get_shift_date_for_now
from .views import _list_rollcall_sessions


def _v5_error(exc):
    if isinstance(exc, ValidationError):
        detail = exc.detail
        if isinstance(detail, dict):
            return Response(detail, status=status.HTTP_400_BAD_REQUEST)
        return Response({"error": detail}, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, PermissionDenied):
        return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    raise exc


def _base_qs_v5():
    return RollCallSession.objects.filter(is_deleted=False).select_related(
        "location",
        "site",
        "shift",
        "started_by",
        "ended_by",
    )


def _require_site(request):
    site_id = request.data.get("site_id") or request.query_params.get("site_id")
    if not site_id:
        raise ValidationError({"site_id": ["This field is required."]})
    site = get_site_or_error(site_id)
    assert_caller_can_access_site(request.user, site)
    return site


class RollCallSessionListViewV5(APIView):
    """GET /rollcall/v5/sessions/ — live filters plus site_id."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            qs, err, err_status = _list_rollcall_sessions(request)
            if err:
                return Response({"error": err}, status=err_status)
            qs = apply_rollcall_site_filter(qs.select_related("site"), request)
            serializer = RollCallSessionSerializerV5(qs, many=True, context={"request": request})
            return Response(serializer.data)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class RollCallDashboardFilterViewV5(RollCallSessionListViewV5):
    """GET /rollcall/v5/dashboard/filter/ — alias of list v5."""

    pass


class RollCallStartSessionViewV5(APIView):
    """POST /rollcall/v5/sessions/start/ — multipart: shift_id, start_photo, site_id."""

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
        try:
            site = _require_site(request)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        location = site.location
        if not location or getattr(location, "is_deleted", False):
            return Response(
                {"error": "Location not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        shift = Shift.objects.filter(
            id=shift_id, location_id=location.id, is_deleted=False
        ).first()
        if not shift:
            return Response(
                {"error": "Shift not found for this site's organisation"},
                status=status.HTTP_404_NOT_FOUND,
            )

        shift_date, _ = get_shift_date_for_now(request, str(location.id), shift)
        session = RollCallSession(
            location=location,
            site=site,
            shift=shift,
            shift_date=shift_date,
            status=RollCallSession.STATUS_OPEN,
            started_by=request.user,
            started_at=timezone.now(),
        )
        session.start_photo = start_photo
        session.save()

        serializer = RollCallSessionSerializerV5(session, context={"request": request})
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class RollCallEndSessionViewV5(APIView):
    """POST /rollcall/v5/sessions/<id>/end/ — multipart: end_photo, site_id."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request, session_id):
        end_photo = request.FILES.get("end_photo") or request.FILES.get("image")
        if not end_photo:
            return Response(
                {"error": "end_photo is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            site = _require_site(request)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        session = _base_qs_v5().filter(id=session_id).first()
        if not session:
            return Response(
                {"error": "Session not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        if session.site_id:
            if str(session.site_id) != str(site.id):
                return Response(
                    {"error": "Session does not belong to this site"},
                    status=status.HTTP_403_FORBIDDEN,
                )
        else:
            # Legacy row with no site: allow if site is in the session's org.
            if str(session.location_id) != str(site.location_id):
                return Response(
                    {"error": "Session does not belong to this site's organisation"},
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
        if not session.site_id:
            session.site = site
            session.save()
        else:
            session.save()

        serializer = RollCallSessionSerializerV5(session, context={"request": request})
        return Response(serializer.data)


class RollCallExcelExportViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            qs, err, err_status = _list_rollcall_sessions(request)
            if err:
                return Response({"error": err}, status=err_status)
            qs = apply_rollcall_site_filter(qs.select_related("site"), request)
            content, filename = generate_rollcall_excel(qs, request, include_site=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        except Exception as exc:
            return Response(
                {"error": f"Failed to generate Excel: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response = HttpResponse(
            content,
            content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class RollCallPdfExportViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            qs, err, err_status = _list_rollcall_sessions(request)
            if err:
                return Response({"error": err}, status=err_status)
            qs = apply_rollcall_site_filter(qs.select_related("site"), request)
            content, filename = generate_rollcall_pdf(qs, request, include_site=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)
        except Exception as exc:
            return Response(
                {"error": f"Failed to generate PDF: {exc}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response
