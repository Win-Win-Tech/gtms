"""Celery tasks for CCTV ANPR OCR + gate."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, Optional

import cv2
import numpy as np
from celery import shared_task
from django.utils.dateparse import parse_datetime

from visitor.ai.pipeline_v2 import extract_vehicle_from_bgr

from . import settings_helpers as anpr_settings
from .gate import apply_gate_event, normalize_plate
from .plate_stabilize import stabilize_plate

logger = logging.getLogger(__name__)


def _parse_captured_at(raw: Optional[str]) -> Optional[datetime]:
    if not raw:
        return None
    dt = parse_datetime(raw)
    if dt is None:
        return None
    if timezone_is_naive(dt):
        dt = dt.replace(tzinfo=dt_timezone.utc)
    return dt


def timezone_is_naive(dt: datetime) -> bool:
    return dt.tzinfo is None or dt.tzinfo.utcoffset(dt) is None


def _load_bgr(jpeg_path: Optional[str], jpeg_b64: Optional[str]):
    if jpeg_path and os.path.isfile(jpeg_path):
        data = np.fromfile(jpeg_path, dtype=np.uint8)
        bgr = cv2.imdecode(data, cv2.IMREAD_COLOR)
        return bgr
    if jpeg_b64:
        import base64

        raw = base64.b64decode(jpeg_b64)
        data = np.frombuffer(raw, dtype=np.uint8)
        return cv2.imdecode(data, cv2.IMREAD_COLOR)
    return None


@shared_task(
    name="visitor.anpr.tasks.process_anpr_frame",
    bind=True,
    max_retries=0,
    soft_time_limit=30,
    time_limit=45,
    expires=60,
)
def process_anpr_frame(
    self,
    *,
    camera_id: str,
    site_id: str,
    location_id: str,
    track_id: str,
    direction_mode: str = "toggle",
    captured_at: Optional[str] = None,
    jpeg_path: Optional[str] = None,
    jpeg_b64: Optional[str] = None,
    cross_dir: int = 0,
    detect_meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    OCR one event JPEG then apply gate. Stale frames are rejected.
    """
    from scheduler.models import Location, LocationSite, SiteCamera

    now = datetime.now(dt_timezone.utc)
    captured = _parse_captured_at(captured_at)
    if captured is not None:
        age = (now - captured.astimezone(dt_timezone.utc)).total_seconds()
        if age > anpr_settings.stale_frame_sec():
            logger.warning(
                "[ANPR_TASK] stale frame age=%.1fs track=%s camera=%s",
                age,
                track_id,
                camera_id,
            )
            _cleanup_jpeg(jpeg_path)
            return {"ok": False, "reason": "stale", "age_sec": age}

    try:
        site = LocationSite.objects.select_related("location").get(id=site_id)
        location = site.location
        cam = SiteCamera.objects.filter(id=camera_id).first()
        direction = (cam.direction if cam else direction_mode) or "toggle"
    except LocationSite.DoesNotExist:
        logger.error("[ANPR_TASK] site missing id=%s", site_id)
        _cleanup_jpeg(jpeg_path)
        return {"ok": False, "reason": "site_missing"}

    bgr = _load_bgr(jpeg_path, jpeg_b64)
    if bgr is None:
        logger.warning("[ANPR_TASK] bad jpeg track=%s path=%s", track_id, jpeg_path)
        _cleanup_jpeg(jpeg_path)
        return {"ok": False, "reason": "bad_jpeg"}

    ocr = extract_vehicle_from_bgr(bgr)
    if not ocr.get("found"):
        logger.info(
            "[ANPR_TASK] ocr miss track=%s reason=%s meta=%s",
            track_id,
            ocr.get("reason"),
            detect_meta,
        )
        _cleanup_jpeg(jpeg_path)
        return {"ok": False, "reason": "ocr_miss", "ocr": ocr}

    plate = normalize_plate(ocr.get("number") or ocr.get("vehicle_number") or "")
    if not plate:
        _cleanup_jpeg(jpeg_path)
        return {"ok": False, "reason": "empty_plate", "ocr": ocr}

    resolved, how = stabilize_plate(
        plate,
        site_id=site.id,
        track_id=track_id,
        confidence=ocr.get("confidence"),
    )
    if resolved != plate:
        logger.info(
            "[ANPR_TASK] plate corrected %s -> %s (%s) track=%s",
            plate,
            resolved,
            how,
            track_id,
        )
    plate = resolved

    result = apply_gate_event(
        plate=plate,
        site=site,
        location=location,
        direction_mode=direction,
        cross_dir=int(cross_dir or 0),
        confidence=ocr.get("confidence"),
        evidence_path=jpeg_path,
        yolo_label=(
            ocr.get("yolo_label")
            or ocr.get("vehicle_type")
            or (ocr.get("detect") or {}).get("label")
            or (detect_meta or {}).get("label")
        ),
    )
    # Evidence was copied into VisitorAsset on success; always remove temp pending JPEG
    _cleanup_jpeg(jpeg_path)
    result["ocr_confidence"] = ocr.get("confidence")
    result["track_id"] = track_id
    result["camera_id"] = camera_id
    return result


def _cleanup_jpeg(path: Optional[str]) -> None:
    if not path:
        return
    try:
        if os.path.isfile(path):
            os.remove(path)
    except OSError:
        pass
