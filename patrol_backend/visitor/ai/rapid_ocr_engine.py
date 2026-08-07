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

# Cap RapidOCR internal resize (default 2000 is slow on CPU).
_OCR_MAX_SIDE = int(os.environ.get("VISITOR_AI_OCR_MAX_SIDE", "960"))
_OCR_THREADS = int(os.environ.get("VISITOR_AI_OCR_THREADS", "2"))
# Angle classifier is expensive; pipeline already tries 90° rotates when needed.
_OCR_USE_CLS = (os.environ.get("VISITOR_AI_OCR_USE_CLS") or "0").lower() in (
    "1",
    "true",
    "yes",
    "on",
)


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

        logger.info(
            "Loading RapidOCR (max_side=%s threads=%s use_cls=%s)",
            _OCR_MAX_SIDE,
            _OCR_THREADS,
            _OCR_USE_CLS,
        )
        try:
            # Keyword overrides map into RapidOCR config.yaml sections.
            _rapid_ocr_engine = RapidOCR(
                **{
                    "Global.use_cls": _OCR_USE_CLS,
                    "Global.max_side_len": _OCR_MAX_SIDE,
                    "Global.intra_op_num_threads": _OCR_THREADS,
                    "Global.inter_op_num_threads": _OCR_THREADS,
                    "Det.intra_op_num_threads": _OCR_THREADS,
                    "Det.inter_op_num_threads": _OCR_THREADS,
                    "Det.limit_side_len": min(736, _OCR_MAX_SIDE),
                    "Cls.intra_op_num_threads": _OCR_THREADS,
                    "Cls.inter_op_num_threads": _OCR_THREADS,
                    "Rec.intra_op_num_threads": _OCR_THREADS,
                    "Rec.inter_op_num_threads": _OCR_THREADS,
                    "Rec.rec_batch_num": 8,
                }
            )
        except Exception as exc:
            logger.error("Failed to initialize RapidOCR: %s", exc)
            raise RuntimeError(f"RapidOCR initialization failed: {exc}") from exc

        return _rapid_ocr_engine


def _box_center(box) -> Tuple[float, float]:
    """Return (cx, cy) for a RapidOCR 4-point box."""
    try:
        pts = list(box)
        xs = [float(p[0]) for p in pts]
        ys = [float(p[1]) for p in pts]
        return (sum(xs) / len(xs), sum(ys) / len(ys))
    except Exception:
        return (0.0, 0.0)


def run_rapid_ocr(bgr_image) -> List[Tuple[str, float]]:
    """
    Run RapidOCR on BGR image.

    Returns list of (text, confidence) in **reading order** (top→bottom,
    then left→right). Reading order is required so name parsers can use
    label→next-line and "above DOB / above Nationality" layout rules.
    """
    if bgr_image is None or getattr(bgr_image, "size", 0) == 0:
        return []

    ocr = _get_rapid_ocr()
    rows: List[Tuple[float, float, str, float]] = []

    try:
        # RapidOCR returns tuple (result, elapse)
        # result is a list of [box, text, score]
        result, _ = ocr(bgr_image)
    except Exception as exc:
        logger.error("Error executing RapidOCR: %s", exc)
        return []

    if not result:
        return []

    for item in result:
        if not item or len(item) < 3:
            continue
        text = str(item[1]).strip()
        if not text:
            continue
        try:
            score = float(item[2])
        except (ValueError, TypeError):
            score = 0.0
        cx, cy = _box_center(item[0])
        rows.append((cy, cx, text, score))

    if not rows:
        return []

    # Cluster into text rows (~half a typical line height)
    ys = sorted(r[0] for r in rows)
    gaps = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
    median_gap = sorted(gaps)[len(gaps) // 2] if gaps else 20.0
    row_tol = max(12.0, min(40.0, median_gap * 0.6 if median_gap > 0 else 18.0))

    rows.sort(key=lambda r: (r[0], r[1]))
    ordered: List[Tuple[str, float]] = []
    cluster: List[Tuple[float, float, str, float]] = []
    cluster_y = None
    for row in rows:
        cy, cx, text, score = row
        if cluster_y is None or abs(cy - cluster_y) <= row_tol:
            cluster.append(row)
            cluster_y = cy if cluster_y is None else (cluster_y * 0.7 + cy * 0.3)
        else:
            cluster.sort(key=lambda r: r[1])
            ordered.extend((t, s) for _, _, t, s in cluster)
            cluster = [row]
            cluster_y = cy
    if cluster:
        cluster.sort(key=lambda r: r[1])
        ordered.extend((t, s) for _, _, t, s in cluster)
    return ordered
