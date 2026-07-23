"""
POST /visitors/ai/extract/
Multipart: type=id|vehicle, image=<file>

Response (always):
  { "type", "found", "number", "confidence" }
"""

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .ai import extract_from_upload


def _slim_response(extract_type, result=None, found=False, number=None, confidence=None):
    """Public API shape — only type / found / number / confidence."""
    if result is not None:
        found = bool(result.get("found"))
        if found:
            number = result.get("id_number") or result.get("vehicle_number") or result.get("number")
            confidence = result.get("confidence")
        else:
            number = None
            confidence = None
        extract_type = result.get("type") or extract_type
    return {
        "type": extract_type or None,
        "found": found,
        "number": number,
        "confidence": confidence,
    }


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
                _slim_response(extract_type, found=False),
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            result = extract_from_upload(uploaded, extract_type)
        except ImportError:
            return Response(
                _slim_response(extract_type, found=False),
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception:
            return Response(
                _slim_response(extract_type, found=False),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        http_status = status.HTTP_200_OK
        if result.get("reason") == "invalid_type":
            http_status = status.HTTP_400_BAD_REQUEST
        return Response(_slim_response(extract_type, result=result), status=http_status)
