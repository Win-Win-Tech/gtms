"""Enqueue helpers + Redis queue depth check for ANPR backpressure."""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.conf import settings

from . import settings_helpers as anpr_settings

logger = logging.getLogger(__name__)


def queue_depth() -> int:
    """Approximate length of the anpr Celery queue in Redis."""
    try:
        import redis
        from urllib.parse import urlparse

        url = getattr(settings, "CELERY_BROKER_URL", "redis://localhost:6379/0")
        parsed = urlparse(url)
        db = int((parsed.path or "/0").lstrip("/") or "0")
        client = redis.Redis(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            db=db,
            socket_connect_timeout=1,
            socket_timeout=1,
        )
        # Celery Redis transport list key for named queue
        q = anpr_settings.queue_name()
        return int(client.llen(q) or 0)
    except Exception as exc:
        logger.debug("[ANPR] queue_depth failed: %s", exc)
        return 0


def can_enqueue() -> bool:
    return queue_depth() < anpr_settings.max_queue_depth()


def enqueue_anpr_frame(payload: Dict[str, Any]) -> Optional[str]:
    """
    Send process_anpr_frame to the dedicated anpr queue.
    Returns async result id or None if skipped.
    """
    if not can_enqueue():
        logger.warning("[ANPR] backpressure — skip enqueue depth high track=%s", payload.get("track_id"))
        return None

    from .tasks import process_anpr_frame

    async_result = process_anpr_frame.apply_async(
        kwargs=payload,
        queue=anpr_settings.queue_name(),
        expires=anpr_settings.stale_frame_sec() + 30,
    )
    logger.info(
        "[ANPR] enqueued task=%s track=%s camera=%s",
        async_result.id,
        payload.get("track_id"),
        payload.get("camera_id"),
    )
    return async_result.id
