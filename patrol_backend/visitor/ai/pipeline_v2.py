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

import cv2
from .detect import crop_with_padding, detect_id_region, detect_vehicles, warp_document_if_possible
from .parse import extract_id_name, extract_id_number, extract_vehicle_number
from .preprocess import load_and_resize_image, pil_to_bgr_ndarray
from .rapid_ocr_engine import ensure_rapid_ocr_ready, run_rapid_ocr

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = float(os.environ.get("VISITOR_AI_TIMEOUT_SEC", "25"))
MIN_OCR_CONF = float(os.environ.get("VISITOR_AI_MIN_OCR_CONF", "0.45"))
MAX_CONCURRENT = int(os.environ.get("VISITOR_AI_MAX_CONCURRENT", "2"))
# Pre-resize longest side before OCR (smaller = faster on CPU).
ID_MAX_SIDE = int(os.environ.get("VISITOR_AI_ID_MAX_SIDE", "800"))
# Hard cap on RapidOCR calls per ID request (each ~1–3s on CPU).
MAX_ID_OCR_PASSES = int(os.environ.get("VISITOR_AI_MAX_ID_OCR_PASSES", "2"))
_ai_sema_v2 = threading.Semaphore(max(1, MAX_CONCURRENT))
# Reuse one worker pool instead of creating/destroying per request.
_timeout_pool = ThreadPoolExecutor(max_workers=max(1, MAX_CONCURRENT))


def _ocr_lines_preview(lines, limit: int = 20) -> str:
    """Compact OCR dump for logs (text + confidence)."""
    if not lines:
        return "[]"
    parts = []
    for text, conf in lines[:limit]:
        parts.append(f"{text!r}:{float(conf):.2f}")
    extra = f" …(+{len(lines) - limit})" if len(lines) > limit else ""
    return "[" + "; ".join(parts) + "]" + extra


def extract_from_upload_v2(file_obj, extract_type: str) -> Dict[str, Any]:
    """
    Public entry point for RapidOCR (v2 API).

    Returns a dict suitable for JSONResponse.
    """
    extract_type = (extract_type or "").strip().lower()
    if extract_type not in ("id", "vehicle"):
        logger.info("extract-v2 invalid_type=%s", extract_type)
        return {
            "type": extract_type or None,
            "found": False,
            "reason": "invalid_type",
            "error": "type must be 'id' or 'vehicle'",
        }

    acquired = _ai_sema_v2.acquire(blocking=False)
    if not acquired:
        logger.warning("extract-v2 busy type=%s", extract_type)
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

        ensure_rapid_ocr_ready()
        touch_activity()

        def _run():
            img = load_and_resize_image(file_obj, max_side=ID_MAX_SIDE)
            bgr = pil_to_bgr_ndarray(img)
            h, w = bgr.shape[:2]
            logger.info(
                "extract-v2 image type=%s resized=%sx%s max_side=%s elapsed_ms=%s",
                extract_type,
                w,
                h,
                ID_MAX_SIDE,
                _elapsed_ms(t0),
            )
            if extract_type == "id":
                return _pipeline_id_v2(bgr, t0)
            return _pipeline_vehicle_v2(bgr, t0)

        fut = _timeout_pool.submit(_run)
        try:
            result = fut.result(timeout=timeout)
            touch_activity()
            logger.info(
                "extract-v2 done type=%s found=%s reason=%s conf=%s number=%s name=%s elapsed_ms=%s",
                extract_type,
                result.get("found"),
                result.get("reason"),
                result.get("confidence"),
                result.get("id_number") or result.get("vehicle_number"),
                result.get("name"),
                result.get("elapsed_ms"),
            )
            return result
        except FuturesTimeout:
            logger.warning("Visitor RapidOCR v2 timed out after %ss type=%s", timeout, extract_type)
            touch_activity()
            return {
                "type": extract_type,
                "found": False,
                "reason": "timeout",
                "elapsed_ms": int((time.monotonic() - t0) * 1000),
            }
        except ValueError as exc:
            logger.warning("extract-v2 bad_image type=%s err=%s", extract_type, exc)
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


