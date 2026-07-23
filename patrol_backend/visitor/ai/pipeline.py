"""
End-to-end extract pipeline for type=id | type=vehicle.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict

from .detect import crop_with_padding, detect_id_region, detect_vehicles
from .ocr_engine import run_ocr
from .parse import extract_id_number, extract_vehicle_number
from .preprocess import load_and_resize_image, pil_to_bgr_ndarray

logger = logging.getLogger(__name__)

# Soft timeout for whole pipeline (seconds).
# CPU cold start + PaddleOCR often needs 15–30s; 8s was too aggressive.
DEFAULT_TIMEOUT_SEC = float(os.environ.get("VISITOR_AI_TIMEOUT_SEC", "45"))
MIN_OCR_CONF = float(os.environ.get("VISITOR_AI_MIN_OCR_CONF", "0.45"))
# Limit concurrent AI jobs on small VPS (4 vCPU / 8 GB)
MAX_CONCURRENT = int(os.environ.get("VISITOR_AI_MAX_CONCURRENT", "1"))
_ai_sema = threading.Semaphore(max(1, MAX_CONCURRENT))


def extract_from_upload(file_obj, extract_type: str) -> Dict[str, Any]:
    """
    Public entry used by the Django view.

    Returns a dict always suitable for JSONResponse (found true/false).
    Never raises for "not found" cases; raises ImportError for missing packages.
    """
    extract_type = (extract_type or "").strip().lower()
    if extract_type not in ("id", "vehicle"):
        return {
            "type": extract_type or None,
            "found": False,
            "reason": "invalid_type",
            "error": "type must be 'id' or 'vehicle'",
        }

    acquired = _ai_sema.acquire(blocking=False)
    if not acquired:
        return {
            "type": extract_type,
            "found": False,
            "reason": "busy",
            "error": "AI service is busy — retry in a moment",
        }

    timeout = DEFAULT_TIMEOUT_SEC
    t0 = time.monotonic()
    try:
        from .memory import touch_activity
        from .ocr_engine import ensure_ocr_ready

        # Warm OCR only (YOLO stays lazy — ID path never loads it).
        ensure_ocr_ready()
        touch_activity()

        def _run():
            img = load_and_resize_image(file_obj)
            bgr = pil_to_bgr_ndarray(img)
            if extract_type == "id":
                return _pipeline_id(bgr, t0)
            return _pipeline_vehicle(bgr, t0)

        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run)
            try:
                result = fut.result(timeout=timeout)
                touch_activity()
                return result
            except FuturesTimeout:
                logger.warning("Visitor AI timed out after %ss type=%s", timeout, extract_type)
                touch_activity()
                return {
                    "type": extract_type,
                    "found": False,
                    "reason": "timeout",
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }
            except ValueError as exc:
                return {
                    "type": extract_type,
                    "found": False,
                    "reason": "bad_image",
                    "error": str(exc),
                    "elapsed_ms": int((time.monotonic() - t0) * 1000),
                }
    finally:
        _ai_sema.release()


def _elapsed_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _pipeline_id(bgr, t0: float) -> Dict[str, Any]:
    """
    type=id:
      detect document region → crop (or full frame) → OCR → parse ID number
      If crop OCR fails to parse, retry full frame (common with phone + watermarks).
    """
    box = detect_id_region(bgr)
    if box is not None:
        roi = crop_with_padding(bgr, box, pad_ratio=0.08)
        detect_meta = {
            "detector": box.label,
            "confidence": round(box.confidence, 3),
            "box": [box.x1, box.y1, box.x2, box.y2],
        }
    else:
        roi = bgr
        detect_meta = {"detector": "full_frame", "confidence": None, "box": None}

    lines = run_ocr(roi)
    parsed = extract_id_number(lines) if lines else None

    # Crop can miss the IC line (glare / watermark / tight box) — try full frame
    if not parsed and box is not None:
        lines_full = run_ocr(bgr)
        parsed_full = extract_id_number(lines_full) if lines_full else None
        if parsed_full:
            lines = lines_full
            parsed = parsed_full
            detect_meta = {
                "detector": "full_frame_fallback",
                "confidence": None,
                "box": detect_meta.get("box"),
            }

    if not lines:
        return {
            "type": "id",
            "found": False,
            "reason": "no_id_document",
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
        }

    if not parsed:
        return {
            "type": "id",
            "found": False,
            "reason": "no_id_document",
            "detect": detect_meta,
            "ocr_preview": [t for t, _ in lines[:5]],
            "elapsed_ms": _elapsed_ms(t0),
        }

    id_number, conf, hint = parsed
    if conf < MIN_OCR_CONF:
        return {
            "type": "id",
            "found": False,
            "reason": "low_confidence",
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
        }

    return {
        "type": "id",
        "found": True,
        "id_number": id_number,
        "confidence": round(float(conf), 3),
        "document_hint": hint,
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
    }


def _pipeline_vehicle(bgr, t0: float) -> Dict[str, Any]:
    """
    type=vehicle:
      YOLO vehicle? → crop largest → OCR → parse plate
    """
    vehicles = detect_vehicles(bgr)
    if not vehicles:
        return {
            "type": "vehicle",
            "found": False,
            "reason": "no_vehicle",
            "elapsed_ms": _elapsed_ms(t0),
        }

    best = vehicles[0]
    roi = crop_with_padding(bgr, best, pad_ratio=0.05)
    h = roi.shape[0]
    bottom = roi[int(h * 0.45) :, :] if h > 40 else roi

    lines = run_ocr(bottom)
    if not lines:
        lines = run_ocr(roi)

    detect_meta = {
        "detector": "yolov8n",
        "label": best.label,
        "confidence": round(best.confidence, 3),
        "box": [best.x1, best.y1, best.x2, best.y2],
        "vehicle_count": len(vehicles),
    }

    if not lines:
        return {
            "type": "vehicle",
            "found": False,
            "reason": "no_vehicle",
            "detect": detect_meta,
            "detail": "vehicle_seen_but_no_text",
            "elapsed_ms": _elapsed_ms(t0),
        }

    parsed = extract_vehicle_number(lines)
    if not parsed:
        return {
            "type": "vehicle",
            "found": False,
            "reason": "no_vehicle",
            "detect": detect_meta,
            "detail": "vehicle_seen_but_no_plate",
            "ocr_preview": [t for t, _ in lines[:5]],
            "elapsed_ms": _elapsed_ms(t0),
        }

    plate, conf = parsed
    if conf < MIN_OCR_CONF:
        return {
            "type": "vehicle",
            "found": False,
            "reason": "low_confidence",
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
        }

    return {
        "type": "vehicle",
        "found": True,
        "vehicle_number": plate,
        "confidence": round(float(conf), 3),
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
    }
