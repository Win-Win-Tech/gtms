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
from .detect import (
    crop_with_padding,
    detect_id_region,
    detect_plates,
    detect_vehicles,
    warp_document_if_possible,
)
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
# Plate pipeline OCR budget (detect + enhance / vehicle-zone fallbacks).
MAX_PLATE_OCR_PASSES = int(os.environ.get("VISITOR_AI_MAX_PLATE_OCR_PASSES", "4"))
_VEHICLE_DETECT_LABELS = frozenset({"car", "truck", "bus", "motorcycle", "full_frame"})
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


def _slim_vehicle_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Stable shape for HTTP + CCTV ANPR Celery consumers."""
    number = result.get("vehicle_number") or result.get("number")
    detect = result.get("detect") or {}
    yolo_label = ""
    if isinstance(detect, dict):
        yolo_label = (detect.get("label") or "").strip().lower()
    return {
        "type": "vehicle",
        "found": bool(result.get("found")),
        "number": number,
        "vehicle_number": number,
        "confidence": result.get("confidence"),
        "reason": result.get("reason"),
        "raw_text": result.get("raw_text") or [],
        "detect": detect,
        "yolo_label": yolo_label,
        "vehicle_type": yolo_label,  # coarse COCO class; gate maps to lookup codes
        "elapsed_ms": result.get("elapsed_ms"),
        "engine": result.get("engine") or "plate+rapidocr",
        "error": result.get("error"),
    }


def extract_vehicle_from_bgr(bgr) -> Dict[str, Any]:
    """
    Plate YOLO + RapidOCR on an in-memory BGR frame (CCTV ANPR / library reuse).

    Same core path as upload extract-v2 type=vehicle. Synchronous — callers
    (HTTP timeout pool or Celery time limits) own concurrency/timeouts.
    """
    import numpy as np

    if bgr is None or not isinstance(bgr, np.ndarray) or bgr.size == 0:
        return _slim_vehicle_result({
            "found": False,
            "reason": "bad_image",
            "error": "empty frame",
        })

    t0 = time.monotonic()
    try:
        from .memory import touch_activity

        ensure_rapid_ocr_ready()
        touch_activity()

        h, w = bgr.shape[:2]
        max_side = max(h, w)
        frame = bgr
        if max_side > ID_MAX_SIDE:
            scale = ID_MAX_SIDE / float(max_side)
            frame = cv2.resize(
                bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        logger.info(
            "extract_vehicle_from_bgr resized=%sx%s",
            frame.shape[1],
            frame.shape[0],
        )
        result = _pipeline_vehicle_plate_v2(frame, t0)
        touch_activity()
        slim = _slim_vehicle_result(result)
        logger.info(
            "extract_vehicle_from_bgr done found=%s number=%s conf=%s reason=%s elapsed_ms=%s",
            slim.get("found"),
            slim.get("number"),
            slim.get("confidence"),
            slim.get("reason"),
            slim.get("elapsed_ms"),
        )
        return slim
    except Exception as exc:
        logger.exception("extract_vehicle_from_bgr failed: %s", exc)
        return _slim_vehicle_result({
            "found": False,
            "reason": "error",
            "error": str(exc),
            "elapsed_ms": int((time.monotonic() - t0) * 1000),
        })


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
            # Shared library with CCTV ANPR (already inside timeout pool)
            return extract_vehicle_from_bgr(bgr)

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
                result.get("id_number") or result.get("vehicle_number") or result.get("number"),
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


def _sharpen_for_blur(bgr):
    """Mild unsharp mask — helps soft focus / screen-photo blur without inventing glyphs."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return bgr
    blur = cv2.GaussianBlur(bgr, (0, 0), sigmaX=1.2)
    sharp = cv2.addWeighted(bgr, 1.45, blur, -0.45, 0)
    return np.clip(sharp, 0, 255).astype(bgr.dtype)


