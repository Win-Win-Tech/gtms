"""Lightweight IoU / centroid tracker for ANPR Reader."""

from __future__ import annotations

import os
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
    vehicle_label: str = ""
    line_side: int = 0
    prev_line_side: int = 0
    crossed: bool = False
    cross_dir: int = 0  # +1 or -1 when crossed
    state: str = "CANDIDATE"  # CANDIDATE|STABLE|OCR_QUEUED|COMMITTED|COOLDOWN
    best_sharpness: float = -1.0
    best_jpeg: Optional[bytes] = None  # plate zoom — OCR only
    best_evidence_jpeg: Optional[bytes] = None  # full frame / vehicle — visitor photos
    best_meta: Dict[str, Any] = field(default_factory=dict)
    # Runner-up plate zooms (score, jpeg, meta) for multi-frame OCR voting
    extra_ocr: List[Tuple[float, bytes, Dict[str, Any]]] = field(default_factory=list)
    updated_at: float = field(default_factory=time.monotonic)
    created_at: float = field(default_factory=time.monotonic)
    queued_at: float = 0.0  # monotonic time when OCR was enqueued


def _box_area(b: Tuple[float, float, float, float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


class SimpleTracker:
    def __init__(
        self,
        camera_key: str,
        iou_thresh: float = 0.25,
        max_misses: int = 5,
        max_jump: Optional[float] = None,
    ):
        self.camera_key = camera_key
        self.iou_thresh = iou_thresh
        self.max_misses = max_misses
        # Plate boxes are small: a moving plate often has zero IoU with its previous
        # box at 1–3 detections/s. Fall back to nearest centre within this jump
        # (normalised frame units, per missed frame it may travel further).
        self.max_jump = (
            float(os.environ.get("ANPR_TRACK_MAX_JUMP", "0.2") or 0.2)
            if max_jump is None
            else float(max_jump)
        )
        self._next_id = 1
        self.tracks: Dict[str, Track] = {}

    def _new_id(self) -> str:
        tid = f"{self.camera_key}-{self._next_id}"
        self._next_id += 1
        return tid

    @staticmethod
    def _apply(tr: Track, det) -> None:
        nx, ny, side, box_n, conf, veh_label = det
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
        if veh_label:
            tr.vehicle_label = veh_label
        tr.hits += 1
        tr.misses = 0
        tr.updated_at = time.monotonic()
        if tr.hits >= 2 and tr.state == "CANDIDATE":
            tr.state = "STABLE"

    def update(
        self,
        detections: List[
            Tuple[float, float, int, Tuple[float, float, float, float], float, str]
        ],
        # each: nx, ny, line_side, box_norm, conf, vehicle_label
    ) -> List[Track]:
        used_det = set()
        matched = set()

        # 1) Greedy match by IoU (parked / slow vehicles)
        for tid, tr in list(self.tracks.items()):
            best_j, best_iou = -1, 0.0
            for j, det in enumerate(detections):
                if j in used_det:
                    continue
                score = _iou(tr.box_norm, det[3])
                if score > best_iou:
                    best_iou, best_j = score, j
            if best_j >= 0 and best_iou >= self.iou_thresh:
                used_det.add(best_j)
                matched.add(tid)
                self._apply(tr, detections[best_j])

        # 2) Nearest centre for the rest (moving vehicles), closest pairs first
        pairs = []
        for tid, tr in self.tracks.items():
            if tid in matched:
                continue
            limit = self.max_jump * (1.0 + 0.5 * tr.misses)
            area_t = _box_area(tr.box_norm)
            for j, det in enumerate(detections):
                if j in used_det:
                    continue
                dist = ((det[0] - tr.nx) ** 2 + (det[1] - tr.ny) ** 2) ** 0.5
                if dist > limit:
                    continue
                area_d = _box_area(det[3])
                if area_t > 0 and area_d > 0 and not (0.33 <= area_d / area_t <= 3.0):
                    continue
                pairs.append((dist, tid, j))
        for dist, tid, j in sorted(pairs):
            if tid in matched or j in used_det:
                continue
            used_det.add(j)
            matched.add(tid)
            self._apply(self.tracks[tid], detections[j])

        now = time.monotonic()
        for tid, tr in self.tracks.items():
            if tid not in matched:
                tr.misses += 1
                tr.updated_at = now

        for j, (nx, ny, side, box_n, conf, veh_label) in enumerate(detections):
            if j in used_det:
                continue
            tid = self._new_id()
            self.tracks[tid] = Track(
                track_id=tid,
                nx=nx,
                ny=ny,
                box_norm=box_n,
                conf=conf,
                vehicle_label=veh_label or "",
                line_side=side,
                prev_line_side=side,
            )

        # Drop lost
        for tid in list(self.tracks.keys()):
            if self.tracks[tid].misses > self.max_misses:
                del self.tracks[tid]

        return list(self.tracks.values())
