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
from .plate_enhance import (
    enhance_plate,
    is_two_line_shape,
    plate_color,
    stitch_two_line,
    upscale_plate,
)
from .preprocess import load_and_resize_image, pil_to_bgr_ndarray
from .rapid_ocr_engine import (
    ensure_rapid_ocr_ready,
    run_rapid_ocr,
    run_rapid_ocr_detailed,
)

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SEC = float(os.environ.get("VISITOR_AI_TIMEOUT_SEC", "25"))
MIN_OCR_CONF = float(os.environ.get("VISITOR_AI_MIN_OCR_CONF", "0.45"))
MAX_CONCURRENT = int(os.environ.get("VISITOR_AI_MAX_CONCURRENT", "2"))
# Pre-resize longest side before OCR (smaller = faster on CPU).
ID_MAX_SIDE = int(os.environ.get("VISITOR_AI_ID_MAX_SIDE", "800"))
# Plate YOLO detect size (OCR still uses full-resolution crops when available).
PLATE_DETECT_MAX_SIDE = int(os.environ.get("VISITOR_AI_PLATE_DETECT_MAX_SIDE", "960"))
# Upscale distant plate crops so RapidOCR can read glyphs.
PLATE_OCR_MIN_LONG_SIDE = int(os.environ.get("VISITOR_AI_PLATE_OCR_MIN_LONG_SIDE", "480"))
# Hard cap on RapidOCR calls per ID request (each ~1–3s on CPU).
MAX_ID_OCR_PASSES = int(os.environ.get("VISITOR_AI_MAX_ID_OCR_PASSES", "2"))
# Plate pipeline OCR budget (detect + enhance / vehicle-zone fallbacks).
MAX_PLATE_OCR_PASSES = int(os.environ.get("VISITOR_AI_MAX_PLATE_OCR_PASSES", "5"))
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


