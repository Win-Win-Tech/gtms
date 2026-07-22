"""
POST /visitors/ai/extract/
Multipart: type=id|vehicle, image=<file>
"""

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ai import extract_from_upload


class VisitorAiExtractView(APIView):
    """
    AI assist for Manual Entry — prefill only (never blocks check-in).

    Body (multipart/form-data):
      - type: "id" | "vehicle"
      - image: file (also accepts id_image / vehicle_image / file)
    """

    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        extract_type = (
            request.data.get("type")
            or request.query_params.get("type")
            or ""
        ).strip().lower()

        uploaded = (
            request.FILES.get("image")
            or request.FILES.get("file")
            or request.FILES.get("id_image")
            or request.FILES.get("vehicle_image")
        )
        if not uploaded:
            return Response(
                {
                    "type": extract_type or None,
                    "found": False,
                    "reason": "missing_image",
                    "error": "image file is required (field name: image)",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = extract_from_upload(uploaded, extract_type)
        except ImportError as exc:
            return Response(
                {
                    "type": extract_type or None,
                    "found": False,
                    "reason": "dependency_missing",
                    "error": str(exc),
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as exc:
            return Response(
                {
                    "type": extract_type or None,
                    "found": False,
                    "reason": "internal_error",
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        http_status = status.HTTP_200_OK
        if result.get("reason") == "invalid_type":
            http_status = status.HTTP_400_BAD_REQUEST
        return Response(result, status=http_status)
