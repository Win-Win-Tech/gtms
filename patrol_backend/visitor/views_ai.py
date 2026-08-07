"""
POST /visitors/ai/extract/ (v1 - PaddleOCR)
POST /visitors/ai/extract-v2/ (v2 - RapidOCR)
Multipart: type=id|vehicle, image=<file>

Response (always):
  { "type", "found", "number", "confidence" }
"""

from rest_framework import status
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from scheduler.models import Location
from .ai import extract_from_upload
from .utils import resolve_location_for_request


def _slim_response(extract_type, result=None, found=False, number=None, confidence=None, name=None, raw_text=None):
    """Public API shape — type / found / number / name / confidence / raw_text."""
    if result is not None:
        found = bool(result.get("found"))
        raw_text = result.get("raw_text") or []
        if found:
            number = result.get("id_number") or result.get("vehicle_number") or result.get("number")
            name = result.get("name")
            confidence = result.get("confidence")
        else:
            number = None
            name = None
            confidence = None
        extract_type = result.get("type") or extract_type
    return {
        "type": extract_type or None,
        "found": found,
        "number": number,
        "name": name,
        "confidence": confidence,
        "raw_text": raw_text or [],
    }


def _is_ai_extraction_enabled_for_request(request):
    """
    Checks if AI extraction is enabled for the organization/location associated with the request.
    Returns True if enabled, False if disabled.
    """
    loc_id_raw = (
        request.data.get("location_id")
        or request.query_params.get("location_id")
    )
    loc_id, _ = resolve_location_for_request(request, loc_id_raw)
    if loc_id:
        loc = Location.objects.filter(id=loc_id, is_deleted=False).first()
        if loc:
            return bool(loc.is_ai_extraction_enabled)

    user_loc = getattr(request.user, "location", None)
    if user_loc:
        return bool(getattr(user_loc, "is_ai_extraction_enabled", True))

    return True


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

        # if not _is_ai_extraction_enabled_for_request(request):
        return Response(
                _slim_response(extract_type, found=False),
                status=status.HTTP_400_BAD_REQUEST,
        )

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


class VisitorAiExtractV2View(APIView):
    """
    AI assist v2 (RapidOCR engine) for Manual Entry — prefill only.

    POST /visitors/ai/extract-v2/
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

        if not _is_ai_extraction_enabled_for_request(request):
            return Response(
                _slim_response(extract_type, found=False),
                status=status.HTTP_400_BAD_REQUEST,
            )

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
            from .ai.pipeline_v2 import extract_from_upload_v2

            result = extract_from_upload_v2(uploaded, extract_type)
        except ImportError as exc:
            return Response(
                {
                    **_slim_response(extract_type, found=False),
                    "error": f"RapidOCR package not installed. Run 'pip install rapidocr-onnxruntime'. Detail: {exc}",
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )
        except Exception as exc:
            return Response(
                {
                    **_slim_response(extract_type, found=False),
                    "error": str(exc),
                },
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        http_status = status.HTTP_200_OK
        if result.get("reason") == "invalid_type":
            http_status = status.HTTP_400_BAD_REQUEST
        return Response(_slim_response(extract_type, result=result), status=http_status)
