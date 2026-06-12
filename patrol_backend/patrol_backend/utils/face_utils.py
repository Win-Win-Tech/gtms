"""
Face attendance helpers (optional dependency: face_recognition + dlib).

If packages are not installed, is_face_attendance_available() is False and
checkin_v4/checkout_v4 return 503 when a location has face attendance enabled.
"""
import io
import logging
from dataclasses import dataclass
from typing import List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# Default tolerance for 1:1 verify (lower = stricter)
DEFAULT_FACE_TOLERANCE = 0.45


def get_identify_tolerance() -> float:
    from django.conf import settings

    return float(getattr(settings, "FACE_IDENTIFY_TOLERANCE", 0.45))


def get_identify_tolerance_strict() -> float:
    from django.conf import settings

    return float(getattr(settings, "FACE_IDENTIFY_TOLERANCE_STRICT", 0.38))


def get_identify_min_margin() -> float:
    from django.conf import settings

    return float(getattr(settings, "FACE_IDENTIFY_MIN_MARGIN", 0.055))


def get_identify_top_k() -> int:
    from django.conf import settings

    return max(2, int(getattr(settings, "FACE_IDENTIFY_TOP_K", 3)))

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


def _apply_exif_orientation(img):
    """Phones often store portrait JPEGs with EXIF rotation; browsers show upright, raw pixels may not."""
    try:
        from PIL import ImageOps

        return ImageOps.exif_transpose(img)
    except Exception:
        return img


def load_image_rgb_array(image_bytes: bytes, max_dimension: Optional[int] = None) -> Optional[np.ndarray]:
    """
    Decode upload to RGB numpy array with EXIF orientation applied.
    Optionally downscale so longest side <= max_dimension (keeps kiosk fast).
    """
    fr = _load_face_recognition()
    if not fr or not image_bytes:
        return None
    try:
        from PIL import Image

        with Image.open(io.BytesIO(image_bytes)) as img:
            img = _apply_exif_orientation(img)
            img = img.convert("RGB")
            w, h = img.size
            if max_dimension and max(w, h) > max_dimension and max(w, h) > 0:
                scale = max_dimension / float(max(w, h))
                new_w = max(1, int(w * scale))
                new_h = max(1, int(h * scale))
                img = img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            return np.asarray(img)
    except Exception as exc:
        logger.warning("load_image_rgb_array failed, fallback to face_recognition loader: %s", exc)
        try:
            return fr.load_image_file(io.BytesIO(image_bytes))
        except Exception:
            return None


def _encoding_from_image(
    image: np.ndarray,
    *,
    upsample: int,
    num_jitters: int,
) -> Optional[np.ndarray]:
    fr = _load_face_recognition()
    if fr is None or image is None:
        return None
    locations = fr.face_locations(image, number_of_times_to_upsample=max(0, int(upsample)))
    if not locations:
        return None
    encodings = fr.face_encodings(image, locations, num_jitters=max(0, int(num_jitters)))
    if not encodings:
        return None
    return encodings[0]


def _kiosk_detect_attempts() -> List[Tuple[Optional[int], int, int]]:
    """
    Ordered (max_dimension, upsample, num_jitters) attempts.
    Fast first; retry with stronger detect if settings allow.
    """
    from django.conf import settings

    max_w = int(getattr(settings, "FACE_KIOSK_MAX_IMAGE_WIDTH", 640))
    upsample = int(getattr(settings, "FACE_KIOSK_UPSAMPLE", 0))
    num_jitters = int(getattr(settings, "FACE_KIOSK_NUM_JITTERS", 0))
    attempts = [(max_w, upsample, num_jitters)]

    if not getattr(settings, "FACE_KIOSK_DETECT_RETRY", True):
        return attempts

    retry_upsample = int(getattr(settings, "FACE_KIOSK_RETRY_UPSAMPLE", 1))
    retry_max = int(getattr(settings, "FACE_KIOSK_RETRY_MAX_IMAGE_WIDTH", 1280))
    retry_jitters = int(getattr(settings, "FACE_KIOSK_RETRY_NUM_JITTERS", 1))

    if (retry_upsample, retry_max, retry_jitters) != (upsample, max_w, num_jitters):
        attempts.append((retry_max, retry_upsample, retry_jitters))

    # Last resort: full resolution + stronger upsample (some devices only work here).
    full_max = int(getattr(settings, "FACE_KIOSK_FULL_MAX_IMAGE_WIDTH", 0)) or None
    full_upsample = int(getattr(settings, "FACE_KIOSK_FULL_UPSAMPLE", 1))
    if full_max is not None or full_upsample > retry_upsample:
        attempts.append((full_max, full_upsample, retry_jitters))

    # Deduplicate while preserving order
    seen = set()
    unique = []
    for item in attempts:
        if item not in seen:
            seen.add(item)
            unique.append(item)
    return unique


