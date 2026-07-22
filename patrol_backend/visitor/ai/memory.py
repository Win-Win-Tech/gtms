"""
Memory helpers for 8 GB hosts — idle unload of OCR / YOLO singletons.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time

logger = logging.getLogger(__name__)

# After this many idle seconds, drop PaddleOCR / YOLO from RAM.
# Set VISITOR_AI_IDLE_UNLOAD_SEC=0 to keep models warm forever.
IDLE_UNLOAD_SEC = float(os.environ.get("VISITOR_AI_IDLE_UNLOAD_SEC", "90"))

_lock = threading.Lock()
_last_used = 0.0
_timer: threading.Timer | None = None


def touch_activity() -> None:
    """Mark AI as used; (re)schedule idle unload."""
    global _last_used, _timer
    with _lock:
        _last_used = time.monotonic()
        if IDLE_UNLOAD_SEC <= 0:
            return
        if _timer is not None:
            _timer.cancel()
        _timer = threading.Timer(IDLE_UNLOAD_SEC, _idle_unload)
        _timer.daemon = True
        _timer.start()


def _idle_unload() -> None:
    with _lock:
        idle_for = time.monotonic() - _last_used
        if idle_for + 0.5 < IDLE_UNLOAD_SEC:
            return
    logger.info(
        "Visitor AI idle %.0fs — unloading OCR/YOLO to free RAM",
        idle_for,
    )
    unload_all_models()


def unload_all_models() -> None:
    """Drop warm models and ask the allocator to return memory."""
    try:
        from .ocr_engine import unload_ocr

        unload_ocr()
    except Exception as exc:
        logger.debug("unload_ocr failed: %s", exc)
    try:
        from .detect import unload_yolo

        unload_yolo()
    except Exception as exc:
        logger.debug("unload_yolo failed: %s", exc)
    gc.collect()
    try:
        import torch

        if hasattr(torch, "cuda") and torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass
