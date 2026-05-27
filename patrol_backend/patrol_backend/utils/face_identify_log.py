"""
Persist kiosk face-identification failure images for audit/debug.

Layout: {MEDIA_ROOT}/logphoto/{YYYY-MM-DD}/{location_id}/{error_code}_{random}.jpg
"""
import logging
import os
import threading
import uuid
from datetime import date
from typing import Optional

from django.conf import settings

logger = logging.getLogger(__name__)

FACE_IDENTIFY_ERROR_CODES = frozenset(
    {
        "face_not_detected",
        "no_enrolled_faces",
        "face_not_matched",
    }
)


def save_face_identify_error_image(
    location_id: str,
    error_code: str,
    image_bytes: bytes,
) -> Optional[str]:
    """
    Write image bytes under logphoto/ date / location / {error_code}_{id}.jpg
    Returns relative path from MEDIA_ROOT, or None on skip/failure.
    """
    if not getattr(settings, "FACE_IDENTIFY_LOG_PHOTOS_ENABLED", True):
        return None
    if not image_bytes or error_code not in FACE_IDENTIFY_ERROR_CODES:
        return None

    if getattr(settings, "FACE_IDENTIFY_LOG_ASYNC", True):
        threading.Thread(
            target=_write_face_identify_error_image,
            args=(str(location_id), error_code, image_bytes),
            daemon=True,
        ).start()
        return None

    return _write_face_identify_error_image(str(location_id), error_code, image_bytes)


def _write_face_identify_error_image(
    location_id: str, error_code: str, image_bytes: bytes
) -> Optional[str]:
    try:
        day = date.today().isoformat()
        loc_part = str(location_id).replace(os.sep, "_").strip() or "unknown"
        filename = f"{error_code}_{uuid.uuid4().hex[:12]}.jpg"
        rel_dir = os.path.join("logphoto", day, loc_part)
        abs_dir = os.path.join(settings.MEDIA_ROOT, rel_dir)
        os.makedirs(abs_dir, exist_ok=True)
        abs_path = os.path.join(abs_dir, filename)
        with open(abs_path, "wb") as f:
            f.write(image_bytes)
        rel_path = os.path.join(rel_dir, filename)
        logger.info(
            "Face identify error image saved: location=%s code=%s path=%s",
            location_id,
            error_code,
            rel_path,
        )
        return rel_path
    except Exception as exc:
        logger.warning(
            "Failed to save face identify error image location=%s code=%s: %s",
            location_id,
            error_code,
            exc,
        )
        return None