def _upscale_if_small(bgr, min_long_side: int = 220):
    """Upscale tiny plate crops (far vehicle / phone-screen capture) before OCR."""
    try:
        import cv2
    except ImportError:
        return bgr
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if longest >= min_long_side:
        return bgr
    scale = min_long_side / float(max(1, longest))
    return cv2.resize(
        bgr,
        (max(1, int(w * scale)), max(1, int(h * scale))),
        interpolation=cv2.INTER_CUBIC,
    )


def _is_vehicle_sized_box(box, frame) -> bool:
    """True when YOLO returned a vehicle (not a tight license-plate box)."""
    label = (getattr(box, "label", "") or "").lower()
    if label in _VEHICLE_DETECT_LABELS:
        return True
    try:
        return _box_fill_ratio(box, frame) >= 0.12
    except Exception:
        return False


def _plate_zone_crops(bgr, box) -> list:
    """
    Build OCR crops. For vehicle boxes, prefer lower bands (where plates sit).
    For real plate boxes, use the padded plate ROI.
    Returns list of (image, detector_name).
    """
    out = []
    if _is_vehicle_sized_box(box, bgr):
        roi = crop_with_padding(bgr, box, pad_ratio=0.04)
        h_roi = roi.shape[0]
        if h_roi > 40:
            # Primary: lower ~55% of vehicle (plate zone)
            out.append((roi[int(h_roi * 0.45) :, :], "vehicle_bottom"))
            # Tighter: lower ~40%
            out.append((roi[int(h_roi * 0.60) :, :], "vehicle_lower"))
        out.append((roi, "vehicle_full"))
        return out

    roi = crop_with_padding(bgr, box, pad_ratio=0.12)
    roi = _deskew_plate_roi(roi)
    out.append((roi, "plate_crop"))
    return out


def _pick_plate_from_attempts(attempts):
    """
    Choose plate from OCR passes: prefer consensus across passes, then conf+length.
    """
    scored = []
    for lines_local, parsed_local, meta_local in attempts:
        if not parsed_local:
            continue
        plate, conf = parsed_local
        plate = (plate or "").upper().strip()
        if len(plate) < 4:
            continue
        scored.append((plate, float(conf), meta_local, lines_local))
    if not scored:
        return None, float("-inf"), None, None

    # Vote by exact plate string
    from collections import Counter

    counts = Counter(p for p, _, _, _ in scored)
    best_plate = None
    best_score = float("-inf")
    best_meta = None
    best_lines = None
    best_conf = float("-inf")
    for plate, conf, meta, lines in scored:
        # consensus bonus + prefer longer (less truncated OCR)
        vote = counts[plate]
        score = conf + 0.12 * (vote - 1) + 0.015 * len(plate)
        if score > best_score:
            best_score = score
            best_plate = plate
            best_meta = meta
            best_lines = lines
            best_conf = conf
    return best_plate, best_conf, best_meta, best_lines