def _prepare_rotation_variants(bgr):
    # Only rotate if image is vertical (height > width)
    if bgr.shape[0] <= bgr.shape[1]:
        return []
    try:
        r90 = cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
        r270 = cv2.rotate(bgr, cv2.ROTATE_90_COUNTERCLOCKWISE)
        return [
            (r90, {"detector": "rotated_90_cw", "confidence": None, "box": None, "frame_fill_ratio": 1.0}),
            (r270, {"detector": "rotated_90_ccw", "confidence": None, "box": None, "frame_fill_ratio": 1.0}),
        ]
    except Exception:
        return []


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
    ocr_passes = 0

    def _add_attempt(image, meta) -> bool:
        """Run OCR once. Returns True if we should stop (got id+name)."""
        nonlocal ocr_passes
        if ocr_passes >= MAX_ID_OCR_PASSES:
            return True
        ocr_passes += 1
        lines_local = run_rapid_ocr(image)
        parsed_local = extract_id_number(lines_local) if lines_local else None
        name_local = (
            extract_id_name(lines_local, parsed_local[2])
            if (lines_local and parsed_local)
            else None
        )
        attempts.append((lines_local, parsed_local, name_local, meta))
        return bool(parsed_local and name_local and parsed_local[1] >= MIN_OCR_CONF)

    # Pass 1: primary ROI (or full frame)
    done = _add_attempt(roi, detect_meta)
    _first_lines, first_parsed, first_name, first_meta = attempts[0]

    # Good enough on first pass — skip expensive fallbacks
    if done or (
        first_parsed
        and first_name
        and not _should_reject_ambiguous_id(first_parsed[2], first_parsed[1], first_meta)
        and first_parsed[1] >= MIN_OCR_CONF
    ):
        pass
    elif ocr_passes < MAX_ID_OCR_PASSES:
        # Pass 2 only: pick the single most useful fallback (not a chain of 5+)
        fallback = None
        # Vertical / phone photos of landscape cards → rotate first
        if bgr.shape[0] > bgr.shape[1] * 1.05:
            try:
                fallback = (
                    cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE),
                    {
                        "detector": "rotated_90_cw",
                        "confidence": None,
                        "box": None,
                        "frame_fill_ratio": 1.0,
                    },
                )
            except Exception:
                fallback = None
        if fallback is None and box is not None:
            fallback = (
                bgr,
                {
                    "detector": "full_frame_fallback",
                    "confidence": None,
                    "box": detect_meta.get("box"),
                    "frame_fill_ratio": 1.0,
                },
            )
        if fallback is None:
            inset_list = _prepare_inset_variant(bgr)
            if inset_list:
                fallback = inset_list[0]
        if fallback is not None:
            _add_attempt(fallback[0], fallback[1])

    lines = next((lines_local for lines_local, _, _, _ in attempts if lines_local), [])
    parsed = None
    extracted_name = None
    best_meta = detect_meta
    best_score = float("-inf")

    for lines_local, parsed_local, name_local, meta_local in attempts:
        if not lines_local:
            continue
        if parsed_local:
            score = _id_attempt_rank(parsed_local, meta_local) + (0.25 if name_local else 0.0)
            if score > best_score:
                best_score = score
                lines = lines_local
                parsed = parsed_local
                extracted_name = name_local
                best_meta = meta_local
        elif not lines:
            lines = lines_local

    detect_meta = best_meta
    if isinstance(detect_meta, dict):
        detect_meta = {**detect_meta, "ocr_passes": ocr_passes}

    if not lines or not parsed:
        return {
            "type": "id",
            "found": False,
            "reason": "no_id_document",
            "detect": detect_meta,
            "ocr_preview": [t for t, _ in lines[:5]] if lines else [],
            "raw_text": [t for t, _ in lines] if lines else [],
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
            "raw_text": [t for t, _ in lines] if lines else [],
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    if not extracted_name and lines:
        extracted_name = extract_id_name(lines, hint)

    return {
        "type": "id",
        "found": True,
        "id_number": id_number,
        "name": extracted_name,
        "confidence": round(float(conf), 3),
        "document_hint": hint,
        "raw_text": [t for t, _ in lines],
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
        "engine": "rapidocr",
    }


def _pipeline_vehicle_v2(bgr, t0: float) -> Dict[str, Any]:
    # Fast path: most visitor plate photos are close-ups — OCR full frame first
    # and skip YOLO unless needed (YOLO load/infer is costly on CPU).
    h, w = bgr.shape[:2]
    detect_meta = {"detector": "full_frame_fast"}
    logger.info("vehicle-v2 start frame=%sx%s", w, h)

    lines = run_rapid_ocr(bgr)
    parsed = extract_vehicle_number(lines) if lines else None
    logger.info(
        "vehicle-v2 full_frame ocr_lines=%s parsed=%s conf=%s texts=%s",
        len(lines or []),
        parsed[0] if parsed else None,
        round(float(parsed[1]), 3) if parsed else None,
        _ocr_lines_preview(lines),
    )
    if parsed and parsed[1] >= MIN_OCR_CONF:
        logger.info("vehicle-v2 hit=full_frame plate=%s conf=%.3f", parsed[0], parsed[1])
        return {
            "type": "vehicle",
            "found": True,
            "vehicle_number": parsed[0],
            "confidence": round(float(parsed[1]), 3),
            "raw_text": [t for t, _ in lines],
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    attempts = [(lines, parsed)]

    # One more cheap crop before YOLO
    inset = _crop_inset(bgr, inset_ratio=0.06)
    if inset is not bgr:
        lines_i = run_rapid_ocr(inset)
        parsed_i = extract_vehicle_number(lines_i) if lines_i else None
        logger.info(
            "vehicle-v2 inset ocr_lines=%s parsed=%s conf=%s texts=%s",
            len(lines_i or []),
            parsed_i[0] if parsed_i else None,
            round(float(parsed_i[1]), 3) if parsed_i else None,
            _ocr_lines_preview(lines_i),
        )
        if parsed_i and parsed_i[1] >= MIN_OCR_CONF:
            logger.info("vehicle-v2 hit=inset plate=%s conf=%.3f", parsed_i[0], parsed_i[1])
            return {
                "type": "vehicle",
                "found": True,
                "vehicle_number": parsed_i[0],
                "confidence": round(float(parsed_i[1]), 3),
                "raw_text": [t for t, _ in lines_i],
                "detect": {"detector": "full_frame_inset"},
                "elapsed_ms": _elapsed_ms(t0),
                "engine": "rapidocr",
            }
        attempts.append((lines_i, parsed_i))

    vehicles = detect_vehicles(bgr)
    detect_meta = {"detector": "yolov8n", "vehicle_count": len(vehicles)}
    logger.info(
        "vehicle-v2 yolo count=%s boxes=%s",
        len(vehicles),
        [
            {
                "label": v.label,
                "conf": round(v.confidence, 3),
                "box": [v.x1, v.y1, v.x2, v.y2],
                "area": v.area,
            }
            for v in vehicles[:3]
        ],
    )

    if vehicles:
        best = vehicles[0]
        detect_meta.update({
            "label": best.label,
            "confidence": round(best.confidence, 3),
            "box": [best.x1, best.y1, best.x2, best.y2],
        })
        roi = crop_with_padding(bgr, best, pad_ratio=0.05)
        h_roi = roi.shape[0]
        bottom_roi = roi[int(h_roi * 0.45):, :] if h_roi > 40 else roi

        lines_bottom = run_rapid_ocr(bottom_roi)
        parsed_bottom = extract_vehicle_number(lines_bottom) if lines_bottom else None
        logger.info(
            "vehicle-v2 yolo_bottom ocr_lines=%s parsed=%s conf=%s texts=%s",
            len(lines_bottom or []),
            parsed_bottom[0] if parsed_bottom else None,
            round(float(parsed_bottom[1]), 3) if parsed_bottom else None,
            _ocr_lines_preview(lines_bottom),
        )
        if parsed_bottom and parsed_bottom[1] >= MIN_OCR_CONF:
            logger.info(
                "vehicle-v2 hit=yolo_bottom plate=%s conf=%.3f",
                parsed_bottom[0],
                parsed_bottom[1],
            )
            return {
                "type": "vehicle",
                "found": True,
                "vehicle_number": parsed_bottom[0],
                "confidence": round(float(parsed_bottom[1]), 3),
                "raw_text": [t for t, _ in lines_bottom],
                "detect": detect_meta,
                "elapsed_ms": _elapsed_ms(t0),
                "engine": "rapidocr",
            }
        attempts.append((lines_bottom, parsed_bottom))

    best_candidate = None
    best_conf = float("-inf")
    best_lines = None
    for att_lines, att_parsed in attempts:
        if att_parsed and att_parsed[1] > best_conf:
            best_conf = att_parsed[1]
            best_candidate = att_parsed[0]
            best_lines = att_lines

    if best_candidate and best_conf >= 0.35:
        logger.info(
            "vehicle-v2 hit=low_conf_fallback plate=%s conf=%.3f min_strict=%.2f",
            best_candidate,
            best_conf,
            MIN_OCR_CONF,
        )
        return {
            "type": "vehicle",
            "found": True,
            "vehicle_number": best_candidate,
            "confidence": round(float(best_conf), 3),
            "raw_text": [t for t, _ in (best_lines or [])],
            "detect": detect_meta,
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }

    reason = "no_vehicle" if not vehicles else "no_plate_found"
    logger.warning(
        "vehicle-v2 miss reason=%s best_candidate=%s best_conf=%s yolo_count=%s attempts=%s",
        reason,
        best_candidate,
        None if best_conf == float("-inf") else round(float(best_conf), 3),
        len(vehicles),
        [
            {
                "parsed": p[0] if p else None,
                "conf": round(float(p[1]), 3) if p else None,
                "ocr_n": len(ls or []),
            }
            for ls, p in attempts
        ],
    )
    return {
        "type": "vehicle",
        "found": False,
        "reason": reason,
        "raw_text": [t for t, _ in (lines or [])],
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
        "engine": "rapidocr",
    }
