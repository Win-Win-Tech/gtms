"""ANPR settings helpers (env-backed via Django settings)."""

from __future__ import annotations

from django.conf import settings


def anpr_enabled() -> bool:
    return bool(getattr(settings, "ANPR_ENABLED", False))


def max_cameras() -> int:
    return max(1, int(getattr(settings, "ANPR_MAX_CAMERAS", 2)))


def detect_fps() -> float:
    return max(0.2, float(getattr(settings, "ANPR_DETECT_FPS", 1.0)))


def cooldown_sec() -> int:
    return max(5, int(getattr(settings, "ANPR_COOLDOWN_SEC", 60)))


def queue_name() -> str:
    return getattr(settings, "ANPR_QUEUE", "anpr") or "anpr"


def max_queue_depth() -> int:
    return max(1, int(getattr(settings, "ANPR_MAX_QUEUE_DEPTH", 8)))


def stale_frame_sec() -> int:
    return max(3, int(getattr(settings, "ANPR_STALE_FRAME_SEC", 10)))


def min_track_hits() -> int:
    return max(1, int(getattr(settings, "ANPR_MIN_TRACK_HITS", 2)))


def detect_conf() -> float:
    return float(getattr(settings, "ANPR_DETECT_CONF", 0.25))


def camera_refresh_sec() -> int:
    """Seconds between SiteCamera DB polls (min 60). Default 300 (5 min)."""
    return max(60, int(getattr(settings, "ANPR_CAMERA_REFRESH_SEC", 300)))
