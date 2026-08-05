"""
End-to-end RapidOCR pipeline for type=id | type=vehicle (v2 API).
"""

from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from typing import Any, Dict

from .detect import crop_with_padding, detect_id_region, detect_vehicles, warp_document_if_possible
from .parse import extract_id_number, extract_vehicle_number
from .preprocess import load_and_resize_image, pil_to_bgr_ndarray
from .rapid_ocr_engine import ensure_rapid_ocr_ready, run_rapid_ocr

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = float(os.environ.get("VISITOR_AI_TIMEOUT_SEC", "30"))
MIN_OCR_CONF = float(os.environ.get("VISITOR_AI_MIN_OCR_CONF", "0.45"))
MAX_CONCURRENT = int(os.environ.get("VISITOR_AI_MAX_CONCURRENT", "2"))
_ai_sema_v2 = threading.Semaphore(max(1, MAX_CONCURRENT))


def extract_from_upload_v2(file_obj, extract_type: str) -> Dict[str, Any]:
    """
    Public entry point for RapidOCR (v2 API).

    Returns a dict suitable for JSONResponse.
    """
    extract_type = (extract_type or "").strip().lower()
    if extract_type not in ("id", "vehicle"):
        return {
            "type": extract_type or None,
            "found": False,
            "reason": "invalid_type",
            "error": "type must be 'id' or 'vehicle'",
        }

    acquired = _ai_sema_v2.acquire(blocking=False)
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
        ensure_rapid_ocr_ready()

        def _run():
            img = load_and_resize_image(file_obj, max_side=1024)
            bgr = pil_to_bgr_ndarray(img)
            if extract_type == "id":
                return _pipeline_id_v2(bgr, t0)
            return _pipeline_vehicle_v2(bgr, t0)

        with ThreadPoolExecutor(max_workers=1) as pool:
            fut = pool.submit(_run)
            try:
                result = fut.result(timeout=timeout)
                return result
            except FuturesTimeout:
                logger.warning("Visitor RapidOCR v2 timed out after %ss type=%s", timeout, extract_type)
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
        _ai_sema_v2.release()


def _elapsed_ms(t0: float) -> int:
    return int((time.monotonic() - t0) * 1000)


def _crop_inset(bgr, inset_ratio: float = 0.06):
    h, w = bgr.shape[:2]
    dx = int(w * inset_ratio)
    dy = int(h * inset_ratio)
    if w - (2 * dx) < 40 or h - (2 * dy) < 40:
        return bgr
    return bgr[dy : h - dy, dx : w - dx].copy()


def _enhance_for_ocr(bgr):
    try:
        import cv2
    except ImportError:
        return bgr

    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    boosted = clahe.apply(gray)
    return cv2.cvtColor(boosted, cv2.COLOR_GRAY2BGR)


def _box_fill_ratio(box, bgr) -> float:
    h, w = bgr.shape[:2]
    frame_area = float(max(1, h * w))
    return max(0.0, min(1.0, box.area / frame_area))


def _should_reject_ambiguous_id(hint: str, conf: float, detect_meta: Dict[str, Any]) -> bool:
    detector = detect_meta.get("detector")
    fill_ratio = float(detect_meta.get("frame_fill_ratio") or 0.0)
    low_trust_hint = hint in {"labeled", "unknown"}

    if not low_trust_hint:
        return False

    if detector in {"full_frame", "full_frame_fallback", "full_frame_inset"} and conf < 0.75:
        return True

    if fill_ratio >= 0.88 and conf < 0.80:
        return True

    return False


def _id_attempt_rank(parsed, detect_meta: Dict[str, Any]) -> float:
    id_number, conf, hint = parsed
    detector = detect_meta.get("detector")
    trust_bonus = {
        "mykad": 18.0,
        "aadhaar": 16.0,
        "indian_dl": 14.0,
        "passport": 8.0,
        "labeled": -4.0,
        "unknown": -12.0,
    }
    detector_bonus = {
        "warped_document": 6.0,
        "document_region": 4.0,
        "id_document": 4.0,
        "full_frame_inset": 1.5,
        "full_frame_fallback": 0.0,
        "full_frame": -1.0,
        "enhanced_roi": 2.0,
        "enhanced_warped_document": 3.0,
    }
    score = (float(conf) * 100.0) + trust_bonus.get(hint, 0.0) + detector_bonus.get(detector, 0.0)
    if _should_reject_ambiguous_id(hint, conf, detect_meta):
        score -= 100.0
    if len(id_number) < 8:
        score -= 12.0
    return score


def _prepare_inset_variant(bgr):
    inset = _crop_inset(bgr, inset_ratio=0.06)
    if inset is bgr:
        return []
    return [
        (
            inset,
            {
                "detector": "full_frame_inset",
                "confidence": None,
                "box": None,
                "frame_fill_ratio": 0.88,
            },
        )
    ]