def get_face_encoding(image_bytes: bytes, *, fast: bool = False) -> Optional[np.ndarray]:
    """Return 128-d encoding or None if no face / error."""
    fr = _load_face_recognition()
    if not fr or not image_bytes:
        return None
    try:
        if fast:
            for max_dim, upsample, num_jitters in _kiosk_detect_attempts():
                image = load_image_rgb_array(image_bytes, max_dimension=max_dim)
                if image is None:
                    continue
                enc = _encoding_from_image(image, upsample=upsample, num_jitters=num_jitters)
                if enc is not None:
                    return enc
            logger.info("No face found in kiosk image after all detect attempts.")
            return None

        image = load_image_rgb_array(image_bytes, max_dimension=None)
        if image is None:
            return None
        enc = _encoding_from_image(image, upsample=1, num_jitters=2)
        if enc is None:
            logger.info("No face found in image.")
        return enc
    except Exception as e:
        logger.exception("get_face_encoding failed: %s", e)
        return None


def get_face_encoding_kiosk(image_bytes: bytes) -> Optional[np.ndarray]:
    return get_face_encoding(image_bytes, fast=True)


@dataclass
class IdentifyFrameResult:
    encoding: Optional[np.ndarray]
    face_count: int = 0
    tier: str = "fast"


def _identify_quality_attempt() -> Tuple[Optional[int], int, int]:
    from django.conf import settings

    max_w = int(getattr(settings, "FACE_IDENTIFY_MAX_IMAGE_WIDTH", 960))
    upsample = int(getattr(settings, "FACE_IDENTIFY_UPSAMPLE", 1))
    num_jitters = int(getattr(settings, "FACE_IDENTIFY_NUM_JITTERS", 1))
    return max_w, upsample, num_jitters


def extract_identify_frame(image_bytes: bytes, *, use_quality: bool = False) -> IdentifyFrameResult:
    """
    Single decode + detect pass. Fast tier (~640px) for speed; quality tier for borderline cases.
    """
    fr = _load_face_recognition()
    if not fr or not image_bytes:
        return IdentifyFrameResult(None, 0, "quality" if use_quality else "fast")

    tier = "quality" if use_quality else "fast"
    if use_quality:
        attempts = [_identify_quality_attempt()]
    else:
        # Single 640px pass for speed (~1s). Quality tier retries harder cases.
        kiosk_attempts = _kiosk_detect_attempts()
        attempts = [kiosk_attempts[0]] if kiosk_attempts else [(640, 0, 0)]

    for max_dim, upsample, num_jitters in attempts:
        image = load_image_rgb_array(image_bytes, max_dimension=max_dim)
        if image is None:
            continue
        locations = fr.face_locations(image, number_of_times_to_upsample=max(0, int(upsample)))
        if not locations:
            continue
        encodings = fr.face_encodings(image, locations, num_jitters=max(0, int(num_jitters)))
        if not encodings:
            continue
        return IdentifyFrameResult(encodings[0], len(locations), tier)

    return IdentifyFrameResult(None, 0, tier)


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
