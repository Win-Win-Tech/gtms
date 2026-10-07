"""Follow each face across frames so a person passing the camera is handled once."""

from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

import cv2
import numpy as np

from .detector import FaceBox

# Context kept around the face box in the saved crop (fraction of box size per side)
CROP_PAD = 0.25


@dataclass
class FaceTrack:
    track_id: str
    box: FaceBox
    first_seen: float
    last_seen: float
    hits: int = 1
    best_quality: float = 0.0
    best_crop: Optional[np.ndarray] = None
    # Face box inside best_crop: (x1, y1, x2, y2)
    best_box_in_crop: Optional[Tuple[int, int, int, int]] = None
    best_face_px: int = 0
    best_score: float = 0.0


def iou(a: FaceBox, b: FaceBox) -> float:
    ix1, iy1 = max(a.x1, b.x1), max(a.y1, b.y1)
    ix2, iy2 = min(a.x2, b.x2), min(a.y2, b.y2)
    inter = max(0, ix2 - ix1) * max(0, iy2 - iy1)
    if inter <= 0:
        return 0.0
    union = a.width * a.height + b.width * b.height - inter
    return inter / float(union) if union > 0 else 0.0


def _near(a: FaceBox, b: FaceBox) -> bool:
    """Fallback for fast walkers: centres within ~one face width."""
    (ax, ay), (bx, by) = a.centre, b.centre
    reach = 0.8 * max(a.width, a.height, b.width, b.height)
    return (ax - bx) ** 2 + (ay - by) ** 2 <= reach * reach


def face_quality(frame: np.ndarray, box: FaceBox) -> float:
    """Bigger, more confident and sharper faces recognise better."""
    face = frame[box.y1:box.y2, box.x1:box.x2]
    if face.size == 0:
        return 0.0
    grey = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
    sharp = float(cv2.Laplacian(grey, cv2.CV_64F).var())
    return float(box.width * box.height) * box.score * (1.0 + min(sharp, 300.0) / 300.0)


def crop_with_pad(frame: np.ndarray, box: FaceBox):
    h, w = frame.shape[:2]
    px, py = int(box.width * CROP_PAD), int(box.height * CROP_PAD)
    cx1, cy1 = max(0, box.x1 - px), max(0, box.y1 - py)
    cx2, cy2 = min(w, box.x2 + px), min(h, box.y2 + py)
    crop = frame[cy1:cy2, cx1:cx2].copy()
    return crop, (box.x1 - cx1, box.y1 - cy1, box.x2 - cx1, box.y2 - cy1)


class FaceTracker:
    def __init__(
        self,
        *,
        camera_key: str,
        min_hits: int,
        lost_sec: float,
        min_face_px: int,
        iou_threshold: float = 0.3,
    ):
        self.camera_key = camera_key
        self.min_hits = min_hits
        self.lost_sec = lost_sec
        self.min_face_px = min_face_px
        self.iou_threshold = iou_threshold
        self.tracks: Dict[str, FaceTrack] = {}
        self._ids = itertools.count(1)

    def is_confirmed(self, track: FaceTrack) -> bool:
        return track.hits >= self.min_hits

    def update(self, faces: List[FaceBox], frame: np.ndarray, now: float) -> List[FaceTrack]:
        """Match this frame's faces to tracks; return tracks that just left the view."""
        pairs = []
        for tid, tr in self.tracks.items():
            for fi, face in enumerate(faces):
                overlap = iou(tr.box, face)
                if overlap >= self.iou_threshold or _near(tr.box, face):
                    pairs.append((overlap, tid, fi))
        pairs.sort(reverse=True)

        used_tracks, used_faces = set(), set()
        for _, tid, fi in pairs:
            if tid in used_tracks or fi in used_faces:
                continue
            used_tracks.add(tid)
            used_faces.add(fi)
            tr = self.tracks[tid]
            tr.box = faces[fi]
            tr.hits += 1
            tr.last_seen = now
            self._keep_best(tr, frame)

        for fi, face in enumerate(faces):
            if fi in used_faces:
                continue
            tid = f"{self.camera_key}-f{next(self._ids)}"
            tr = FaceTrack(track_id=tid, box=face, first_seen=now, last_seen=now)
            self._keep_best(tr, frame)
            self.tracks[tid] = tr

        return self.expire(now)

    def expire(self, now: float) -> List[FaceTrack]:
        gone = [tid for tid, tr in self.tracks.items() if now - tr.last_seen > self.lost_sec]
        return [self.tracks.pop(tid) for tid in gone]

    def flush(self) -> List[FaceTrack]:
        gone = list(self.tracks.values())
        self.tracks.clear()
        return gone

    def _keep_best(self, tr: FaceTrack, frame: np.ndarray) -> None:
        box = tr.box
        if box.width < self.min_face_px:
            return
        quality = face_quality(frame, box)
        if quality <= tr.best_quality:
            return
        tr.best_quality = quality
        tr.best_crop, tr.best_box_in_crop = crop_with_pad(frame, box)
        tr.best_face_px = box.width
        tr.best_score = box.score
