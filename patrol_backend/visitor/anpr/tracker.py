"""Lightweight IoU / centroid tracker for ANPR Reader."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


def _iou(a: Tuple[float, float, float, float], b: Tuple[float, float, float, float]) -> float:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw, ih = max(0.0, ix2 - ix1), max(0.0, iy2 - iy1)
    inter = iw * ih
    if inter <= 0:
        return 0.0
    area_a = max(0.0, ax2 - ax1) * max(0.0, ay2 - ay1)
    area_b = max(0.0, bx2 - bx1) * max(0.0, by2 - by1)
    union = area_a + area_b - inter
    return inter / union if union > 0 else 0.0


@dataclass
class Track:
    track_id: str
    hits: int = 1
    misses: int = 0
    nx: float = 0.5
    ny: float = 0.5
    box_norm: Tuple[float, float, float, float] = (0, 0, 0, 0)
    conf: float = 0.0
    line_side: int = 0
    prev_line_side: int = 0
    crossed: bool = False
    cross_dir: int = 0  # +1 or -1 when crossed
    state: str = "CANDIDATE"  # CANDIDATE|STABLE|OCR_QUEUED|COMMITTED|COOLDOWN
    best_sharpness: float = -1.0
    best_jpeg: Optional[bytes] = None
    best_meta: Dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)
    queued_at: float = 0.0  # monotonic time when OCR was enqueued


class SimpleTracker:
    def __init__(self, camera_key: str, iou_thresh: float = 0.25, max_misses: int = 5):
        self.camera_key = camera_key
        self.iou_thresh = iou_thresh
        self.max_misses = max_misses
        self._next_id = 1
        self.tracks: Dict[str, Track] = {}

    def _new_id(self) -> str:
        tid = f"{self.camera_key}-{self._next_id}"
        self._next_id += 1
        return tid

    def update(
        self,
        detections: List[Tuple[float, float, float, Tuple[float, float, float, float], float]],
        # each: nx, ny, line_side, box_norm(x1,y1,x2,y2), conf
    ) -> List[Track]:
        assigned: Dict[str, bool] = {}
        used_det = set()

        # Greedy match by IoU
        for tid, tr in list(self.tracks.items()):
            best_j, best_iou = -1, 0.0
            for j, (nx, ny, side, box_n, conf) in enumerate(detections):
                if j in used_det:
                    continue
                score = _iou(tr.box_norm, box_n)
                if score > best_iou:
                    best_iou, best_j = score, j
            if best_j >= 0 and best_iou >= self.iou_thresh:
                nx, ny, side, box_n, conf = detections[best_j]
                used_det.add(best_j)
                tr.prev_line_side = tr.line_side
                tr.line_side = side
                if (
                    tr.prev_line_side != 0
                    and side != 0
                    and tr.prev_line_side != side
                    and tr.state not in ("OCR_QUEUED", "COMMITTED", "COOLDOWN")
                ):
                    tr.crossed = True
                    tr.cross_dir = side  # new side after cross
                tr.nx, tr.ny = nx, ny
                tr.box_norm = box_n
                tr.conf = conf
                tr.hits += 1
                tr.misses = 0
                tr.updated_at = time.monotonic()
                if tr.hits >= 2 and tr.state == "CANDIDATE":
                    tr.state = "STABLE"
                assigned[tid] = True
            else:
                tr.misses += 1
                tr.updated_at = time.monotonic()

        for j, (nx, ny, side, box_n, conf) in enumerate(detections):
            if j in used_det:
                continue
            tid = self._new_id()
            self.tracks[tid] = Track(
                track_id=tid,
                nx=nx,
                ny=ny,
                box_norm=box_n,
                conf=conf,
                line_side=side,
                prev_line_side=side,
            )

        # Drop lost
        for tid in list(self.tracks.keys()):
            if self.tracks[tid].misses > self.max_misses:
                del self.tracks[tid]

        return list(self.tracks.values())