def _deskew_plate_roi(bgr):
    """
    Light deskew for tilted plates (minAreaRect). Returns original on failure.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return bgr
    h, w = bgr.shape[:2]
    if h < 20 or w < 40:
        return bgr
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]
    coords = np.column_stack(np.where(thr > 0))
    if coords.size < 50:
        return bgr
    angle = cv2.minAreaRect(coords)[-1]
    if angle < -45:
        angle = 90 + angle
    if abs(angle) < 1.5 or abs(angle) > 25:
        return bgr
    m = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), angle, 1.0)
    return cv2.warpAffine(
        bgr, m, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )


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


def _pipeline_vehicle_plate_v2(bgr, t0: float) -> Dict[str, Any]:
    """
    extract-v2 vehicle path: dedicated plate YOLO + RapidOCR.

    Handles (same spirit as type=id):
      - full vehicle with plate in frame
      - close-up plate only
      - soft blur / low contrast (CLAHE + mild sharpen)
      - tilt / skew (deskew + 90° rotate when portrait)
      - photo of another device / screen (inset + enhance + upscale tiny crop)
      - perspective-ish document warp fallback

    Does NOT call / modify `_pipeline_vehicle_v2` (legacy vehicle-COCO path).
    """
    h, w = bgr.shape[:2]
    logger.info("vehicle-plate-v2 start frame=%sx%s", w, h)

    ocr_passes = 0
    attempts = []

    def _ocr_attempt(image, meta: Dict[str, Any]) -> bool:
        """Run RapidOCR once. Returns True when a confident plate is found."""
        nonlocal ocr_passes
        if ocr_passes >= MAX_PLATE_OCR_PASSES:
            return False
        ocr_passes += 1
        prepared = _upscale_if_small(image, min_long_side=280)
        lines_local = run_rapid_ocr(prepared)
        parsed_local = extract_vehicle_number(lines_local) if lines_local else None
        attempts.append((lines_local, parsed_local, meta))
        logger.info(
            "vehicle-plate-v2 ocr pass=%s detector=%s lines=%s parsed=%s conf=%s "
            "elapsed_ms=%s texts=%s",
            ocr_passes,
            meta.get("detector"),
            len(lines_local or []),
            parsed_local[0] if parsed_local else None,
            round(float(parsed_local[1]), 3) if parsed_local else None,
            _elapsed_ms(t0),
            _ocr_lines_preview(lines_local),
        )
        return bool(parsed_local and parsed_local[1] >= MIN_OCR_CONF)

    def _detect_on(frame):
        try:
            return detect_plates(frame)
        except Exception as exc:
            logger.warning("vehicle-plate-v2 detect_plates failed: %s", exc)
            return []

    plates = _detect_on(bgr)
    detect_meta = {
        "detector": "plate_yolo",
        "plate_count": len(plates),
        "engine": "plate+rapidocr",
    }
    logger.info(
        "vehicle-plate-v2 yolo count=%s elapsed_ms=%s boxes=%s",
        len(plates),
        _elapsed_ms(t0),
        [
            {
                "label": p.label,
                "conf": round(p.confidence, 3),
                "box": [p.x1, p.y1, p.x2, p.y2],
                "area": p.area,
            }
            for p in plates[:3]
        ],
    )

    # Portrait phone shot of a landscape plate — rotate then re-detect (one shot)
    if not plates and h > w * 1.05:
        try:
            rotated = cv2.rotate(bgr, cv2.ROTATE_90_CLOCKWISE)
            plates_r = _detect_on(rotated)
            if plates_r:
                bgr = rotated
                h, w = bgr.shape[:2]
                plates = plates_r
                detect_meta["rotated"] = "90_cw_before_detect"
                logger.info(
                    "vehicle-plate-v2 rotated_detect count=%s elapsed_ms=%s",
                    len(plates),
                    _elapsed_ms(t0),
                )
        except Exception as exc:
            logger.debug("vehicle-plate-v2 rotate detect skipped: %s", exc)

    hit = False
    if plates:
        best = plates[0]
        detect_meta.update({
            "label": best.label,
            "confidence": round(best.confidence, 3),
            "box": [best.x1, best.y1, best.x2, best.y2],
            "frame_fill_ratio": round(_box_fill_ratio(best, bgr), 3),
            "vehicle_sized": _is_vehicle_sized_box(best, bgr),
        })

        # Vehicle YOLO fallback returns a car box — OCR lower plate zone, not whole car.
        # Real plate YOLO returns a tight plate box — OCR that crop.
        for crop_img, det_name in _plate_zone_crops(bgr, best):
            if ocr_passes >= MAX_PLATE_OCR_PASSES:
                break
            hit = _ocr_attempt(
                crop_img,
                {**detect_meta, "detector": det_name},
            ) or hit
            if ocr_passes >= MAX_PLATE_OCR_PASSES:
                break
            # Second pass: CLAHE + sharpen on same crop (blur / glare)
            enhanced = _sharpen_for_blur(_enhance_for_ocr(crop_img))
            hit = _ocr_attempt(
                enhanced,
                {**detect_meta, "detector": f"{det_name}_enhanced"},
            ) or hit
            # Enough signal from primary zone — stop burning passes on worse crops
            if hit and det_name in ("vehicle_bottom", "plate_crop"):
                break

        # Perspective warp only if still no confident hit
        if not hit and ocr_passes < MAX_PLATE_OCR_PASSES:
            zone = _plate_zone_crops(bgr, best)
            base = zone[0][0] if zone else crop_with_padding(bgr, best, pad_ratio=0.08)
            warped = warp_document_if_possible(base)
            if warped is None:
                warped = warp_document_if_possible(bgr)
            if warped is not None:
                hit = _ocr_attempt(
                    _enhance_for_ocr(warped),
                    {**detect_meta, "detector": "plate_warped"},
                )

    # No plate box (or crop OCR miss): ID-style fallbacks for close-up / screen capture
    if not hit and ocr_passes < MAX_PLATE_OCR_PASSES:
        inset_list = _prepare_inset_variant(bgr)
        if inset_list:
            inset_img, inset_meta = inset_list[0]
            hit = _ocr_attempt(
                _sharpen_for_blur(_enhance_for_ocr(inset_img)),
                {
                    **detect_meta,
                    **inset_meta,
                    "detector": "screen_inset_enhanced",
                    "fallback": True,
                },
            )

    if not hit and ocr_passes < MAX_PLATE_OCR_PASSES:
        hit = _ocr_attempt(
            _sharpen_for_blur(_enhance_for_ocr(bgr)),
            {
                **detect_meta,
                "detector": "full_frame_enhanced",
                "fallback": True,
                "frame_fill_ratio": 1.0,
            },
        )

    best_candidate, best_conf, best_meta, best_lines = _pick_plate_from_attempts(
        attempts
    )
    if best_meta is None:
        best_meta = detect_meta

    if isinstance(best_meta, dict):
        best_meta = {**best_meta, "ocr_passes": ocr_passes}

    if best_candidate and best_conf >= MIN_OCR_CONF:
        logger.info(
            "vehicle-plate-v2 hit plate=%s conf=%.3f detector=%s",
            best_candidate,
            best_conf,
            best_meta.get("detector"),
        )
        return {
            "type": "vehicle",
            "found": True,
            "vehicle_number": best_candidate,
            "confidence": round(float(best_conf), 3),
            "raw_text": [t for t, _ in (best_lines or [])],
            "detect": best_meta,
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "plate+rapidocr",
        }

    # Soft accept (same idea as legacy low_conf_fallback)
    if best_candidate and best_conf >= 0.35:
        logger.info(
            "vehicle-plate-v2 hit=low_conf plate=%s conf=%.3f min_strict=%.2f",
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
            "detect": best_meta,
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "plate+rapidocr",
        }

    reason = "no_plate_found" if plates else "no_plate_detected"
    logger.warning(
        "vehicle-plate-v2 miss reason=%s best=%s conf=%s plate_count=%s ocr_passes=%s",
        reason,
        best_candidate,
        None if best_conf == float("-inf") else round(float(best_conf), 3),
        len(plates),
        ocr_passes,
    )
    return {
        "type": "vehicle",
        "found": False,
        "reason": reason,
        "raw_text": [t for t, _ in (best_lines or [])],
        "detect": best_meta,
        "elapsed_ms": _elapsed_ms(t0),
        "engine": "plate+rapidocr",
    }


def _pipeline_vehicle_v2(bgr, t0: float) -> Dict[str, Any]:
    """
    YOLO vehicle box first → OCR only that crop (plate usually in lower half).
    Full-frame OCR is a last resort — OCRing the whole frame is slower/noisier.

    Legacy path — extract-v2 now uses `_pipeline_vehicle_plate_v2` instead.
    Kept intact for reference / optional fallback callers.
    """
    h, w = bgr.shape[:2]
    logger.info("vehicle-v2 start frame=%sx%s", w, h)

    vehicles = detect_vehicles(bgr)
    detect_meta = {"detector": "yolov8n", "vehicle_count": len(vehicles)}
    logger.info(
        "vehicle-v2 yolo count=%s elapsed_ms=%s boxes=%s",
        len(vehicles),
        _elapsed_ms(t0),
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

    attempts = []

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

        # Prefer lower half of vehicle (plate zone) — smaller image → faster OCR
        lines_bottom = run_rapid_ocr(bottom_roi)
        parsed_bottom = extract_vehicle_number(lines_bottom) if lines_bottom else None
        logger.info(
            "vehicle-v2 yolo_bottom ocr_lines=%s parsed=%s conf=%s elapsed_ms=%s texts=%s",
            len(lines_bottom or []),
            parsed_bottom[0] if parsed_bottom else None,
            round(float(parsed_bottom[1]), 3) if parsed_bottom else None,
            _elapsed_ms(t0),
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

        # Same vehicle box, full ROI (plate not only in bottom half)
        lines_roi = run_rapid_ocr(roi)
        parsed_roi = extract_vehicle_number(lines_roi) if lines_roi else None
        logger.info(
            "vehicle-v2 yolo_roi ocr_lines=%s parsed=%s conf=%s elapsed_ms=%s texts=%s",
            len(lines_roi or []),
            parsed_roi[0] if parsed_roi else None,
            round(float(parsed_roi[1]), 3) if parsed_roi else None,
            _elapsed_ms(t0),
            _ocr_lines_preview(lines_roi),
        )
        if parsed_roi and parsed_roi[1] >= MIN_OCR_CONF:
            logger.info(
                "vehicle-v2 hit=yolo_roi plate=%s conf=%.3f",
                parsed_roi[0],
                parsed_roi[1],
            )
            return {
                "type": "vehicle",
                "found": True,
                "vehicle_number": parsed_roi[0],
                "confidence": round(float(parsed_roi[1]), 3),
                "raw_text": [t for t, _ in lines_roi],
                "detect": {**detect_meta, "ocr_region": "full_roi"},
                "elapsed_ms": _elapsed_ms(t0),
                "engine": "rapidocr",
            }
        attempts.append((lines_roi, parsed_roi))

    # Fallback only when YOLO miss / crop OCR miss — avoid doing this first
    inset = _crop_inset(bgr, inset_ratio=0.06)
    lines_i = run_rapid_ocr(inset)
    parsed_i = extract_vehicle_number(lines_i) if lines_i else None
    logger.info(
        "vehicle-v2 inset_fallback ocr_lines=%s parsed=%s conf=%s elapsed_ms=%s texts=%s",
        len(lines_i or []),
        parsed_i[0] if parsed_i else None,
        round(float(parsed_i[1]), 3) if parsed_i else None,
        _elapsed_ms(t0),
        _ocr_lines_preview(lines_i),
    )
    if parsed_i and parsed_i[1] >= MIN_OCR_CONF:
        logger.info("vehicle-v2 hit=inset_fallback plate=%s conf=%.3f", parsed_i[0], parsed_i[1])
        return {
            "type": "vehicle",
            "found": True,
            "vehicle_number": parsed_i[0],
            "confidence": round(float(parsed_i[1]), 3),
            "raw_text": [t for t, _ in lines_i],
            "detect": {
                **detect_meta,
                "detector": detect_meta.get("detector", "yolov8n"),
                "fallback": "full_frame_inset",
            },
            "elapsed_ms": _elapsed_ms(t0),
            "engine": "rapidocr",
        }
    attempts.append((lines_i, parsed_i))

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
        "raw_text": [t for t, _ in (best_lines or lines_i or [])],
        "detect": detect_meta,
        "elapsed_ms": _elapsed_ms(t0),
        "engine": "rapidocr",
    }
