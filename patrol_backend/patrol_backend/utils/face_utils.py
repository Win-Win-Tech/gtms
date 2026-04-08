"""
Face attendance helpers (optional dependency: face_recognition + dlib).

If packages are not installed, is_face_attendance_available() is False and
checkin_v4/checkout_v4 return 503 when a location has face attendance enabled.
"""
import io
import logging
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default tolerance for face_recognition.compare_faces (lower stricter)
DEFAULT_FACE_TOLERANCE = 0.45

_face_recognition = None  # lazy module or None; False = import failed
_face_recognition_failed = False


def _load_face_recognition():
    global _face_recognition, _face_recognition_failed
    if _face_recognition_failed:
        return None
    if _face_recognition is not None:
        return _face_recognition
    try:
        import face_recognition as fr  # noqa: WPS433 — runtime optional import

        _face_recognition = fr
        return fr
    except Exception as e:
        logger.warning("face_recognition not available: %s", e)
        _face_recognition_failed = True
        return None


def is_face_attendance_available() -> bool:
    return _load_face_recognition() is not None


def get_face_encoding(image_bytes: bytes) -> Optional[np.ndarray]:
    """Return 128-d encoding or None if no face / error."""
    fr = _load_face_recognition()
    if not fr or not image_bytes:
        return None
    try:
        image = fr.load_image_file(io.BytesIO(image_bytes))
        encodings = fr.face_encodings(image)
        if not encodings:
            logger.info("No face found in image.")
            return None
        return encodings[0]
    except Exception as e:
        logger.exception("get_face_encoding failed: %s", e)
        return None


def encoding_to_bytes(enc: np.ndarray) -> bytes:
    return np.asarray(enc, dtype=np.float64).tobytes()


def bytes_to_encoding(raw: bytes) -> Optional[np.ndarray]:
    if not raw:
        return None
    try:
        return np.frombuffer(raw, dtype=np.float64).copy()
    except Exception as e:
        logger.error("bytes_to_encoding failed: %s", e)
        return None


def compare_encodings(
    enrolled: np.ndarray,
    live: np.ndarray,
    tolerance: float = DEFAULT_FACE_TOLERANCE,
) -> Tuple[bool, float]:
    """
    Returns (match_ok, distance). Lower distance is better.
    Uses face_recognition.face_distance.
    """
    fr = _load_face_recognition()
    if not fr:
        return False, float("inf")
    try:
        dist = fr.face_distance([enrolled], live)[0]
        # compare_faces uses tolerance as max distance for a match
        ok = bool(dist <= tolerance)
        return ok, float(dist)
    except Exception as e:
        logger.exception("compare_encodings failed: %s", e)
        return False, float("inf")


def verify_user_face(
    user,
    live_image_bytes: bytes,
    tolerance: float = DEFAULT_FACE_TOLERANCE,
) -> Tuple[bool, str, Optional[float]]:
    """
    1:1 verify live image against user's stored encoding.
    Returns (ok, error_message, distance_or_none).
    """
    if not is_face_attendance_available():
        return False, "Face recognition is not installed on the server", None

    ref = None
    if user.face_encoding:
        ref = bytes_to_encoding(bytes(user.face_encoding))
    if ref is None and user.face_photo:
        try:
            user.face_photo.open("rb")
            try:
                ref = get_face_encoding(user.face_photo.read())
            finally:
                user.face_photo.close()
        except Exception as e:
            logger.warning("Could not load face_photo for user %s: %s", user.pk, e)

    if ref is None:
        return False, "User has no enrolled face (upload face_photo in admin or app)", None

    live = get_face_encoding(live_image_bytes)
    if live is None:
        return False, "No face detected in uploaded image", None

    ok, dist = compare_encodings(ref, live, tolerance=tolerance)
    if not ok:
        return False, "Face does not match enrolled user", dist
    return True, "", dist


def refresh_user_face_encoding_from_photo(user) -> bool:
    """Recompute face_encoding from user.face_photo file. Returns True if saved."""
    if not user.face_photo:
        return False
    try:
        user.face_photo.open("rb")
        try:
            raw = user.face_photo.read()
        finally:
            user.face_photo.close()
        enc = get_face_encoding(raw)
        if enc is None:
            return False
        user.face_encoding = encoding_to_bytes(enc)
        user.save(update_fields=["face_encoding"])
        return True
    except Exception as e:
        logger.exception("refresh_user_face_encoding_from_photo: %s", e)
        return False
