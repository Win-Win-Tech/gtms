"""Face CCTV settings helpers (env-backed via Django settings)."""

from __future__ import annotations

from django.conf import settings


def enabled() -> bool:
    return bool(getattr(settings, "FACE_CCTV_ENABLED", False))


def max_cameras() -> int:
    return max(1, int(getattr(settings, "FACE_CCTV_MAX_CAMERAS", 1)))


def fps() -> float:
    return max(0.2, float(getattr(settings, "FACE_CCTV_FPS", 2.0)))


def detect_max_side() -> int:
    return max(320, int(getattr(settings, "FACE_CCTV_DETECT_MAX_SIDE", 640)))


def detect_score() -> float:
    return min(0.99, max(0.3, float(getattr(settings, "FACE_CCTV_DETECT_SCORE", 0.8))))


def min_face_px() -> int:
    return max(20, int(getattr(settings, "FACE_CCTV_MIN_FACE_PX", 60)))


def min_track_hits() -> int:
    return max(1, int(getattr(settings, "FACE_CCTV_MIN_TRACK_HITS", 2)))


def track_lost_sec() -> float:
    return max(0.3, float(getattr(settings, "FACE_CCTV_TRACK_LOST_SEC", 1.5)))


def motion_gate() -> bool:
    return bool(getattr(settings, "FACE_CCTV_MOTION_GATE", True))


def idle_detect_sec() -> float:
    return max(0.2, float(getattr(settings, "FACE_CCTV_IDLE_DETECT_SEC", 2.0)))


def motion_hold_sec() -> float:
    return max(0.0, float(getattr(settings, "FACE_CCTV_MOTION_HOLD_SEC", 2.0)))


def motion_min_area() -> float:
    return min(0.2, max(0.0005, float(getattr(settings, "FACE_CCTV_MOTION_MIN_AREA", 0.003))))


def camera_refresh_sec() -> int:
    return max(60, int(getattr(settings, "FACE_CCTV_CAMERA_REFRESH_SEC", 300)))


def cv_threads() -> int:
    return max(1, int(getattr(settings, "FACE_CCTV_CV_THREADS", 1)))


def save_debug_crops() -> bool:
    return bool(getattr(settings, "FACE_CCTV_SAVE_DEBUG_CROPS", False))


def debug_keep() -> int:
    return max(10, int(getattr(settings, "FACE_CCTV_DEBUG_KEEP", 200)))