def _prepare_warp_variants(bgr, detect_meta: Dict[str, Any]):
    warped = warp_document_if_possible(bgr)
    if warped is None:
        return []
    return [
        (
            warped,
            {
                "detector": "warped_document",
                "confidence": None,
                "box": detect_meta.get("box"),
                "frame_fill_ratio": 0.72,
            },
        ),
        (
            _enhance_for_ocr(warped),
            {
                "detector": "enhanced_warped_document",
                "confidence": None,
                "box": detect_meta.get("box"),
                "frame_fill_ratio": 0.72,
            },
        ),
    ]


def _prepare_enhanced_roi_variant(roi, detect_meta: Dict[str, Any]):
    return [
        (
            _enhance_for_ocr(roi),
            {
                "detector": "enhanced_roi",
                "confidence": detect_meta.get("confidence"),
                "box": detect_meta.get("box"),
                "frame_fill_ratio": detect_meta.get("frame_fill_ratio"),
            },
        )
    ]


def _pipeline_id_v2(bgr, t0: float) -> Dict[str, Any]:
    box = detect_id_region(bgr)
    if box is not None:
        roi = crop_with_padding(bgr, box, pad_ratio=0.08)
        detect_meta = {
            "detector": box.label,
            "confidence": round(box.confidence, 3),
            "box": [box.x1, box.y1, box.x2, box.y2],
            "frame_fill_ratio": round(_box_fill_ratio(box, bgr), 3),
        }
    else:
        roi = bgr
        detect_meta = {
            "detector": "full_frame",
            "confidence": None,
            "box": None,
            "frame_fill_ratio": 1.0,
        }

    attempts = []

    def _add_attempt(image, meta):
        lines_local = run_rapid_ocr(image)
        parsed_local = extract_id_number(lines_local) if lines_local else None
        attempts.append((lines_local, parsed_local, meta))

    # Pass 1: Primary ROI
    _add_attempt(roi, detect_meta)
    _first_lines, first_parsed, first_meta = attempts[0]

    need_fallback_passes = (
        not first_parsed
        or _should_reject_ambiguous_id(first_parsed[2], first_parsed[1], first_meta)
        or first_parsed[1] < max(MIN_OCR_CONF + 0.1, 0.65)
    )

    if need_fallback_passes:
        if box is not None:
            _add_attempt(
                bgr,
                {
                    "detector": "full_frame_fallback",
                    "confidence": None,
                    "box": detect_meta.get("box"),
                    "frame_fill_ratio": 1.0,
                },
            )

        fallback_variants = []
        fallback_variants.extend(_prepare_inset_variant(bgr))
        fallback_variants.extend(_prepare_warp_variants(bgr, detect_meta))
        if box is not None:
            fallback_variants.extend(_prepare_enhanced_roi_variant(roi, detect_meta))

        for image, meta in fallback_variants:
            _add_attempt(image, meta)

    lines = next((lines_local for lines_local, _, _ in attempts if lines_local), [])
    parsed = None
    best_meta = detect_meta
    best_score = float("-inf")
    for lines_local, parsed_local, meta_local in attempts:
        if not lines_local:
            continue
        if parsed_local:
            score = _id_attempt_rank(parsed_local, meta_local)
            if score > best_score:
                best_score = score
                lines = lines_local
                parsed = parsed_local
                best_meta = meta_local
        elif not lines:
            lines = lines_local

    detect_meta = best_meta

    if not lines or not parsed:
        return {
            "type": "id",
            "found": False,
            "reason": "no_id_document",
            "detect": detect_meta,
            "ocr_preview": [t for t, _ in lines[:5]] if lines else [],
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    id_number, conf, hint = parsed
    if _should_reject_ambiguous_id(hint, conf, detect_meta) or conf < MIN_OCR_CONF:
        return {
            "type": "id",
            "found": False,
            "reason": "low_confidence",
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    return {
        "type": "id",
        "found": True,
        "id_number": id_number,
        "confidence": round(float(conf), 3),
        "document_hint": hint,
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
        "engine": "rapidocr",
    }


def _pipeline_vehicle_v2(bgr, t0: float) -> Dict[str, Any]:
    vehicles = detect_vehicles(bgr)
    if not vehicles:
        return {
            "type": "vehicle",
            "found": False,
            "reason": "no_vehicle",
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    best = vehicles[0]
    roi = crop_with_padding(bgr, best, pad_ratio=0.05)
    h = roi.shape[0]
    bottom = roi[int(h * 0.45) :, :] if h > 40 else roi

    lines = run_rapid_ocr(bottom)
    if not lines:
        lines = run_rapid_ocr(roi)

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
            "engine": "rapidocr",
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
            "engine": "rapidocr",
        }

    plate, conf = parsed
    if conf < MIN_OCR_CONF:
        return {
            "type": "vehicle",
            "found": False,
            "reason": "low_confidence",
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    return {
        "type": "vehicle",
        "found": True,
        "vehicle_number": plate,
        "confidence": round(float(conf), 3),
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
        "engine": "rapidocr",
    }
