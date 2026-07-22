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

__all__ = ["extract_from_upload"]
