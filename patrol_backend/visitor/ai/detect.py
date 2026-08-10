"""
Detection helpers.

Vehicle: YOLOv8n (COCO) — car / truck / bus / motorcycle.
Plate: dedicated license-plate YOLO (VISITOR_AI_PLATE_WEIGHTS).
ID document: OpenCV card-like quad detector by default (COCO has no ID class).
  Optional custom YOLO weights via VISITOR_AI_ID_WEIGHTS env var.
"""

from __future__ import annotations

import logging
import os
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple

logger = logging.getLogger(__name__)

# COCO class ids used for "is there a vehicle?"
VEHICLE_CLASS_IDS = {2, 3, 5, 7}  # car, motorcycle, bus, truck
VEHICLE_CLASS_NAMES = {2: "car", 3: "motorcycle", 5: "bus", 7: "truck"}

# Default plate detector (Ultralytics Hub). Override with local .pt via VISITOR_AI_PLATE_WEIGHTS.
_DEFAULT_PLATE_HUB = "keremberke/yolov8n-license-plate-detection"
_LOCAL_PLATE_CANDIDATES = (
    Path(__file__).resolve().parent / "weights" / "license_plate_detector.pt",
    Path(__file__).resolve().parent / "weights" / "yolov8n-license-plate.pt",
)

_yolo_lock = threading.Lock()
_yolo_model = None
_yolo_id_model = None
_yolo_plate_model = None


def unload_yolo() -> None:
    """Drop warm YOLO singletons (called after idle on 8 GB hosts)."""
    global _yolo_model, _yolo_id_model, _yolo_plate_model
    with _yolo_lock:
        if _yolo_model is None and _yolo_id_model is None and _yolo_plate_model is None:
            return
        logger.info("Unloading YOLO models")
        _yolo_model = None
        _yolo_id_model = None
        _yolo_plate_model = None


def _resolve_plate_weights() -> str:
    """
    Prefer VISITOR_AI_PLATE_WEIGHTS, else local weights under visitor/ai/weights/,
    else Ultralytics Hub plate detector.
    """
    env = (os.environ.get("VISITOR_AI_PLATE_WEIGHTS") or "").strip()
    if env:
        return env
    for path in _LOCAL_PLATE_CANDIDATES:
        if path.is_file():
            return str(path)
    return _DEFAULT_PLATE_HUB


@dataclass
class Box:
    x1: int
    y1: int
    x2: int
    y2: int
    confidence: float
    label: str

    @property
    def area(self) -> int:
        return max(0, self.x2 - self.x1) * max(0, self.y2 - self.y1)


def _get_yolo():
    """Lazy-load YOLOv8n once (warm singleton)."""
    global _yolo_model
    if _yolo_model is not None:
        return _yolo_model
    with _yolo_lock:
        if _yolo_model is not None:
            return _yolo_model
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ImportError(
                "ultralytics is required for visitor AI. "
                "Install: pip install ultralytics"
            ) from exc
        weights = os.environ.get("VISITOR_AI_YOLO_WEIGHTS", "yolov8n.pt")
        logger.info("Loading YOLO weights=%s (first load may download)", weights)
        _yolo_model = YOLO(weights)
        return _yolo_model


def _get_id_yolo():
    """Optional custom ID-document YOLO. Returns None if not configured."""
    global _yolo_id_model
    weights = (os.environ.get("VISITOR_AI_ID_WEIGHTS") or "").strip()
    if not weights:
        return None
    if _yolo_id_model is not None:
        return _yolo_id_model
    with _yolo_lock:
        if _yolo_id_model is not None:
            return _yolo_id_model
        from ultralytics import YOLO

        logger.info("Loading custom ID YOLO weights=%s", weights)
        _yolo_id_model = YOLO(weights)
        return _yolo_id_model


def _get_plate_yolo():
    """Lazy-load dedicated license-plate YOLO once (warm singleton)."""
    global _yolo_plate_model
    if _yolo_plate_model is not None:
        return _yolo_plate_model
    with _yolo_lock:
        if _yolo_plate_model is not None:
            return _yolo_plate_model
        try:
            from ultralytics import YOLO
        except ImportError as exc:
            raise ImportError(
                "ultralytics is required for visitor AI plate detect. "
                "Install: pip install ultralytics"
            ) from exc
        weights = _resolve_plate_weights()
        logger.info("Loading plate YOLO weights=%s (first load may download)", weights)
        _yolo_plate_model = YOLO(weights)
        return _yolo_plate_model


def detect_vehicles(bgr_image, conf: float = 0.35) -> List[Box]:
    """
    Run YOLOv8n and return vehicle boxes sorted by area (largest first).

    Set VISITOR_AI_VEHICLE_SKIP_YOLO=1 to skip YOLO (OCR full frame) and
    avoid loading torch/ultralytics on low-RAM hosts.
    """
    skip = (os.environ.get("VISITOR_AI_VEHICLE_SKIP_YOLO") or "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if skip:
        h, w = bgr_image.shape[:2]
        return [Box(0, 0, w - 1, h - 1, 0.4, "full_frame")]

    model = _get_yolo()
    results = model.predict(
        source=bgr_image,
        conf=conf,
        classes=list(VEHICLE_CLASS_IDS),
        verbose=False,
        device="cpu",
    )
    boxes: List[Box] = []
    if not results:
        return boxes
    r0 = results[0]
    if r0.boxes is None or len(r0.boxes) == 0:
        return boxes
    xyxy = r0.boxes.xyxy.cpu().numpy()
    confs = r0.boxes.conf.cpu().numpy()
    clss = r0.boxes.cls.cpu().numpy().astype(int)
    h, w = bgr_image.shape[:2]
    for i in range(len(xyxy)):
        x1, y1, x2, y2 = [int(v) for v in xyxy[i]]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)
        cid = int(clss[i])
        boxes.append(
            Box(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                confidence=float(confs[i]),
                label=VEHICLE_CLASS_NAMES.get(cid, str(cid)),
            )
        )
    boxes.sort(key=lambda b: b.area, reverse=True)
    return boxes


def detect_plates(bgr_image, conf: float = 0.25) -> List[Box]:
    """
    Dedicated license-plate YOLO — returns plate boxes (largest first).

    Works on full-vehicle scenes and close-up plate shots.
    Weights: VISITOR_AI_PLATE_WEIGHTS, else local visitor/ai/weights/*.pt,
    else Ultralytics Hub keremberke/yolov8n-license-plate-detection.
    """
    skip = (os.environ.get("VISITOR_AI_PLATE_SKIP_YOLO") or "").lower() in (
        "1",
        "true",
        "yes",
        "on",
    )
    if skip:
        return []

    try:
        model = _get_plate_yolo()
    except Exception as exc:
        logger.warning("Plate YOLO unavailable: %s", exc)
        return []

    results = model.predict(
        source=bgr_image,
        conf=conf,
        verbose=False,
        device="cpu",
    )
    boxes: List[Box] = []
    if not results:
        return boxes
    r0 = results[0]
    if r0.boxes is None or len(r0.boxes) == 0:
        return boxes
    xyxy = r0.boxes.xyxy.cpu().numpy()
    confs = r0.boxes.conf.cpu().numpy()
    h, w = bgr_image.shape[:2]
    names = getattr(r0, "names", None) or getattr(model, "names", {}) or {}
    clss = (
        r0.boxes.cls.cpu().numpy().astype(int)
        if r0.boxes.cls is not None
        else [0] * len(xyxy)
    )
    for i in range(len(xyxy)):
        x1, y1, x2, y2 = [int(v) for v in xyxy[i]]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(w - 1, x2), min(h - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue
        cid = int(clss[i]) if i < len(clss) else 0
        label = names.get(cid, "license_plate") if isinstance(names, dict) else "license_plate"
        boxes.append(
            Box(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                confidence=float(confs[i]),
                label=str(label),
            )
        )
    boxes.sort(key=lambda b: b.area, reverse=True)
    return boxes


def detect_id_region(bgr_image) -> Optional[Box]:
    """
    Find an ID-card-like region.

    1) If VISITOR_AI_ID_WEIGHTS is set → custom YOLO
    2) Else OpenCV largest quadrilateral / large contour (document-like)
    3) Else None → caller may OCR the full (resized) frame
    """
    custom = _get_id_yolo()
    if custom is not None:
        results = custom.predict(source=bgr_image, conf=0.35, verbose=False, device="cpu")
        if results and results[0].boxes is not None and len(results[0].boxes):
            xyxy = results[0].boxes.xyxy.cpu().numpy()
            confs = results[0].boxes.conf.cpu().numpy()
            # largest box
            areas = [(b[2] - b[0]) * (b[3] - b[1]) for b in xyxy]
            i = int(max(range(len(areas)), key=lambda k: areas[k]))
            x1, y1, x2, y2 = [int(v) for v in xyxy[i]]
            return Box(x1, y1, x2, y2, float(confs[i]), "id_document")

    return _opencv_document_box(bgr_image)


def _opencv_document_box(bgr_image) -> Optional[Box]:
    """Heuristic: largest contour that looks like a card/document rectangle."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    h, w = bgr_image.shape[:2]
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, None, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    best_area = 0
    img_area = float(h * w)
    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < img_area * 0.05:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) < 4:
            continue
        x, y, bw, bh = cv2.boundingRect(approx)
        if bw < 40 or bh < 40:
            continue
        # Prefer landscape-ish cards but allow portrait passports
        ratio = bw / float(bh)
        if ratio < 0.4 or ratio > 3.5:
            continue
        if area > best_area:
            best_area = area
            best = Box(x, y, x + bw, y + bh, 0.5, "document_region")
    return best


def warp_document_if_possible(bgr_image):
    """
    Perspective-correct the largest document-like quadrilateral.
    Returns a warped BGR crop or None.
    """
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None

    h, w = bgr_image.shape[:2]
    gray = cv2.cvtColor(bgr_image, cv2.COLOR_BGR2GRAY)
    blur = cv2.GaussianBlur(gray, (5, 5), 0)
    edges = cv2.Canny(blur, 50, 150)
    edges = cv2.dilate(edges, None, iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    best_quad = None
    best_area = 0.0
    img_area = float(max(1, h * w))

    for cnt in contours:
        area = cv2.contourArea(cnt)
        if area < img_area * 0.05:
            continue
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) != 4:
            continue
        x, y, bw, bh = cv2.boundingRect(approx)
        if bw < 40 or bh < 40:
            continue
        ratio = bw / float(max(1, bh))
        if ratio < 0.4 or ratio > 3.5:
            continue
        if area > best_area:
            best_area = area
            best_quad = approx.reshape(4, 2).astype("float32")

    if best_quad is None:
        return None

    def _order_points(pts):
        rect = np.zeros((4, 2), dtype="float32")
        s = pts.sum(axis=1)
        rect[0] = pts[np.argmin(s)]
        rect[2] = pts[np.argmax(s)]
        diff = np.diff(pts, axis=1)
        rect[1] = pts[np.argmin(diff)]
        rect[3] = pts[np.argmax(diff)]
        return rect

    rect = _order_points(best_quad)
    (tl, tr, br, bl) = rect
    width_a = np.linalg.norm(br - bl)
    width_b = np.linalg.norm(tr - tl)
    height_a = np.linalg.norm(tr - br)
    height_b = np.linalg.norm(tl - bl)
    max_width = int(max(width_a, width_b))
    max_height = int(max(height_a, height_b))

    if max_width < 40 or max_height < 40:
        return None

    dst = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )
    matrix = cv2.getPerspectiveTransform(rect, dst)
    return cv2.warpPerspective(bgr_image, matrix, (max_width, max_height))


def crop_with_padding(bgr_image, box: Box, pad_ratio: float = 0.08):
    """Crop ROI with padding; returns BGR ndarray."""
    h, w = bgr_image.shape[:2]
    bw = box.x2 - box.x1
    bh = box.y2 - box.y1
    pad_x = int(bw * pad_ratio)
    pad_y = int(bh * pad_ratio)
    x1 = max(0, box.x1 - pad_x)
    y1 = max(0, box.y1 - pad_y)
    x2 = min(w, box.x2 + pad_x)
    y2 = min(h, box.y2 + pad_y)
    return bgr_image[y1:y2, x1:x2].copy()