def extract_vehicle_from_bgr(
    bgr,
    hint_meta: Dict[str, Any] | None = None,
    *,
    max_passes: int | None = None,
) -> Dict[str, Any]:
    """
    Plate YOLO + RapidOCR on an in-memory BGR frame (CCTV ANPR / library reuse).

    Detect may run on a downscaled copy for speed; OCR crops are taken from the
    original full-resolution frame so distant CCTV plates stay readable.

    hint_meta: ANPR reader metadata (zoom_crop, box_norm, zoom_rect). When given,
    the call is treated as CCTV: whole-frame OCR fallbacks are skipped.
    max_passes: optional lower OCR budget (extra frames of the same vehicle).
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
        detect_frame = bgr
        box_mul = 1.0
        detect_max = max(320, PLATE_DETECT_MAX_SIDE)
        if max_side > detect_max:
            scale = detect_max / float(max_side)
            detect_frame = cv2.resize(
                bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
            # Map detect-frame box coords → full-res OCR frame (width ratio)
            box_mul = float(w) / float(detect_frame.shape[1])
        logger.info(
            "extract_vehicle_from_bgr detect=%sx%s ocr_source=%sx%s box_mul=%.3f zoom=%s",
            detect_frame.shape[1],
            detect_frame.shape[0],
            w,
            h,
            box_mul,
            bool((hint_meta or {}).get("zoom_crop")),
        )
        result = _pipeline_vehicle_plate_v2(
            detect_frame,
            t0,
            ocr_bgr=bgr,
            box_mul=box_mul,
            hint_meta=hint_meta,
            max_passes=max_passes,
        )
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


def _is_vehicle_sized_box(box, frame) -> bool:
    """True when YOLO returned a vehicle (not a tight license-plate box)."""
    label = (getattr(box, "label", "") or "").lower()
    if label in _VEHICLE_DETECT_LABELS:
        return True
    try:
        return _box_fill_ratio(box, frame) >= 0.12
    except Exception:
        return False


def _map_box(box, mul: float):
    """Scale a detect-frame Box into OCR-frame pixel coordinates."""
    from visitor.ai.detect import Box

    if mul == 1.0:
        return box
    return Box(
        x1=int(round(box.x1 * mul)),
        y1=int(round(box.y1 * mul)),
        x2=int(round(box.x2 * mul)),
        y2=int(round(box.y2 * mul)),
        confidence=float(box.confidence),
        label=box.label,
    )


def _crop_min_context(bgr, box, *, min_w: int, min_h: int, pad_ratio: float = 0.35):
    """
    Crop around a plate box ensuring a minimum context size (distant CCTV).
    pad_ratio expands relative to the box; min_w/min_h force readable OCR size.
    Returns (crop, plate_rect) where plate_rect is the box in crop pixels.
    """
    h, w = bgr.shape[:2]
    bw = max(1, box.x2 - box.x1)
    bh = max(1, box.y2 - box.y1)
    pad_x = max(int(bw * pad_ratio), (min_w - bw) // 2, 8)
    pad_y = max(int(bh * pad_ratio), (min_h - bh) // 2, 8)
    x1 = max(0, box.x1 - pad_x)
    y1 = max(0, box.y1 - pad_y)
    x2 = min(w, box.x2 + pad_x)
    y2 = min(h, box.y2 + pad_y)
    if x2 <= x1 or y2 <= y1:
        return bgr.copy(), (box.x1, box.y1, box.x2, box.y2)
    rect = (box.x1 - x1, box.y1 - y1, box.x2 - x1, box.y2 - y1)
    return bgr[y1:y2, x1:x2].copy(), rect


def _plate_zone_crops(bgr, box) -> list:
    """
    Build OCR crops. For vehicle boxes, prefer lower bands (where plates sit).
    For real plate boxes: tight crop, then wider context for distant CCTV.
    Returns list of (image, detector_name, plate_rect_in_crop_or_None).
    """
    out = []
    if _is_vehicle_sized_box(box, bgr):
        roi = crop_with_padding(bgr, box, pad_ratio=0.04)
        h_roi = roi.shape[0]
        if h_roi > 40:
            # Primary: lower ~55% of vehicle (plate zone)
            out.append((roi[int(h_roi * 0.45) :, :], "vehicle_bottom", None))
            # Tighter: lower ~40%
            out.append((roi[int(h_roi * 0.60) :, :], "vehicle_lower", None))
        out.append((roi, "vehicle_full", None))
        return out

    fill = _box_fill_ratio(box, bgr)
    bw = max(1, box.x2 - box.x1)
    bh = max(1, box.y2 - box.y1)
    tiny = fill < 0.008 or max(bw, bh) < 64

    # Tight plate crop (still padded enough for deskew)
    tight, tight_rect = _crop_min_context(
        bgr, box, min_w=120 if tiny else 80, min_h=64 if tiny else 40, pad_ratio=0.45
    )
    tight = _deskew_plate_roi(tight)
    out.append((tight, "plate_crop", tight_rect))

    # Wider context around the same plate (bike rear / car bumper)
    wide, wide_rect = _crop_min_context(
        bgr, box, min_w=280 if tiny else 160, min_h=140 if tiny else 80, pad_ratio=1.2
    )
    out.append((wide, "plate_wide", wide_rect))

    if tiny:
        # Even larger band for far scooters (still plate-centered, not full frame)
        ctx, ctx_rect = _crop_min_context(
            bgr, box, min_w=420, min_h=220, pad_ratio=2.5
        )
        out.append((ctx, "plate_context", ctx_rect))
    return out


def _plate_rows(detailed, rect=None) -> list:
    """
    OCR pieces -> plate text rows.

    Keeps only pieces whose centre lies inside the plate box (slightly expanded),
    so painted body text, phone numbers and neighbouring vehicles are dropped.
    Pieces on the same row are joined left-to-right ("TN" "59" "BP" "9717" ->
    "TN 59 BP 9717"); rows stay top-to-bottom for two-line plates.
    Returns (rows, pieces_in_plate) as lists of (text, conf).
    """
    items = list(detailed or [])
    if rect is not None:
        x1, y1, x2, y2 = rect
        ex = (x2 - x1) * 0.15 + 4
        ey = (y2 - y1) * 0.35 + 4
        kept = []
        for text, score, (a, b, c, d) in items:
            cx, cy = (a + c) / 2.0, (b + d) / 2.0
            if x1 - ex <= cx <= x2 + ex and y1 - ey <= cy <= y2 + ey:
                kept.append((text, score, (a, b, c, d)))
        items = kept

    rows: list = []
    for text, score, (a, b, c, d) in sorted(items, key=lambda i: (i[2][1] + i[2][3]) / 2.0):
        cy, height = (b + d) / 2.0, max(1.0, d - b)
        for row in rows:
            if abs(cy - row["cy"]) <= max(height, row["h"]) * 0.5:
                row["items"].append((a, text, float(score)))
                row["cy"] = (row["cy"] + cy) / 2.0
                row["h"] = max(row["h"], height)
                break
        else:
            rows.append({"cy": cy, "h": height, "items": [(a, text, float(score))]})

    out = []
    for row in rows:
        pieces = sorted(row["items"])
        text = " ".join(t for _, t, _ in pieces)
        conf = sum(s for _, _, s in pieces) / len(pieces)
        out.append((text, conf))
    return out, [(t, float(s)) for t, s, _ in items]


def _scale_rect(rect, sx: float, sy: float):
    if rect is None:
        return None
    x1, y1, x2, y2 = rect
    return (x1 * sx, y1 * sy, x2 * sx, y2 * sy)


def _expected_plate_rect(hint_meta, w: int, h: int, reader_zoom: bool):
    """
    Where the tracked plate should be in the OCR image (pixels), from reader
    metadata: box_norm is the plate in the full camera frame, zoom_rect the
    reader's zoom crop in the same frame. None when unknown.
    """
    hm = hint_meta or {}
    try:
        bx1, by1, bx2, by2 = [float(v) for v in hm.get("box_norm") or ()]
    except (TypeError, ValueError):
        bx1 = None
    if reader_zoom:
        try:
            zx1, zy1, zx2, zy2 = [float(v) for v in hm.get("zoom_rect") or ()]
        except (TypeError, ValueError):
            zx1 = None
        if bx1 is not None and zx1 is not None:
            zw, zh = max(1e-6, zx2 - zx1), max(1e-6, zy2 - zy1)
            return (
                (bx1 - zx1) / zw * w,
                (by1 - zy1) / zh * h,
                (bx2 - zx1) / zw * w,
                (by2 - zy1) / zh * h,
            )
        # Old payloads: reader centres the zoom on the plate
        return (w * 0.25, h * 0.25, w * 0.75, h * 0.75)
    if bx1 is None or hm.get("evidence") not in (None, "full_frame"):
        return None
    return (bx1 * w, by1 * h, bx2 * w, by2 * h)


def _choose_plate(plates, expected_rect):
    """Plate box of the tracked vehicle: nearest to where the reader saw it."""
    if not plates:
        return None
    if expected_rect is None or len(plates) == 1:
        return plates[0]
    ex = (expected_rect[0] + expected_rect[2]) / 2.0
    ey = (expected_rect[1] + expected_rect[3]) / 2.0

    def _dist(p):
        cx, cy = (p.x1 + p.x2) / 2.0, (p.y1 + p.y2) / 2.0
        return (cx - ex) ** 2 + (cy - ey) ** 2

    return min(plates, key=_dist)


_PLATE_DETECTOR_TRUST = {
    "plate_crop": 0.18,
    "plate_wide": 0.16,
    "plate_context": 0.12,
    "reader_zoom": 0.15,
    "vehicle_bottom": 0.14,
    "vehicle_lower": 0.10,
    "plate_warped": 0.08,
    "vehicle_full": -0.05,
    "screen_inset_enhanced": -0.22,
    "full_frame_enhanced": -0.30,
    "full_frame": -0.35,
}


def _plate_detector_trust(meta: Dict[str, Any]) -> float:
    if not isinstance(meta, dict):
        return 0.0
    if meta.get("fallback"):
        return -0.25
    det = (meta.get("detector") or "").lower()
    base = det.replace("_enhanced", "").replace("_stitched", "")
    return _PLATE_DETECTOR_TRUST.get(base, _PLATE_DETECTOR_TRUST.get(det, 0.0))


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
        # consensus bonus + structured plate format over long OCR garbage
        vote = counts[plate]
        try:
            from visitor.ai.parse import _plate_format_score

            fmt = _plate_format_score(plate)
        except Exception:
            fmt = len(plate)
        score = (
            conf
            + 0.12 * (vote - 1)
            + 0.02 * fmt
            - 0.03 * max(0, len(plate) - 10)
            + _plate_detector_trust(meta)
        )
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


def _looks_like_reader_zoom(bgr) -> bool:
    """True when JPEG is already a plate-centered strip from the ANPR reader."""
    h, w = bgr.shape[:2]
    area = h * w
    # Full gate frames are ~1280x720+; reader zoom is typically ~300–500 wide strips.
    return max(h, w) <= 720 and area <= 350_000 and w >= h


def _pipeline_vehicle_plate_v2(
    bgr,
    t0: float,
    *,
    ocr_bgr=None,
    box_mul: float = 1.0,
    hint_meta: Dict[str, Any] | None = None,
    max_passes: int | None = None,
) -> Dict[str, Any]:
    """
    extract-v2 vehicle path: dedicated plate YOLO + RapidOCR.

    Detect runs on ``bgr`` (may be downscaled). OCR crops are taken from
    ``ocr_bgr`` (full-res) when provided, with boxes mapped by ``box_mul``.

    Handles (same spirit as type=id):
      - full vehicle with plate in frame
      - close-up plate only
      - soft blur / low contrast (CLAHE + mild sharpen)
      - tilt / skew (deskew + 90° rotate when portrait)
      - distant CCTV plates (wide context crop + strong upscale)
      - photo of another device / screen (inset + enhance + upscale tiny crop)
      - perspective-ish document warp fallback

    Does NOT call / modify `_pipeline_vehicle_v2` (legacy vehicle-COCO path).
    """
    h, w = bgr.shape[:2]
    ocr_frame = ocr_bgr if ocr_bgr is not None else bgr
    oh, ow = ocr_frame.shape[:2]
    logger.info(
        "vehicle-plate-v2 start detect=%sx%s ocr=%sx%s box_mul=%.3f",
        w,
        h,
        ow,
        oh,
        box_mul,
    )

    ocr_passes = 0
    pass_budget = max(1, int(max_passes or MAX_PLATE_OCR_PASSES))
    attempts = []
    # CCTV (reader metadata present): never OCR the whole frame — it mixes text
    # from other vehicles, painted body text and the OSD into one "plate".
    anpr_mode = hint_meta is not None

    def _ocr_attempt(
        image,
        meta: Dict[str, Any],
        *,
        min_long: int | None = None,
        plate_rect=None,
        variant: str = "raw",
    ) -> bool:
        """
        Run RapidOCR once on ``image`` (upscaled, then ``variant`` applied).
        plate_rect (crop pixels) limits parsing to text inside the plate.
        Returns True when a confident plate is found.
        """
        nonlocal ocr_passes
        if ocr_passes >= pass_budget or image is None or image.size == 0:
            return False
        ocr_passes += 1
        side = min_long if min_long is not None else PLATE_OCR_MIN_LONG_SIDE
        prepared, up_method = upscale_plate(image, side)
        rect = _scale_rect(
            plate_rect,
            prepared.shape[1] / float(image.shape[1]),
            prepared.shape[0] / float(image.shape[0]),
        )
        if variant == "enhanced":
            prepared = enhance_plate(prepared, rect)
        elif variant == "stitched":
            stitched = stitch_two_line(prepared, rect)
            if stitched is None:
                ocr_passes -= 1
                return False
            prepared, rect = stitched, None
        detailed = run_rapid_ocr_detailed(prepared)
        lines_local, pieces = _plate_rows(detailed, rect)
        parsed_local = extract_vehicle_number(lines_local) if lines_local else None
        if not parsed_local and len(pieces) > len(lines_local):
            parsed_local = extract_vehicle_number(pieces)
        meta = {**meta, "upscale": up_method}
        attempts.append((lines_local, parsed_local, meta))
        logger.info(
            "vehicle-plate-v2 ocr pass=%s detector=%s crop=%sx%s up=%s(%s) lines=%s "
            "dropped_outside_plate=%s parsed=%s conf=%s elapsed_ms=%s texts=%s",
            ocr_passes,
            meta.get("detector"),
            image.shape[1],
            image.shape[0],
            side,
            up_method,
            len(lines_local or []),
            len(detailed) - len(pieces),
            parsed_local[0] if parsed_local else None,
            round(float(parsed_local[1]), 3) if parsed_local else None,
            _elapsed_ms(t0),
            _ocr_lines_preview(lines_local),
        )
        return bool(parsed_local and parsed_local[1] >= MIN_OCR_CONF)

    def _detect_on(frame, conf: float = 0.25):
        try:
            return detect_plates(frame, conf=conf)
        except Exception as exc:
            logger.warning("vehicle-plate-v2 detect_plates failed: %s", exc)
            return []

    reader_zoom = bool((hint_meta or {}).get("zoom_crop")) or _looks_like_reader_zoom(
        ocr_frame
    )
    # Zoom crops are already plate-centered — use a lower YOLO threshold
    plates = _detect_on(bgr, conf=0.15 if reader_zoom else 0.25)
    detect_meta = {
        "detector": "plate_yolo",
        "plate_count": len(plates),
        "engine": "plate+rapidocr",
        "reader_zoom": reader_zoom,
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
                # OCR source must match orientation when we rotated detect frame
                if ocr_bgr is None or ocr_frame is bgr:
                    ocr_frame = bgr
                else:
                    ocr_frame = cv2.rotate(ocr_frame, cv2.ROTATE_90_CLOCKWISE)
                    oh, ow = ocr_frame.shape[:2]
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
        expected = _expected_plate_rect(hint_meta, w, h, reader_zoom) if anpr_mode else None
        best_det = _choose_plate(plates, expected)
        if best_det is not plates[0]:
            detect_meta["chosen"] = "nearest_tracked"
        best = _map_box(best_det, box_mul)
        # Clamp mapped box into OCR frame
        best.x1 = max(0, min(best.x1, ow - 1))
        best.y1 = max(0, min(best.y1, oh - 1))
        best.x2 = max(best.x1 + 1, min(best.x2, ow))
        best.y2 = max(best.y1 + 1, min(best.y2, oh))

        detect_meta.update({
            "label": best.label,
            "confidence": round(best.confidence, 3),
            "box": [best.x1, best.y1, best.x2, best.y2],
            "frame_fill_ratio": round(_box_fill_ratio(best, ocr_frame), 3),
            "vehicle_sized": _is_vehicle_sized_box(best, ocr_frame),
            "ocr_frame": [ow, oh],
        })
        if not detect_meta["vehicle_sized"]:
            crop_for_color, rect_for_color = _crop_min_context(
                ocr_frame, best, min_w=0, min_h=0, pad_ratio=0.05
            )
            detect_meta["plate_color"] = plate_color(crop_for_color, rect_for_color)

        # Vehicle YOLO fallback returns a car box — OCR lower plate zone, not whole car.
        # Real plate YOLO returns a tight plate box — OCR tight + wide context crops,
        # each raw first, then colour/glare-normalised; two-row plates also stitched.
        two_line = not detect_meta["vehicle_sized"] and is_two_line_shape(
            best.x2 - best.x1, best.y2 - best.y1
        )
        stop_on_hit = ("vehicle_bottom", "plate_crop", "plate_wide", "plate_context")
        zones = _plate_zone_crops(ocr_frame, best)
        for crop_img, det_name, rect in zones:
            variants = ["raw", "enhanced"]
            if two_line and det_name == "plate_crop":
                variants.append("stitched")
            for variant in variants:
                if ocr_passes >= pass_budget:
                    break
                name = det_name if variant == "raw" else f"{det_name}_{variant}"
                hit = _ocr_attempt(
                    crop_img,
                    {**detect_meta, "detector": name},
                    plate_rect=rect,
                    variant=variant,
                ) or hit
                if hit:
                    break
            if (hit and det_name in stop_on_hit) or ocr_passes >= pass_budget:
                break

        # Perspective warp only if still no confident hit
        if not hit and ocr_passes < pass_budget and zones:
            warped = warp_document_if_possible(zones[0][0])
            if warped is not None:
                hit = _ocr_attempt(
                    warped,
                    {**detect_meta, "detector": "plate_warped"},
                    variant="enhanced",
                )

    # Reader already sent a plate zoom JPEG — OCR the strip as a trusted crop
    # (YOLO often returns 0 on tight 359x129 crops even when the plate is readable),
    # but only text around where the reader tracked the plate.
    if not hit and reader_zoom and ocr_passes < pass_budget:
        zoom_rect = _expected_plate_rect(hint_meta, ow, oh, True) if anpr_mode else None
        detect_meta.update({
            "detector": "reader_zoom",
            "box": [0, 0, ow - 1, oh - 1],
            "frame_fill_ratio": 1.0,
            "confidence": float((hint_meta or {}).get("conf") or 0.55),
            "label": "license_plate",
        })
        for variant in ("raw", "enhanced"):
            if hit or ocr_passes >= pass_budget:
                break
            hit = _ocr_attempt(
                ocr_frame,
                {
                    **detect_meta,
                    "detector": "reader_zoom" if variant == "raw" else "reader_zoom_enhanced",
                },
                plate_rect=zoom_rect,
                variant=variant,
            ) or hit

    # No plate box (or crop OCR miss): ID-style fallbacks for close-up / screen capture.
    # Mobile uploads only — CCTV frames contain other vehicles and the OSD.
    if not anpr_mode and not hit and ocr_passes < pass_budget:
        inset_list = _prepare_inset_variant(ocr_frame)
        if inset_list:
            inset_img, inset_meta = inset_list[0]
            hit = _ocr_attempt(
                inset_img,
                {
                    **detect_meta,
                    **inset_meta,
                    "detector": "screen_inset_enhanced",
                    "fallback": True,
                },
                min_long=280,
                variant="enhanced",
            )

    if not anpr_mode and not hit and ocr_passes < pass_budget:
        hit = _ocr_attempt(
            ocr_frame,
            {
                **detect_meta,
                "detector": "full_frame_enhanced",
                "fallback": True,
                "frame_fill_ratio": 1.0,
            },
            min_long=280,
            variant="enhanced",
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
