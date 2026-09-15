"""ROI box + virtual line helpers (normalized 0..1 coordinates).

ROI and line are optional per camera:
- neither → detect/capture on stable track (full frame)
- ROI only → restrict detection / stable-in-ROI capture
- line only → enqueue on virtual-line cross
- both → ROI filter + line cross (with parked-in-ROI fallback)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class NormBox:
    x1: float
    y1: float
    x2: float
    y2: float

    def clamp(self) -> "NormBox":
        x1 = min(max(self.x1, 0.0), 1.0)
        y1 = min(max(self.y1, 0.0), 1.0)
        x2 = min(max(self.x2, 0.0), 1.0)
        y2 = min(max(self.y2, 0.0), 1.0)
        if x2 <= x1:
            x2 = min(1.0, x1 + 0.01)
        if y2 <= y1:
            y2 = min(1.0, y1 + 0.01)
        return NormBox(x1, y1, x2, y2)

    def to_pixels(self, width: int, height: int) -> Tuple[int, int, int, int]:
        b = self.clamp()
        return (
            int(b.x1 * width),
            int(b.y1 * height),
            int(b.x2 * width),
            int(b.y2 * height),
        )

    def to_dict(self) -> Dict[str, float]:
        b = self.clamp()
        return {"x1": b.x1, "y1": b.y1, "x2": b.x2, "y2": b.y2}


@dataclass
class NormLine:
    x1: float
    y1: float
    x2: float
    y2: float

    def clamp(self) -> "NormLine":
        return NormLine(
            min(max(self.x1, 0.0), 1.0),
            min(max(self.y1, 0.0), 1.0),
            min(max(self.x2, 0.0), 1.0),
            min(max(self.y2, 0.0), 1.0),
        )

    def to_pixels(self, width: int, height: int) -> Tuple[int, int, int, int]:
        ln = self.clamp()
        return (
            int(ln.x1 * width),
            int(ln.y1 * height),
            int(ln.x2 * width),
            int(ln.y2 * height),
        )

    def to_dict(self) -> Dict[str, float]:
        ln = self.clamp()
        return {"x1": ln.x1, "y1": ln.y1, "x2": ln.x2, "y2": ln.y2}


def _has_xy_keys(d: Any) -> bool:
    if not isinstance(d, dict) or not d:
        return False
    return all(k in d for k in ("x1", "y1", "x2", "y2"))


def parse_geometry(raw: Optional[Dict[str, Any]]) -> Tuple[Optional[NormBox], Optional[NormLine]]:
    """Parse optional ROI / line. Missing or empty keys → None (no default zone)."""
    data = raw if isinstance(raw, dict) else {}
    roi: Optional[NormBox] = None
    line: Optional[NormLine] = None
    if _has_xy_keys(data.get("roi")):
        roi_d = data["roi"]
        roi = NormBox(
            float(roi_d.get("x1", 0)),
            float(roi_d.get("y1", 0)),
            float(roi_d.get("x2", 1)),
            float(roi_d.get("y2", 1)),
        ).clamp()
    if _has_xy_keys(data.get("line")):
        line_d = data["line"]
        line = NormLine(
            float(line_d.get("x1", 0)),
            float(line_d.get("y1", 0.55)),
            float(line_d.get("x2", 1)),
            float(line_d.get("y2", 0.55)),
        ).clamp()
    return roi, line


def geometry_to_dict(roi: Optional[NormBox], line: Optional[NormLine]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    if roi is not None:
        out["roi"] = roi.to_dict()
    if line is not None:
        out["line"] = line.to_dict()
    return out


def point_in_roi(nx: float, ny: float, roi: Optional[NormBox]) -> bool:
    """True if point is inside ROI, or always True when no ROI is configured."""
    if roi is None:
        return True
    r = roi.clamp()
    return r.x1 <= nx <= r.x2 and r.y1 <= ny <= r.y2


def line_side(nx: float, ny: float, line: Optional[NormLine]) -> int:
    """
    Sign of cross product: which side of the directed line the point is on.
    Returns -1, 0, or +1. Returns 0 when no line is configured.
    """
    if line is None:
        return 0
    ln = line.clamp()
    ax, ay = ln.x2 - ln.x1, ln.y2 - ln.y1
    bx, by = nx - ln.x1, ny - ln.y1
    cross = ax * by - ay * bx
    if abs(cross) < 1e-6:
        return 0
    return 1 if cross > 0 else -1


def crop_roi_bgr(bgr, roi: Optional[NormBox]):
    """Crop to ROI, or return full frame when ROI is not set."""
    if roi is None:
        return bgr, (0, 0)

    h, w = bgr.shape[:2]
    x1, y1, x2, y2 = roi.to_pixels(w, h)
    x1, y1 = max(0, x1), max(0, y1)
    x2, y2 = min(w, x2), min(h, y2)
    if x2 - x1 < 8 or y2 - y1 < 8:
        return bgr, (0, 0)
    return bgr[y1:y2, x1:x2].copy(), (x1, y1)


def boxes_to_centroids_norm(
    boxes: List[Any],
    frame_w: int,
    frame_h: int,
    offset_xy: Tuple[int, int] = (0, 0),
) -> List[Tuple[float, float, Any]]:
    """Return (nx, ny, box) for each detection in full-frame normalized coords."""
    ox, oy = offset_xy
    out = []
    for box in boxes:
        cx = (box.x1 + box.x2) / 2.0 + ox
        cy = (box.y1 + box.y2) / 2.0 + oy
        nx = cx / max(frame_w, 1)
        ny = cy / max(frame_h, 1)
        out.append((nx, ny, box))
    return out
