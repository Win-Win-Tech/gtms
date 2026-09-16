"""ANPR-specific OCR trust rules (stricter than mobile upload extract-v2)."""

from __future__ import annotations

from typing import Any, Dict, Tuple

from visitor.ai.parse import is_rejected_anpr_plate

from .gate import normalize_plate

_ANPR_TRUSTED_DETECTORS = frozenset({
    "plate_crop",
    "plate_wide",
    "plate_context",
    "reader_zoom",
    "vehicle_bottom",
    "vehicle_lower",
    "plate_warped",
})

_ANPR_UNTRUSTED_DETECTORS = frozenset({
    "full_frame_enhanced",
    "screen_inset_enhanced",
    "full_frame",
    "full_frame_fallback",
    "full_frame_inset",
    "vehicle_full",
})

# Distant CCTV plate YOLO often scores 0.30–0.45; vehicle boxes are higher.
_MIN_ANPR_YOLO_CONF_PLATE = 0.28
_MIN_ANPR_YOLO_CONF_VEHICLE = 0.50
_MIN_ANPR_OCR_CONF = 0.45


def _detector_base(detector: str) -> str:
    det = (detector or "").lower().strip()
    if det.endswith("_enhanced"):
        return det[: -len("_enhanced")]
    return det


def anpr_ocr_gate_eligible(ocr: Dict[str, Any]) -> Tuple[bool, str]:
    """
    Return (eligible, reason) for creating a CCTV check-in/out from OCR output.

    Requires a plate/vehicle-zone crop (not full-frame OSD fallback) and rejects
    watermark/timestamp garbage plates.
    """
    if not ocr.get("found"):
        return False, "ocr_miss"

    detect = ocr.get("detect") or {}
    if not isinstance(detect, dict):
        detect = {}

    detector = (detect.get("detector") or "").lower()
    base = _detector_base(detector)

    plate = normalize_plate(ocr.get("number") or ocr.get("vehicle_number") or "")
    raw_text = ocr.get("raw_text") or []
    source_lines = [(t, 1.0) for t in raw_text if t]
    if is_rejected_anpr_plate(plate, source_lines):
        return False, "watermark_plate"

    conf = ocr.get("confidence")
    if conf is not None and float(conf) < _MIN_ANPR_OCR_CONF:
        return False, "low_ocr_conf"

    if detect.get("fallback") or detector in _ANPR_UNTRUSTED_DETECTORS:
        return False, "untrusted_ocr_source"

    if base not in _ANPR_TRUSTED_DETECTORS and detector not in _ANPR_TRUSTED_DETECTORS:
        return False, "no_plate_crop"

    yolo_conf = detect.get("confidence")
    if yolo_conf is not None:
        min_yolo = (
            _MIN_ANPR_YOLO_CONF_PLATE
            if base.startswith("plate") or (detect.get("label") or "").lower()
            in ("license_plate", "plate", "number_plate")
            else _MIN_ANPR_YOLO_CONF_VEHICLE
        )
        if float(yolo_conf) < min_yolo:
            return False, "low_yolo_conf"

    if not detect.get("box") and int(detect.get("plate_count") or 0) <= 0:
        return False, "no_detection_box"

    return True, "ok"
