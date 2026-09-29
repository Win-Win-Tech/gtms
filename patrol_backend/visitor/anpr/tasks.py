"""Celery tasks for CCTV ANPR OCR + gate."""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone as dt_timezone
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from celery import shared_task
from django.db import close_old_connections
from django.utils.dateparse import parse_datetime

from visitor.ai.pipeline_v2 import extract_vehicle_from_bgr

from . import settings_helpers as anpr_settings
from .gate import apply_gate_event, normalize_plate
from .ocr_gate import anpr_ocr_gate_eligible
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


def _vote_below_conf() -> float:
    try:
        return float(os.environ.get("ANPR_VOTE_BELOW_CONF", "0.95"))
    except ValueError:
        return 0.95


def _ocr_plate(ocr: Dict[str, Any]) -> str:
    if not ocr.get("found"):
        return ""
    return normalize_plate(ocr.get("number") or ocr.get("vehicle_number") or "")


def _vote_plates(reads: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    Pick the plate that most frames agree on (sum of OCR confidence, gate-eligible
    reads only). One blurred/glared frame that misreads a character is outvoted by
    two clean frames. Returns (winning OCR dict, vote summary).
    """
    tally: Dict[str, Dict[str, Any]] = {}
    for ocr in reads:
        plate = _ocr_plate(ocr)
        if not plate or not anpr_ocr_gate_eligible(ocr)[0]:
            continue
        conf = float(ocr.get("confidence") or 0.0)
        entry = tally.setdefault(plate, {"score": 0.0, "count": 0, "best": ocr})
        entry["score"] += conf
        entry["count"] += 1
        if conf > float(entry["best"].get("confidence") or 0.0):
            entry["best"] = ocr
    summary = {p: (e["count"], round(e["score"], 3)) for p, e in tally.items()}
    if not tally:
        return reads[0], summary
    winner = max(tally.values(), key=lambda e: (e["score"], e["count"]))
    return winner["best"], summary


@shared_task(
    name="visitor.anpr.tasks.process_anpr_frame",
    bind=True,
    max_retries=0,
    soft_time_limit=90,
    time_limit=120,
    expires=90,
)
def process_anpr_frame(
    self,
    *,
    camera_id: str,
    site_id: str,
    location_id: str,
    track_id: str,
    direction_mode: str = "toggle",
    gate_mode: str = "parked_toggle",
    captured_at: Optional[str] = None,
    jpeg_path: Optional[str] = None,
    ocr_jpeg_path: Optional[str] = None,
    jpeg_b64: Optional[str] = None,
    cross_dir: int = 0,
    detect_meta: Optional[Dict[str, Any]] = None,
    ocr_extra: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """
    OCR one event JPEG then apply gate. Stale frames are rejected.

    jpeg_path: full vehicle/frame evidence (saved on VisitorAsset).
    ocr_jpeg_path: optional plate-zoom crop for OCR only.
    ocr_extra: runner-up plate crops of the same vehicle ([{path, meta}]); OCR'd
        and voted on when the primary read is weak or not gate-eligible.
    gate_mode: parked_toggle | line_direction (from SiteCamera).
    """
    from scheduler.models import LocationSite, SiteCamera

    ocr_extra = [e for e in (ocr_extra or []) if isinstance(e, dict) and e.get("path")]
    temp_paths = [jpeg_path, ocr_jpeg_path] + [e["path"] for e in ocr_extra]

    # Drop stale pooled connections before any DB work (MySQL wait_timeout).
    close_old_connections()

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
            _cleanup_jpeg(*temp_paths)
            return {"ok": False, "reason": "stale", "age_sec": age}

    try:
        site = LocationSite.objects.select_related("location").get(id=site_id)
        location = site.location
        cam = SiteCamera.objects.filter(id=camera_id).first()
        direction = (cam.direction if cam else direction_mode) or "toggle"
        resolved_gate_mode = (
            (getattr(cam, "gate_mode", None) if cam else None)
            or gate_mode
            or "parked_toggle"
        )
    except LocationSite.DoesNotExist:
        logger.error("[ANPR_TASK] site missing id=%s", site_id)
        _cleanup_jpeg(*temp_paths)
        return {"ok": False, "reason": "site_missing"}

    # Prefer plate-zoom for OCR; evidence path stays full vehicle/frame.
    ocr_path = ocr_jpeg_path if (ocr_jpeg_path and os.path.isfile(ocr_jpeg_path)) else jpeg_path
    bgr = _load_bgr(ocr_path, jpeg_b64)
    if bgr is None:
        logger.warning("[ANPR_TASK] bad jpeg track=%s path=%s", track_id, ocr_path)
        _cleanup_jpeg(*temp_paths)
        return {"ok": False, "reason": "bad_jpeg"}

    # Long CPU/GPU work — release DB sockets so they are not idle-killed mid-OCR.
    close_old_connections()
    ocr = extract_vehicle_from_bgr(bgr, hint_meta=detect_meta)

    primary_ok = anpr_ocr_gate_eligible(ocr)[0]
    primary_conf = float(ocr.get("confidence") or 0.0)
    if ocr_extra and (not primary_ok or primary_conf < _vote_below_conf()):
        reads = [ocr]
        for extra in ocr_extra:
            extra_bgr = _load_bgr(extra["path"], None)
            if extra_bgr is None:
                continue
            try:
                reads.append(
                    extract_vehicle_from_bgr(
                        extra_bgr, hint_meta=extra.get("meta") or detect_meta, max_passes=2
                    )
                )
            except Exception as exc:
                logger.warning("[ANPR_TASK] extra frame OCR failed track=%s: %s", track_id, exc)
        ocr, votes = _vote_plates(reads)
        logger.info(
            "[ANPR_TASK] vote track=%s frames=%d votes=%s chosen=%s",
            track_id,
            len(reads),
            votes,
            _ocr_plate(ocr) or "-",
        )
    close_old_connections()

    if not ocr.get("found"):
        logger.info(
            "[ANPR_TASK] ocr miss track=%s reason=%s meta=%s",
            track_id,
            ocr.get("reason"),
            detect_meta,
        )
        _cleanup_jpeg(*temp_paths)
        return {"ok": False, "reason": "ocr_miss", "ocr": ocr}

    plate = normalize_plate(ocr.get("number") or ocr.get("vehicle_number") or "")
    if not plate:
        _cleanup_jpeg(*temp_paths)
        return {"ok": False, "reason": "empty_plate", "ocr": ocr}

    eligible, gate_reason = anpr_ocr_gate_eligible(ocr)
    if not eligible:
        logger.info(
            "[ANPR_TASK] gate skip track=%s reason=%s plate=%s detector=%s conf=%s",
            track_id,
            gate_reason,
            plate,
            (ocr.get("detect") or {}).get("detector"),
            ocr.get("confidence"),
        )
        _cleanup_jpeg(*temp_paths)
        return {"ok": False, "reason": gate_reason, "plate": plate, "ocr": ocr}

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
        gate_mode=resolved_gate_mode,
        cross_dir=int(cross_dir or 0),
        confidence=ocr.get("confidence"),
        evidence_path=jpeg_path,
        yolo_label=(
            (detect_meta or {}).get("vehicle_label")
            or (detect_meta or {}).get("label")
            or ocr.get("yolo_label")
            or ocr.get("vehicle_type")
            or (ocr.get("detect") or {}).get("label")
        ),
    )
    # Evidence was copied into VisitorAsset on success; always remove temp pending JPEGs
    _cleanup_jpeg(*temp_paths)
    result["ocr_confidence"] = ocr.get("confidence")
    result["track_id"] = track_id
    result["camera_id"] = camera_id
    return result


def _cleanup_jpeg(*paths: Optional[str]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
