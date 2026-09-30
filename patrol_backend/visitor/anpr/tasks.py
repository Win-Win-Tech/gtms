"""Celery tasks for CCTV ANPR OCR + gate."""

from __future__ import annotations

import logging
import os
import threading
import time
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
from .queue import report_result

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
    # A read missing only the last digit (TN58BF482 of TN58BF4827) backs the full plate.
    support = {
        p: e["score"]
        + sum(o["score"] for q, o in tally.items() if len(q) == len(p) - 1 and p.startswith(q))
        for p, e in tally.items()
    }
    winner = max(tally.items(), key=lambda pe: (support[pe[0]], pe[1]["score"], pe[1]["count"]))[1]
    return winner["best"], summary


@shared_task(
    name="visitor.anpr.tasks.process_anpr_frame",
    bind=True,
    max_retries=0,
    soft_time_limit=90,
    time_limit=120,
)
def process_anpr_frame(self, **kwargs) -> Dict[str, Any]:
    """Run one event and tell the reader whether the vehicle needs another try."""
    track_id = kwargs.get("track_id")
    result: Dict[str, Any] = {"ok": False, "reason": "error"}
    try:
        result = _process_anpr_frame(**kwargs)
        return result
    finally:
        outcome = _outcome(result)
        report_result(track_id, outcome, str(result.get("plate") or "") if outcome == "ok" else "")


# Gate decided on a real plate without an event — retrying would not change it.
_DECIDED_REASONS = frozenset({
    "cooldown", "already_in", "no_open_entry", "no_cross_dir", "lane_entry_only",
    "lane_exit_only", "still_parked",
})


def _same_plate(a: str, b: str) -> bool:
    """Equal, or one OCR character apart (glare on a parked plate)."""
    if not a or not b or len(a) != len(b):
        return False
    return sum(1 for x, y in zip(a, b) if x != y) <= 1


def _outcome(result: Dict[str, Any]) -> str:
    if result.get("ok") or str(result.get("reason") or "") in _DECIDED_REASONS:
        return "ok"
    return "miss"


def _process_anpr_frame(
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
    attempt: int = 1,
    known_plates: Optional[List[str]] = None,
    stationary: bool = False,
) -> Dict[str, Any]:
    """
    OCR one event JPEG then apply gate. Stale frames are rejected.

    jpeg_path: full vehicle/frame evidence (saved on VisitorAsset).
    ocr_jpeg_path: optional plate-zoom crop for OCR only.
    ocr_extra: runner-up plate crops of the same vehicle ([{path, meta}]); OCR'd
        and voted on when the primary read is weak or not gate-eligible.
    gate_mode: parked_toggle | line_direction (from SiteCamera).
    known_plates: plates already read at this track's parked spot; reading one of
        them again means the same vehicle is still parked (no IN/OUT toggle).
    stationary: the vehicle never moved while tracked — may check in, never out.
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
    t_start = time.monotonic()
    ocr = extract_vehicle_from_bgr(bgr, hint_meta=detect_meta)

    primary_ok = anpr_ocr_gate_eligible(ocr)[0]
    primary_conf = float(ocr.get("confidence") or 0.0)
    if ocr_extra and (not primary_ok or primary_conf < _vote_below_conf()):
        reads = [ocr]
        budget = anpr_settings.vote_budget_sec()
        for extra in ocr_extra:
            if budget and time.monotonic() - t_start >= budget:
                logger.info(
                    "[ANPR_TASK] vote budget %.0fs used — skip remaining frames track=%s",
                    budget,
                    track_id,
                )
                break
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
            "[ANPR_TASK] ocr miss track=%s attempt=%s reason=%s raw=%s",
            track_id,
            attempt,
            ocr.get("reason"),
            (ocr.get("raw_text") or [])[:6],
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

    for known in known_plates or []:
        if _same_plate(plate, normalize_plate(str(known or ""))):
            logger.info(
                "[ANPR_TASK] still parked plate=%s (read before at this spot) track=%s",
                plate,
                track_id,
            )
            _cleanup_jpeg(*temp_paths)
            return {"ok": False, "reason": "still_parked", "plate": plate}

    result = apply_gate_event(
        plate=plate,
        site=site,
        location=location,
        direction_mode=direction,
        gate_mode=resolved_gate_mode,
        cross_dir=int(cross_dir or 0),
        stationary=bool(stationary),
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


def _worker_serves_anpr_queue() -> bool:
    import re
    import sys

    argv = " ".join(sys.argv)
    m = re.search(r"(?:-Q|--queues)[=\s]*([\w,.\-]+)", argv)
    if not m:
        return False
    return anpr_settings.queue_name() in m.group(1).split(",")


def _warm_models() -> None:
    t0 = time.monotonic()
    try:
        from visitor.ai.detect import detect_plates
        from visitor.ai.plate_enhance import upscale_plate
        from visitor.ai.rapid_ocr_engine import run_rapid_ocr_detailed

        detect_plates(np.zeros((320, 320, 3), dtype=np.uint8), conf=0.25)
        blank = np.full((60, 200, 3), 255, dtype=np.uint8)
        cv2.putText(blank, "TN01AB1234", (5, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 0), 2)
        upscale_plate(blank[:, :100], 480)
        run_rapid_ocr_detailed(blank)
        logger.info("[ANPR_TASK] models warm (keep loaded) ms=%.0f", (time.monotonic() - t0) * 1000)
    except Exception as exc:
        logger.warning("[ANPR_TASK] model warm-up failed: %s", exc)


try:
    from celery.signals import worker_process_init

    @worker_process_init.connect
    def _anpr_worker_process_init(**_kwargs) -> None:
        """
        The ANPR worker must answer in seconds: never idle-unload plate YOLO, and
        load models at start instead of on the first vehicle (~15 s cold).
        ANPR_WARM_MODELS=0 disables; =1 forces on any worker.
        """
        flag = (os.environ.get("ANPR_WARM_MODELS") or "auto").strip().lower()
        if flag in ("0", "false", "no", "off"):
            return
        if flag not in ("1", "true", "yes", "on") and not _worker_serves_anpr_queue():
            return
        from visitor.ai import memory

        memory.IDLE_UNLOAD_SEC = 0
        # Background: worker_process_init must return within a few seconds.
        threading.Thread(target=_warm_models, name="anpr-warmup", daemon=True).start()
except ImportError:
    pass


def _cleanup_jpeg(*paths: Optional[str]) -> None:
    for path in paths:
        if not path:
            continue
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass
