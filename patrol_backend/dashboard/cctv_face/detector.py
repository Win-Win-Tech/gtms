"""Face detection with OpenCV YuNet (cv2.FaceDetectorYN, small ONNX model, CPU)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np

from . import settings_helpers as face_settings

logger = logging.getLogger(__name__)

MODEL_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "models",
    "face_detection_yunet_2023mar.onnx",
)


@dataclass
class FaceBox:
    """One face in full-frame pixel coordinates."""

    x1: int
    y1: int
    x2: int
    y2: int
    score: float
    # Right eye, left eye, nose tip, right / left mouth corner: (x, y) pairs
    landmarks: Tuple[float, ...] = ()

    @property
    def width(self) -> int:
        return self.x2 - self.x1

    @property
    def height(self) -> int:
        return self.y2 - self.y1

    @property
    def centre(self) -> Tuple[float, float]:
        return (self.x1 + self.x2) / 2.0, (self.y1 + self.y2) / 2.0


class FaceDetector:
    def __init__(
        self,
        model_path: str = MODEL_PATH,
        *,
        score_threshold: Optional[float] = None,
        max_side: Optional[int] = None,
    ):
        self.model_path = model_path
        self.score_threshold = (
            face_settings.detect_score() if score_threshold is None else float(score_threshold)
        )
        self.max_side = face_settings.detect_max_side() if max_side is None else int(max_side)
        self._net = None
        self._input_size: Tuple[int, int] = (0, 0)
        self._failed = False

    @property
    def available(self) -> bool:
        return self._load() is not None

    def _load(self):
        if self._net is not None or self._failed:
            return self._net
        try:
            self._net = cv2.FaceDetectorYN.create(
                self.model_path, "", (320, 320), self.score_threshold, 0.3, 50
            )
        except Exception as exc:
            self._failed = True
            logger.error("[FACE_CCTV] face detector load failed (%s): %s", self.model_path, exc)
        return self._net

    def detect(self, frame_bgr: np.ndarray) -> List[FaceBox]:
        net = self._load()
        if net is None or frame_bgr is None or frame_bgr.size == 0:
            return []
        h, w = frame_bgr.shape[:2]
        scale = min(1.0, self.max_side / float(max(h, w)))
        img = frame_bgr
        if scale < 1.0:
            img = cv2.resize(
                frame_bgr,
                (max(1, int(w * scale)), max(1, int(h * scale))),
                interpolation=cv2.INTER_AREA,
            )
        ih, iw = img.shape[:2]
        if self._input_size != (iw, ih):
            net.setInputSize((iw, ih))
            self._input_size = (iw, ih)
        _, faces = net.detect(img)
        return faces_from_yunet(faces, scale, w, h)


def faces_from_yunet(faces, scale: float, frame_w: int, frame_h: int) -> List[FaceBox]:
    """YuNet rows [x, y, w, h, 10 landmark values, score] → FaceBox in full-frame pixels."""
    if faces is None:
        return []
    inv = 1.0 / scale if scale > 0 else 1.0
    out: List[FaceBox] = []
    for row in np.asarray(faces, dtype=np.float32).reshape(-1, 15):
        x, y, bw, bh = (float(v) * inv for v in row[:4])
        x1 = max(0, int(round(x)))
        y1 = max(0, int(round(y)))
        x2 = min(frame_w, int(round(x + bw)))
        y2 = min(frame_h, int(round(y + bh)))
        if x2 - x1 < 2 or y2 - y1 < 2:
            continue
        out.append(
            FaceBox(
                x1=x1,
                y1=y1,
                x2=x2,
                y2=y2,
                score=float(row[14]),
                landmarks=tuple(float(v) * inv for v in row[4:14]),
            )
        )
    return out
