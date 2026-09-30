"""Enqueue helpers + Redis queue depth check for ANPR backpressure."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any, Dict, Optional, Tuple

from django.conf import settings

from . import settings_helpers as anpr_settings

logger = logging.getLogger(__name__)

_client_lock = threading.Lock()
_client = None


def redis_client():
    """Shared client on the Celery broker Redis (queue depth + OCR result feedback)."""
    global _client
    if _client is not None:
        return _client
    with _client_lock:
        if _client is None:
            import redis

            url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
            _client = redis.Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1)
    return _client


def queue_depth() -> int:
    """Approximate length of the anpr Celery queue in Redis."""
    try:
        # Celery Redis transport list key for named queue
        return int(redis_client().llen(anpr_settings.queue_name()) or 0)
    except Exception as exc:
        logger.debug("[ANPR] queue_depth failed: %s", exc)
        return 0


def can_enqueue() -> bool:
    return queue_depth() < anpr_settings.max_queue_depth()


def _result_key(track_id: str) -> str:
    return f"anpr:result:{track_id}"


def report_result(track_id: Optional[str], outcome: str, plate: str = "") -> None:
    """
    Worker → reader: ``ok`` (plate read, gate decided) or ``miss`` (no usable
    plate / stale / bad frame). A ``miss`` lets the reader retry a vehicle that
    is still standing in front of the camera. The plate rides along as
    ``ok|PLATE`` so the reader knows which vehicle is parked where.
    """
    if not track_id:
        return
    value = f"{outcome}|{plate}" if plate else outcome
    try:
        redis_client().setex(_result_key(track_id), 600, value)
    except Exception as exc:
        logger.debug("[ANPR] report_result failed track=%s: %s", track_id, exc)


def split_result(raw: Optional[str]) -> Tuple[Optional[str], str]:
    """``ok|TN01AB1234`` → (``ok``, ``TN01AB1234``); None → (None, "")."""
    if raw is None:
        return None, ""
    outcome, _, plate = str(raw).partition("|")
    return outcome, plate


def pop_result(track_id: str) -> Optional[str]:
    try:
        pipe = redis_client().pipeline()
        pipe.get(_result_key(track_id))
        pipe.delete(_result_key(track_id))
        raw, _ = pipe.execute()
    except Exception as exc:
        logger.debug("[ANPR] pop_result failed track=%s: %s", track_id, exc)
        return None
    if raw is None:
        return None
    return raw.decode() if isinstance(raw, bytes) else str(raw)


def _parked_key(camera_id: str) -> str:
    return f"anpr:parked:{camera_id}"


def save_parked(camera_id: str, spots: list, ttl: float) -> None:
    """Read parked vehicles [[box_norm, plate, wall_time], …] — survives a reader restart."""
    try:
        if spots:
            redis_client().setex(_parked_key(camera_id), max(1, int(ttl)), json.dumps(spots))
        else:
            redis_client().delete(_parked_key(camera_id))
    except Exception as exc:
        logger.debug("[ANPR] save_parked failed camera=%s: %s", camera_id, exc)


def load_parked(camera_id: str) -> list:
    try:
        raw = redis_client().get(_parked_key(camera_id))
        return json.loads(raw) if raw else []
    except Exception as exc:
        logger.debug("[ANPR] load_parked failed camera=%s: %s", camera_id, exc)
        return []


def clear_result(track_id: str) -> None:
    try:
        redis_client().delete(_result_key(track_id))
    except Exception as exc:
        logger.debug("[ANPR] clear_result failed track=%s: %s", track_id, exc)


def enqueue_anpr_frame(payload: Dict[str, Any]) -> Optional[str]:
    """
    Send process_anpr_frame to the dedicated anpr queue.
    Returns async result id or None if skipped.
    """
    if not can_enqueue():
        logger.warning("[ANPR] backpressure — skip enqueue depth high track=%s", payload.get("track_id"))
        return None

    from .tasks import process_anpr_frame

    clear_result(str(payload.get("track_id") or ""))
    async_result = process_anpr_frame.apply_async(
        kwargs=payload,
        queue=anpr_settings.queue_name(),
        expires=anpr_settings.stale_frame_sec() + 30,
    )
    logger.info(
        "[ANPR] enqueued task=%s track=%s camera=%s attempt=%s",
        async_result.id,
        payload.get("track_id"),
        payload.get("camera_id"),
        payload.get("attempt", 1),
    )
    return async_result.id
