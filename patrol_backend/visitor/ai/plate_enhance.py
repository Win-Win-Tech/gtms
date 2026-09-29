"""
Plate-crop image helpers for OCR (CCTV ANPR + mobile vehicle upload).

- plate_color: white / yellow / green / black / unknown (HSV)
- enhance_plate: glare suppression -> best-contrast channel -> dark text on a
  light background (green EV / black plates) -> CLAHE -> mild sharpen
- upscale_plate: FSRCNN super-resolution (OpenCV dnn, CPU) for small crops,
  bicubic otherwise
- stitch_two_line: square two-row plates (bikes, autos) -> one text row

None of this can recover characters that are not in the pixels (heavy motion
blur, fully blown-out glare); camera shutter / exposure must handle those.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Optional, Sequence, Tuple

import cv2
import numpy as np

logger = logging.getLogger(__name__)

_WEIGHTS_DIR = Path(
    os.environ.get("VISITOR_AI_PLATE_SR_DIR")
    or (Path(__file__).resolve().parent / "weights")
)
_SR_ENABLED = (os.environ.get("VISITOR_AI_PLATE_SR") or "1").lower() in (
    "1",
    "true",
    "yes",
    "on",
)
# Super-resolution only for small crops (cost grows with pixels; big crops gain nothing).
_SR_MAX_INPUT_SIDE = int(os.environ.get("VISITOR_AI_PLATE_SR_MAX_SIDE", "360"))

_sr_lock = threading.Lock()
_sr_nets: dict = {}  # scale -> cv2.dnn.Net, or False after a failed load

Rect = Tuple[int, int, int, int]


def _clip_rect(rect: Optional[Sequence[float]], w: int, h: int) -> Optional[Rect]:
    if rect is None:
        return None
    try:
        x1, y1, x2, y2 = [int(round(float(v))) for v in rect]
    except (TypeError, ValueError):
        return None
    x1, y1 = max(0, min(x1, w - 1)), max(0, min(y1, h - 1))
    x2, y2 = max(x1 + 1, min(x2, w)), max(y1 + 1, min(y2, h))
    if x2 - x1 < 4 or y2 - y1 < 4:
        return None
    return x1, y1, x2, y2


def _analysis_region(img, plate_rect: Optional[Sequence[float]] = None, frac: float = 0.75):
    """Pixels used to judge colour / polarity: the plate box, else the crop centre."""
    h, w = img.shape[:2]
    r = _clip_rect(plate_rect, w, h)
    if r is not None:
        x1, y1, x2, y2 = r
        return img[y1:y2, x1:x2]
    dy, dx = int(h * (1 - frac) / 2), int(w * (1 - frac) / 2)
    c = img[dy : h - dy, dx : w - dx]
    return c if c.size else img


def plate_color(bgr, plate_rect: Optional[Sequence[float]] = None) -> str:
    """Dominant plate background colour (Indian: white / yellow / green / black)."""
    if bgr is None or getattr(bgr, "size", 0) == 0 or bgr.ndim != 3:
        return "unknown"
    hsv = cv2.cvtColor(_analysis_region(bgr, plate_rect), cv2.COLOR_BGR2HSV)
    hue, sat, val = cv2.split(hsv)
    colored = (sat > 70) & (val > 60)
    if float(colored.mean()) >= 0.30:
        median_hue = float(np.median(hue[colored]))
        if 12 <= median_hue <= 35:
            return "yellow"
        if 36 <= median_hue <= 95:
            return "green"
        return "unknown"
    if float(np.median(val)) < 85:
        return "black"
    return "white"


def _text_is_light(gray) -> bool:
    """True when glyphs are brighter than the background (green EV, black rental plates)."""
    if gray is None or gray.size < 64:
        return False
    _, bw = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # Background covers more area than glyph strokes.
    return float((bw > 0).mean()) < 0.5


def _best_channel(bgr, plate_rect: Optional[Sequence[float]] = None):
    """
    Single channel with the strongest text/background separation.
    Grey is weak on white-on-green; the red channel separates it well.
    """
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    b, g, r = cv2.split(bgr)
    best, best_score = gray, -1.0
    for idx, ch in enumerate((gray, r, g, b)):
        region = _analysis_region(ch, plate_rect)
        if region.size < 64:
            continue
        _, bw = cv2.threshold(region, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        fg, bg = region[bw > 0], region[bw == 0]
        if fg.size < 10 or bg.size < 10:
            continue
        score = abs(float(fg.mean()) - float(bg.mean())) / (float(fg.std()) + float(bg.std()) + 1.0)
        if idx == 0:
            score *= 1.05  # prefer grey on ties
        if score > best_score:
            best, best_score = ch, score
    return best


def suppress_glare(bgr) -> Tuple[np.ndarray, float]:
    """
    Inpaint small specular highlights (sun / headlight reflections) that are much
    brighter than their surroundings. A uniformly bright white plate is untouched.
    Returns (image, glare_fraction).
    """
    if bgr is None or getattr(bgr, "size", 0) == 0 or bgr.ndim != 3:
        return bgr, 0.0
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_BGR2HSV)
    sat, val = hsv[..., 1], hsv[..., 2]
    k = max(3, min(31, (min(h, w) // 4) | 1))
    local = cv2.medianBlur(val, k)
    mask = (
        (val >= 245)
        & (sat < 60)
        & (val.astype(np.int16) - local.astype(np.int16) >= 18)
    ).astype(np.uint8) * 255
    frac = float(mask.mean()) / 255.0
    if frac < 0.002 or frac > 0.30:
        return bgr, frac
    mask = cv2.dilate(mask, np.ones((3, 3), np.uint8), iterations=1)
    return cv2.inpaint(bgr, mask, 3, cv2.INPAINT_TELEA), frac


def enhance_plate(bgr, plate_rect: Optional[Sequence[float]] = None):
    """
    OCR-friendly version of a plate crop: always dark text on a light background.
    ``plate_rect`` (x1, y1, x2, y2 in crop pixels) focuses the analysis on the plate.
    """
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return bgr
    if bgr.ndim == 2:
        bgr = cv2.cvtColor(bgr, cv2.COLOR_GRAY2BGR)
    img, _ = suppress_glare(bgr)
    gray = _best_channel(img, plate_rect)
    region = _analysis_region(gray, plate_rect)
    if float(np.median(region)) > 225:
        # Over-exposed plate: pull highlights down so faint strokes separate
        lut = (np.power(np.arange(256) / 255.0, 1.6) * 255.0).astype(np.uint8)
        gray = cv2.LUT(gray, lut)
        region = _analysis_region(gray, plate_rect)
    if _text_is_light(region):
        gray = cv2.bitwise_not(gray)
    gray = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8)).apply(gray)
    blur = cv2.GaussianBlur(gray, (0, 0), sigmaX=1.0)
    gray = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


class _DepthToSpace:
    """
    TF depth_to_space for OpenCV dnn (NCHW). OpenCV 4.10 has no importer for it and
    4.11's built-in layer rejects the TF attribute name, so FSRCNN fails to load.
    """

    def __init__(self, params, blobs):
        self.block = int(params.get("block_size", params.get("blocksize", 0)) or 0)

    def _b(self, channels: int) -> int:
        return self.block or int(round(channels ** 0.5))

    def getMemoryShapes(self, inputs):
        n, c, h, w = inputs[0]
        b = self._b(c)
        return [[n, c // (b * b), h * b, w * b]]

    def forward(self, inputs):
        x = inputs[0]
        n, c, h, w = x.shape
        b = self._b(c)
        co = c // (b * b)
        return [x.reshape(n, b, b, co, h, w).transpose(0, 3, 4, 1, 5, 2).reshape(n, co, h * b, w * b)]


_d2s_registered = False


def _load_sr(scale: int):
    global _d2s_registered
    net = _sr_nets.get(scale)
    if net is not None:
        return net or None
    with _sr_lock:
        net = _sr_nets.get(scale)
        if net is not None:
            return net or None
        path = _WEIGHTS_DIR / f"FSRCNN_x{scale}.pb"
        if not path.is_file():
            _sr_nets[scale] = False
            logger.info("Plate SR model missing (%s) — using bicubic upscale", path)
            return None
        try:
            if not _d2s_registered:
                cv2.dnn_registerLayer("DepthToSpace", _DepthToSpace)
                _d2s_registered = True
            net = cv2.dnn.readNetFromTensorflow(str(path))
            net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
            net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
            _sr_nets[scale] = net
            logger.info("Plate SR ready model=%s", path.name)
            return net
        except Exception as exc:
            _sr_nets[scale] = False
            logger.warning("Plate SR load failed (%s): %s — using bicubic", path, exc)
            return None


def super_resolve(bgr, scale: int):
    """FSRCNN on the luma channel (chroma bicubic). Returns None if unavailable."""
    net = _load_sr(scale)
    if net is None:
        return None
    ycc = cv2.cvtColor(bgr, cv2.COLOR_BGR2YCrCb)
    luma = ycc[..., 0].astype(np.float32) / 255.0
    blob = cv2.dnn.blobFromImage(luma)
    with _sr_lock:
        net.setInput(blob)
        out = net.forward()
    y_up = np.clip(out[0, 0] * 255.0, 0, 255).astype(np.uint8)
    h2, w2 = y_up.shape[:2]
    chroma = cv2.resize(ycc[..., 1:], (w2, h2), interpolation=cv2.INTER_CUBIC)
    return cv2.cvtColor(np.dstack([y_up, chroma]), cv2.COLOR_YCrCb2BGR)


def upscale_plate(bgr, min_long_side: int, max_scale: float = 12.0) -> Tuple[np.ndarray, str]:
    """
    Enlarge small plate crops so RapidOCR can read the glyphs.
    Returns (image, method) where method is none / bicubic / fsrcnn_x2 / fsrcnn_x4.
    """
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return bgr, "none"
    h, w = bgr.shape[:2]
    longest = max(h, w)
    if longest >= min_long_side:
        return bgr, "none"
    need = min(max_scale, min_long_side / float(max(1, longest)))
    out, method = bgr, "bicubic"
    if _SR_ENABLED and bgr.ndim == 3 and longest <= _SR_MAX_INPUT_SIDE and need >= 1.6:
        scale = 4 if need >= 3.0 else 2
        try:
            sr = super_resolve(bgr, scale)
        except Exception as exc:
            logger.debug("Plate SR failed: %s", exc)
            sr = None
        if sr is not None:
            out, method = sr, f"fsrcnn_x{scale}"
    oh, ow = out.shape[:2]
    target = min_long_side / float(max(oh, ow))
    if abs(target - 1.0) > 0.05:
        out = cv2.resize(
            out,
            (max(1, int(ow * target)), max(1, int(oh * target))),
            interpolation=cv2.INTER_CUBIC if target > 1 else cv2.INTER_AREA,
        )
    return out, method


def is_two_line_shape(width: float, height: float) -> bool:
    """Indian two-row plates (bikes, autos, trucks' rear) are roughly 1.2–2:1."""
    return height > 0 and (width / float(height)) < 2.4


def stitch_two_line(bgr, plate_rect: Optional[Sequence[float]] = None):
    """
    Cut a two-row plate at the gap between rows and place the rows side by side,
    so OCR reads "TN64 AF2974" as one line. Returns None when it cannot split.
    """
    if bgr is None or getattr(bgr, "size", 0) == 0:
        return None
    h, w = bgr.shape[:2]
    r = _clip_rect(plate_rect, w, h)
    if r is not None:
        x1, y1, x2, y2 = r
        mx, my = int((x2 - x1) * 0.06), int((y2 - y1) * 0.06)
        bgr = bgr[max(0, y1 - my) : min(h, y2 + my), max(0, x1 - mx) : min(w, x2 + mx)]
        h, w = bgr.shape[:2]
    if h < 16 or w < 20:
        return None
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY) if bgr.ndim == 3 else bgr
    if _text_is_light(gray):
        gray = cv2.bitwise_not(gray)
    _, ink = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    rows = ink.mean(axis=1)
    lo, hi = int(h * 0.3), int(h * 0.7)
    split = lo + int(np.argmin(rows[lo:hi])) if hi > lo else h // 2
    top, bottom = bgr[:split], bgr[split:]
    if top.shape[0] < 8 or bottom.shape[0] < 8:
        return None
    row_h = max(top.shape[0], bottom.shape[0])

    def _fit(img):
        s = row_h / float(img.shape[0])
        return cv2.resize(img, (max(1, int(img.shape[1] * s)), row_h), interpolation=cv2.INTER_CUBIC)

    top, bottom = _fit(top), _fit(bottom)
    border = np.concatenate([bgr[0], bgr[-1], bgr[:, 0], bgr[:, -1]])
    fill = np.median(border, axis=0).astype(np.uint8)
    gap = np.empty((row_h, max(6, row_h // 3)) + bgr.shape[2:], dtype=np.uint8)
    gap[...] = fill
    return np.hstack([top, gap, bottom])
