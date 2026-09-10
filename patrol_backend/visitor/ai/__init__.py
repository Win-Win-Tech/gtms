"""
Visitor AI extraction — CPU-friendly pipeline for 4 vCPU / 8 GB RAM hosts.

Flow:
  upload → preprocess (resize) → detect (OpenCV document / optional YOLO)
  → crop ROI → PaddleOCR → parse → JSON response

Light defaults (8 GB):
  - angle classifier off
  - 2 CPU threads
  - idle unload of OCR/YOLO after ~90s
  - YOLO only for type=vehicle (unless VEHICLE_SKIP_YOLO)
"""

from .pipeline import extract_from_upload
from .pipeline_v2 import extract_from_upload_v2, extract_vehicle_from_bgr

__all__ = ["extract_from_upload", "extract_from_upload_v2", "extract_vehicle_from_bgr"]
