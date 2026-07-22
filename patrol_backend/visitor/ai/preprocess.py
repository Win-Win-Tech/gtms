"""
Image preprocess for visitor AI.

Goal: shrink phone photos so YOLO + PaddleOCR stay fast on CPU (4 vCPU / 8 GB).
"""

from __future__ import annotations

from io import BytesIO

from PIL import Image, ImageOps

# Longest side after resize — good balance of OCR quality vs speed on CPU
MAX_SIDE = 1280
# Reject huge uploads early (bytes) — frontend/Postman should stay under this
MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8 MB


def load_and_resize_image(file_obj, max_side: int = MAX_SIDE) -> Image.Image:
    """
    Read uploaded file → RGB PIL image → downscale so max(width, height) <= max_side.

    Also applies EXIF transpose so phone rotations don't break detection.
    """
    raw = file_obj.read() if hasattr(file_obj, "read") else file_obj
    if isinstance(raw, (bytes, bytearray)) and len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError(
            f"Image too large (max {MAX_UPLOAD_BYTES // (1024 * 1024)} MB). "
            "Compress or resize before upload."
        )

    img = Image.open(BytesIO(raw) if isinstance(raw, (bytes, bytearray)) else file_obj)
    img = ImageOps.exif_transpose(img)
    img = img.convert("RGB")

    w, h = img.size
    longest = max(w, h)
    if longest > max_side:
        scale = max_side / float(longest)
        new_size = (max(1, int(w * scale)), max(1, int(h * scale)))
        img = img.resize(new_size, Image.Resampling.BILINEAR)
    return img


def pil_to_bgr_ndarray(img: Image.Image):
    """Convert PIL RGB → OpenCV BGR numpy array (YOLO/OCR often expect ndarray)."""
    import numpy as np

    rgb = np.asarray(img)
    # RGB → BGR
    return rgb[:, :, ::-1].copy()
