"""ANPR settings helpers (env-backed via Django settings)."""

from __future__ import annotations

from django.conf import settings


def anpr_enabled() -> bool:
    return bool(getattr(settings, "ANPR_ENABLED", False))


def max_cameras() -> int:
    return max(1, int(getattr(settings, "ANPR_MAX_CAMERAS", 2)))


def detect_fps() -> float:
    return max(0.2, float(getattr(settings, "ANPR_DETECT_FPS", 3.0)))


def cooldown_sec() -> int:
    return max(5, int(getattr(settings, "ANPR_COOLDOWN_SEC", 60)))


def queue_name() -> str:
    return getattr(settings, "ANPR_QUEUE", "anpr") or "anpr"


def max_queue_depth() -> int:
    return max(1, int(getattr(settings, "ANPR_MAX_QUEUE_DEPTH", 8)))


def stale_frame_sec() -> int:
    return max(3, int(getattr(settings, "ANPR_STALE_FRAME_SEC", 90)))


def roi_margin() -> float:
    """Extra margin (fraction of frame) around the drawn ANPR zone for plate detection."""
    return min(0.3, max(0.0, float(getattr(settings, "ANPR_ROI_MARGIN", 0.08))))


def miss_retries() -> int:
    """Extra OCR attempts for a vehicle still in view after a miss / stale drop."""
    return max(0, int(getattr(settings, "ANPR_MISS_RETRIES", 2)))


def retry_collect_sec() -> float:
    """Seconds of fresh plate views gathered before a retry is queued."""
    return max(0.0, float(getattr(settings, "ANPR_RETRY_COLLECT_SEC", 2.0)))


def vote_budget_sec() -> float:
    """Skip runner-up frame OCR once a task has spent this long (queue keeps moving)."""
    return max(0.0, float(getattr(settings, "ANPR_VOTE_BUDGET_SEC", 20.0)))


def motion_gate() -> bool:
    """Skip detection while the ANPR zone is unchanged (idle gate saves CPU)."""
    return bool(getattr(settings, "ANPR_MOTION_GATE", True))


def idle_detect_sec() -> float:
    """While nothing moves, still detect this often (parked vehicles, OCR results)."""
    return max(0.2, float(getattr(settings, "ANPR_IDLE_DETECT_SEC", 1.0)))


def motion_hold_sec() -> float:
    """Keep full detection rate this long after the last motion (vehicle stopping)."""
    return max(0.0, float(getattr(settings, "ANPR_MOTION_HOLD_SEC", 3.0)))


def motion_min_area() -> float:
    """Fraction of the zone that must change to count as motion."""
    return min(0.2, max(0.0005, float(getattr(settings, "ANPR_MOTION_MIN_AREA", 0.003))))


def min_track_hits() -> int:
    return max(1, int(getattr(settings, "ANPR_MIN_TRACK_HITS", 2)))


def lost_flush_sec() -> float:
    """A vehicle unseen this long is sent with the frames collected so far."""
    return max(0.3, float(getattr(settings, "ANPR_LOST_FLUSH_SEC", 1.0)))


def parked_memory_sec() -> float:
    """How long a read parked vehicle's spot + plate is remembered after last seen."""
    return max(0.0, float(getattr(settings, "ANPR_PARKED_MEMORY_SEC", 120.0)))


def detect_conf() -> float:
    # Lower default helps distant/parked plates after 640→higher downscale.
    return float(getattr(settings, "ANPR_DETECT_CONF", 0.18))


def detect_max_side() -> int:
    """Longest side (px) for plate YOLO input. Higher = better distant plates, slower CPU."""
    return max(480, int(getattr(settings, "ANPR_DETECT_MAX_SIDE", 960)))


def camera_refresh_sec() -> int:
    """Seconds between SiteCamera DB polls (min 60). Default 300 (5 min)."""
    return max(60, int(getattr(settings, "ANPR_CAMERA_REFRESH_SEC", 300)))
