"""
RapidOCR wrapper module — ONNX Runtime lightweight engine.
CPU-friendly, low memory footprint, fast inference.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import List, Tuple

logger = logging.getLogger(__name__)

_rapid_ocr_lock = threading.Lock()
_rapid_ocr_engine = None


def ensure_rapid_ocr_ready() -> None:
    """Eagerly load RapidOCR instance."""
    _get_rapid_ocr()


def unload_rapid_ocr() -> None:
    """Drop the warm RapidOCR instance to release RAM when idle."""
    global _rapid_ocr_engine
    with _rapid_ocr_lock:
        if _rapid_ocr_engine is None:
            return
        logger.info("Unloading RapidOCR engine")
        _rapid_ocr_engine = None


def _get_rapid_ocr():
    global _rapid_ocr_engine
    if _rapid_ocr_engine is not None:
        return _rapid_ocr_engine

    with _rapid_ocr_lock:
        if _rapid_ocr_engine is not None:
            return _rapid_ocr_engine

        try:
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise ImportError(
                "rapidocr-onnxruntime is required for visitor AI v2. "
                "Install it using: pip install rapidocr-onnxruntime"
            ) from exc

        logger.info("Loading RapidOCR (ONNX Runtime engine)")
        try:
            _rapid_ocr_engine = RapidOCR()
        except Exception as exc:
            logger.error("Failed to initialize RapidOCR: %s", exc)
            raise RuntimeError(f"RapidOCR initialization failed: {exc}") from exc

        return _rapid_ocr_engine


def run_rapid_ocr(bgr_image) -> List[Tuple[str, float]]:
    """
    Run RapidOCR on BGR image.

    Returns list of (text, confidence) sorted by confidence desc.
    """
    if bgr_image is None or getattr(bgr_image, "size", 0) == 0:
        return []

    ocr = _get_rapid_ocr()
    lines: List[Tuple[str, float]] = []

    try:
        # RapidOCR returns tuple (result, elapse)
        # result is a list of [box, text, score]
        result, _ = ocr(bgr_image)
    except Exception as exc:
        logger.error("Error executing RapidOCR: %s", exc)
        return lines

    if not result:
        return lines

    for item in result:
        if not item or len(item) < 3:
            continue
        text = str(item[1]).strip()
        try:
            score = float(item[2])
        except (ValueError, TypeError):
            score = 0.0

        if text:
            lines.append((text, score))

    lines.sort(key=lambda t: t[1], reverse=True)
    return lines
