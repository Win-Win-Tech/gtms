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
    # Lower default helps distant/parked plates after 640→higher downscale.
    return float(getattr(settings, "ANPR_DETECT_CONF", 0.18))


def detect_max_side() -> int:
    """Longest side (px) for plate YOLO input. Higher = better distant plates, slower CPU."""
    return max(480, int(getattr(settings, "ANPR_DETECT_MAX_SIDE", 960)))


def camera_refresh_sec() -> int:
    """Seconds between SiteCamera DB polls (min 60). Default 300 (5 min)."""
    return max(60, int(getattr(settings, "ANPR_CAMERA_REFRESH_SEC", 300)))
