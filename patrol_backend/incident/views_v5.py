from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.site_access import assert_caller_can_access_site, get_site_or_error
from .exports import generate_incident_excel, generate_incident_pdf, get_incident_export_queryset
from .models import incidentreport
from .serializers_v5 import IncidentSerializerV5
from .site_filter import apply_incident_site_filter
from .views import (
    IncidentAssignView,
    IncidentReportView,
    IncidentResolveView,
)


def _v5_error(exc):
    if isinstance(exc, ValidationError):
        return Response(exc.detail, status=status.HTTP_400_BAD_REQUEST)
    if isinstance(exc, PermissionDenied):
        return Response({"error": str(exc)}, status=status.HTTP_403_FORBIDDEN)
    raise exc


class IncidentReportViewV5(IncidentReportView):
    """POST /incident/v5/report/ — same as live create, site_id required."""

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        site_id = request.data.get("site_id")
        if not site_id:
            return Response(
                {"site_id": ["This field is required."]},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            site = get_site_or_error(site_id)
            assert_caller_can_access_site(request.user, site)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)

        # Live post() saves location=request.user.location, then Cloudinary + Twilio.
        # Point that at the selected site’s org so alerts use the right timezone.
        original_location = getattr(request.user, "location", None)
        request.user.location = site.location
        try:
            response = super().post(request)
        finally:
            request.user.location = original_location

        if response.status_code != status.HTTP_201_CREATED:
            return response

        ticket_number = response.data.get("ticket_number")
        incident = (
            incidentreport.objects.select_related("site", "location", "checkpoint")
            .filter(ticket_number=ticket_number)
            .first()
        )
        if incident:
            incident.site = site
            incident.location = site.location
            incident.save(update_fields=["site", "location"])
            serializer = IncidentSerializerV5(incident, context={"request": request})
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return response


class IncidentFilterViewV5(APIView):
    """GET /incident/v5/dashboard/filter/ — live filters plus site_id."""

    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            queryset, error_response = get_incident_export_queryset(request)
            if error_response:
                return error_response
            queryset = apply_incident_site_filter(queryset, request)
            serializer = IncidentSerializerV5(queryset, many=True, context={"request": request})
            return Response(serializer.data, status=status.HTTP_200_OK)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class IncidentExportExcelViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            queryset, error_response = get_incident_export_queryset(request)
            if error_response:
                return error_response
            queryset = apply_incident_site_filter(queryset, request)
            return generate_incident_excel(queryset, request, include_site=True)
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class IncidentExportPdfViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            queryset, error_response = get_incident_export_queryset(request)
            if error_response:
                return error_response
            queryset = apply_incident_site_filter(queryset, request)
            from django.http import HttpResponse

            try:
                content, filename = generate_incident_pdf(queryset, request, include_site=True)
            except ImportError as exc:
                return Response({"error": str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            response = HttpResponse(content, content_type="application/pdf")
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class MyTicketsExportExcelViewV5(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        try:
            queryset, error_response = get_incident_export_queryset(
                request, assigned_to_user=request.user
            )
            if error_response:
                return error_response
            queryset = apply_incident_site_filter(queryset, request)
            return generate_incident_excel(
                queryset, request, prefix="mytickets", include_site=True
            )
        except (ValidationError, PermissionDenied) as exc:
            return _v5_error(exc)


class IncidentAssignViewV5(IncidentAssignView):
    def post(self, request, ticket_number):
        response = super().post(request, ticket_number)
        if response.status_code != status.HTTP_200_OK:
            return response
        incident = (
            incidentreport.objects.select_related("site")
            .filter(ticket_number=ticket_number)
            .first()
        )
        if not incident:
            return response
        serializer = IncidentSerializerV5(incident, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class IncidentResolveViewV5(IncidentResolveView):
    def post(self, request, ticket_number):
        response = super().post(request, ticket_number)
        if response.status_code != status.HTTP_200_OK:
            return response
        incident = (
            incidentreport.objects.select_related("site")
            .filter(ticket_number=ticket_number)
            .first()
        )
        if not incident:
            return response
        serializer = IncidentSerializerV5(incident, context={"request": request})
        return Response(serializer.data, status=status.HTTP_200_OK)
