"""ANPR Reader — RTSP watch, ROI/line, track, enqueue best JPEG."""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional, Tuple

import cv2
import numpy as np
from django.conf import settings

from visitor.ai.detect import Box, detect_plates, detect_vehicles

from . import settings_helpers as anpr_settings
from .geometry import (
    crop_roi_bgr,
    expand_roi,
    line_side,
    parse_geometry,
    point_in_roi,
)
from .queue import (
    enqueue_anpr_frame,
    load_parked,
    pop_result as pop_ocr_result,
    save_parked,
    split_result,
)
from .tracker import SimpleTracker, _centre_inside, _iou

logger = logging.getLogger(__name__)


def _camera_config_key(camera) -> Tuple:
    """Fingerprint of fields that require restarting a camera worker if changed."""
    geom = getattr(camera, "anpr_geometry", None) or {}
    try:
        geom_s = json.dumps(geom, sort_keys=True, default=str)
    except TypeError:
        geom_s = str(geom)
    return (
        str(camera.id),
        str(getattr(camera, "rtsp_url", "") or ""),
        str(getattr(camera, "direction", "") or "toggle"),
        str(getattr(camera, "gate_mode", "") or "parked_toggle"),
        str(getattr(camera, "site_id", "") or ""),
        str(getattr(getattr(camera, "site", None), "location_id", "") or ""),
        geom_s,
    )


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _extra_ocr_frames() -> int:
    """Runner-up plate views sent with each event for OCR voting (0 disables)."""
    return max(0, min(4, int(os.environ.get("ANPR_EXTRA_OCR_FRAMES", "2") or 0)))


def _vehicle_assist_labels() -> frozenset:
    """
    Vehicle classes whose plate the plate model often misses (square two-row bike
    plates, truck plates). Empty (ANPR_VEHICLE_ASSIST_LABELS=) disables the assist.
    """
    raw = os.environ.get("ANPR_VEHICLE_ASSIST_LABELS", "motorcycle,bicycle,truck,bus")
    return frozenset(s.strip().lower() for s in raw.split(",") if s.strip())


def _plate_zones_without_plate(vehicles, plates, labels) -> list:
    """
    For each vehicle of ``labels`` with no detected plate inside it, a box over its
    lower part (where the plate is) so it is tracked and OCR'd like a plate.
    Boxes stay in detection-image pixels.
    """
    zones = []
    owners = {_plate_owner(_xyxy(p), vehicles) for p in plates}
    for i, v in enumerate(vehicles):
        label = (v.label or "").strip().lower()
        if label not in labels:
            continue
        vw, vh = v.x2 - v.x1, v.y2 - v.y1
        if vw <= 0 or vh <= 0:
            continue
        if i in owners:
            continue
        zones.append(
            Box(
                x1=v.x1,
                y1=int(v.y1 + 0.45 * vh),
                x2=v.x2,
                y2=v.y2,
                confidence=float(v.confidence),
                label=label,
            )
        )
    return _dedupe_zones(zones)


def _xyxy(b) -> Tuple[float, float, float, float]:
    return (float(b.x1), float(b.y1), float(b.x2), float(b.y2))


def _plate_fits(plate, vehicle) -> bool:
    """
    Plate centre inside the vehicle box and below its top 30 %. Plates are never
    at the top of their own vehicle; a car plate seen behind a parked scooter's
    handlebar is — without this the car's plate was given to the scooter (the
    scooter was never read, the car was saved as a motorcycle with its photo).
    """
    px1, py1, px2, py2 = plate
    vx1, vy1, vx2, vy2 = vehicle
    cx, cy = (px1 + px2) / 2.0, (py1 + py2) / 2.0
    return vx1 <= cx <= vx2 and vy1 + 0.3 * (vy2 - vy1) <= cy <= vy2


def _plate_owner(plate, vehicles) -> Optional[int]:
    """Index of the vehicle a plate belongs to: the smallest box it fits."""
    best, best_area = None, None
    for i, v in enumerate(vehicles):
        vb = _xyxy(v)
        if not _plate_fits(plate, vb):
            continue
        area = (vb[2] - vb[0]) * (vb[3] - vb[1])
        if best_area is None or area < best_area:
            best, best_area = i, area
    return best


def _dedupe_zones(zones) -> list:
    """
    One zone per vehicle: the vehicle model sometimes returns two boxes for one
    scooter (motorcycle + bicycle, or rider + scooter); the extra zone became a
    second track that re-read — and re-toggled — the parked vehicle.
    """
    kept = []
    for z in sorted(zones, key=lambda b: -float(b.confidence)):
        if any(
            _centre_inside(_xyxy(z), _xyxy(k)) or _centre_inside(_xyxy(k), _xyxy(z))
            for k in kept
        ):
            continue
        kept.append(z)
    return kept


def _same_spot(a, b) -> bool:
    """Two normalised boxes on the same vehicle (plate box vs lower-vehicle zone too)."""
    return _centre_inside(a, b) or _centre_inside(b, a) or _iou(a, b) >= 0.3


_PLATE_LABELS = frozenset({"", "license_plate", "plate", "number_plate"})


def _same_kind(a: str, b: str) -> bool:
    """A plate box fits any vehicle; a car box never matches a motorcycle's spot."""
    a, b = (a or "").lower(), (b or "").lower()
    return a in _PLATE_LABELS or b in _PLATE_LABELS or a == b


def _vehicle_for_track(vehicles, tr, veh_label: str, w: int, h: int):
    return _vehicle_for_box(vehicles, tr.box_norm, veh_label, w, h)


def _vehicle_for_box(vehicles, box_norm, veh_label: str, w: int, h: int):
    """
    Vehicle the plate box belongs to (evidence crop). Vehicles overlap in the
    picture — a bike parked in front of a car sits inside the car's box — so among
    boxes containing the plate centre prefer the track's own class, then a box
    holding the whole plate box, then the tightest box. None when no vehicle
    contains the plate (evidence falls back to the full frame).
    """
    bx1, by1, bx2, by2 = [float(v) for v in box_norm]
    best, best_key = None, None
    for v in vehicles:
        x1, y1, x2, y2 = v.x1 / w, v.y1 / h, v.x2 / w, v.y2 / h
        if not _plate_fits((bx1, by1, bx2, by2), (x1, y1, x2, y2)):
            continue
        tol = 0.01
        holds_box = x1 - tol <= bx1 and y1 - tol <= by1 and bx2 <= x2 + tol and by2 <= y2 + tol
        key = (
            (v.label or "").strip().lower() != veh_label,
            not holds_box,
            (x2 - x1) * (y2 - y1),
        )
        if best_key is None or key < best_key:
            best, best_key = v, key
    return best


def _rebuild_assist_vehicle(tr, veh_label: str, w: int, h: int, box_norm=None):
    """Vehicle-assist box is the lower 55 % of the vehicle: rebuild the vehicle."""
    if veh_label not in _vehicle_assist_labels():
        return None
    bx1, by1, bx2, by2 = [float(v) for v in (box_norm or tr.box_norm)]
    top = by1 - (by2 - by1) * 0.45 / 0.55
    return Box(
        x1=int(bx1 * w),
        y1=int(max(0.0, top) * h),
        x2=int(bx2 * w),
        y2=int(by2 * h),
        confidence=float(tr.conf),
        label=veh_label,
    )


def _remove_files(paths) -> None:
    for p in paths:
        if not p:
            continue
        try:
            os.remove(p)
        except OSError:
            pass


def _payload_files(payload) -> list:
    return [payload.get("jpeg_path"), payload.get("ocr_jpeg_path")] + [
        e.get("path") for e in payload.get("ocr_extra") or []
    ]


def _to_frame_boxes(boxes, scale: float, ox: int, oy: int) -> list:
    """Detection-image boxes → full-frame pixel boxes."""
    return [
        Box(
            x1=int(b.x1 / scale + ox),
            y1=int(b.y1 / scale + oy),
            x2=int(b.x2 / scale + ox),
            y2=int(b.y2 / scale + oy),
            confidence=float(b.confidence),
            label=b.label,
        )
        for b in boxes
    ]


def _cut_by_crop(box, crop_rect, w: int, h: int) -> bool:
    """
    True when the box reaches a zone-crop edge that is not the frame edge (the
    vehicle continues outside the zone). YOLO boxes of cut-off objects often stop
    a little short of the edge, hence the 2 % tolerance.
    """
    if not crop_rect:
        return True
    cx1, cy1, cx2, cy2 = crop_rect
    tx, ty = max(8, 0.02 * w), max(8, 0.02 * h)
    return (
        (cx1 > 0 and box.x1 <= cx1 + tx)
        or (cy1 > 0 and box.y1 <= cy1 + ty)
        or (cx2 < w and box.x2 >= cx2 - tx)
        or (cy2 < h and box.y2 >= cy2 - ty)
    )


def _motion_thumb(crop):
    """Small blurred grey copy of the zone for cheap frame-to-frame comparison."""
    ch, cw = crop.shape[:2]
    tw = 320
    th = max(1, int(ch * tw / float(max(cw, 1))))
    small = cv2.resize(crop, (tw, th), interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(small, cv2.COLOR_BGR2GRAY)
    return cv2.GaussianBlur(grey, (5, 5), 0)


def _changed_fraction(a, b, pixel_delta: int = 20) -> float:
    return float(np.count_nonzero(cv2.absdiff(a, b) > pixel_delta)) / float(max(a.size, 1))


def _plate_quality(frame, box_norm, w: int, h: int) -> float:
    """Sharpness of the plate region weighted by plate size (bigger = more readable)."""
    try:
        x1n, y1n, x2n, y2n = [float(v) for v in box_norm]
    except (TypeError, ValueError):
        return 0.0
    px1, py1 = max(0, int(x1n * w)), max(0, int(y1n * h))
    px2, py2 = min(w, int(x2n * w)), min(h, int(y2n * h))
    if px2 - px1 < 8 or py2 - py1 < 4:
        return 0.0
    gray = cv2.cvtColor(frame[py1:py2, px1:px2], cv2.COLOR_BGR2GRAY)
    return _sharpness(gray) * float(px2 - px1) ** 0.5


def _plate_zoom(frame, box_norm, w: int, h: int):
    """
    Plate-centred crop for OCR (distant plates) → (image, zoom_rect_norm).
    zoom_rect is None when the full frame is returned.
    """
    try:
        x1n, y1n, x2n, y2n = [float(v) for v in box_norm]
    except (TypeError, ValueError):
        return frame, None
    bw = max(0.01, x2n - x1n)
    bh = max(0.01, y2n - y1n)
    # Expand to ~min 28% width / 18% height of frame around plate
    need_w = max(bw * 3.0, 0.28)
    need_h = max(bh * 3.0, 0.18)
    cx = (x1n + x2n) / 2.0
    cy = (y1n + y2n) / 2.0
    x1 = max(0.0, cx - need_w / 2.0)
    y1 = max(0.0, cy - need_h / 2.0)
    x2 = min(1.0, cx + need_w / 2.0)
    y2 = min(1.0, cy + need_h / 2.0)
    px1, py1 = int(x1 * w), int(y1 * h)
    px2, py2 = int(x2 * w), int(y2 * h)
    if px2 <= px1 + 20 or py2 <= py1 + 20:
        return frame, None
    return frame[py1:py2, px1:px2], (px1 / w, py1 / h, px2 / w, py2 / h)


def _open_rtsp(url: str) -> Optional[cv2.VideoCapture]:
    # Force TCP via FFmpeg options when possible
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return None
    # Prefer newest frame; large OpenCV buffers are how overnight stills get stuck
    try:
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
    except Exception:
        pass
    return cap


def _frame_fingerprint(frame) -> Optional[bytes]:
    """Tiny downscale fingerprint — identical bytes ⇒ frozen / duplicate RTSP frame."""
    try:
        small = cv2.resize(frame, (32, 18), interpolation=cv2.INTER_AREA)
        return small.tobytes()
    except Exception:
        return None


def _pending_dir() -> str:
    root = getattr(settings, "MEDIA_ROOT", None) or "/tmp"
    path = os.path.join(str(root), "anpr_pending")
    os.makedirs(path, exist_ok=True)
    return path


def _save_jpeg(bgr, track_id: str) -> str:
    name = f"{track_id}-{int(time.time() * 1000)}.jpg".replace("/", "_")
    path = os.path.join(_pending_dir(), name)
    cv2.imwrite(path, bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
    return path


class _FrameGrabber:
    """
    Reads the RTSP stream continuously on its own thread and keeps only the newest
    frame. Reading only a few frames per detect tick lets the camera's stream back
    up (old frames, timeouts); this keeps the socket drained and frames live.
    The capture is owned and released by this thread only.
    """

    def __init__(self, cap: cv2.VideoCapture, camera_id: str):
        self._cap = cap
        self._lock = threading.Lock()
        self._frame = None
        self._seq = 0
        self._stop = False
        self.failed = False
        self._thread = threading.Thread(
            target=self._run, name=f"anpr-grab-{str(camera_id)[:8]}", daemon=True
        )
        self._thread.start()

    def _run(self) -> None:
        try:
            while not self._stop:
                ok, frame = self._cap.read()
                if not ok or frame is None:
                    self.failed = True
                    break
                with self._lock:
                    self._frame = frame
                    self._seq += 1
        except Exception as exc:
            logger.warning("[ANPR_READER] grabber error: %s", exc)
            self.failed = True
        finally:
            try:
                self._cap.release()
            except Exception:
                pass

    def latest(self):
        with self._lock:
            return self._frame, self._seq

    def stop(self, wait: float = 0.0) -> None:
        """Ask the thread to stop; ``wait`` > 0 blocks until it has released the capture
        (needed before interpreter exit, or the native read aborts the process)."""
        self._stop = True
        if wait > 0 and self._thread.is_alive() and self._thread is not threading.current_thread():
            self._thread.join(timeout=wait)


def _use_grab_thread() -> bool:
    return (os.environ.get("ANPR_GRAB_THREAD") or "1").lower() in ("1", "true", "yes", "on")


class CameraWorker:
    def __init__(self, camera):
        self.camera = camera
        self.config_key = _camera_config_key(camera)
        drawn_roi, self.line = parse_geometry(getattr(camera, "anpr_geometry", None) or {})
        self.roi = expand_roi(drawn_roi, anpr_settings.roi_margin())
        self.gate_mode = (
            str(getattr(camera, "gate_mode", "") or "parked_toggle").strip().lower()
            or "parked_toggle"
        )
        self.tracker = SimpleTracker(
            camera_key=str(camera.id)[:8], fps=anpr_settings.detect_fps()
        )
        self.cap: Optional[cv2.VideoCapture] = None
        self.grabber: Optional[_FrameGrabber] = None
        self._last_seq = 0
        self._last_new_frame_at = 0.0
        self.last_detect_at = 0.0
        self.reconnect_at = 0.0
        self._tick_count = 0
        self._last_status_log = 0.0
        self._motion_ref = None
        self._motion_until = 0.0
        self._last_detect_run = 0.0
        self._idle_skips = 0
        self._tick_crop = None
        self._tick_vehicles: list = []
        # track_id → {"payload", "deadline"}: frames of a vehicle that left while its
        # first read was queued; sent only if that read misses.
        self._followups: dict = {}
        self._followups_polled_at = 0.0
        # track_id → (box_norm, plate, last_seen wall time): read vehicles parked in
        # the zone. Survives track loss, tracker resets and reader restarts (Redis)
        # so a vehicle standing still is not read — or toggled — again.
        self._parked_spots: dict = {}
        self._parked_saved_at = 0.0
        self._parked_dirty = False
        keep = anpr_settings.parked_memory_sec()
        for i, item in enumerate(load_parked(str(camera.id))):
            try:
                box, plate, seen, label = item
                if plate and time.time() - float(seen) <= keep:
                    self._parked_spots[f"saved-{i}"] = (
                        tuple(float(v) for v in box), str(plate), float(seen), str(label or "")
                    )
            except (TypeError, ValueError):
                continue
        self._plates_loaded_logged = False
        # Frozen-RTSP detection: same downscaled fingerprint for too long → reconnect
        self._last_frame_fp = None
        self._frame_fp_same_since = 0.0
        self._freeze_reconnect_sec = float(
            os.environ.get("ANPR_FREEZE_RECONNECT_SEC", "20") or 20
        )
        if self.gate_mode == "line_direction" and self.line is None:
            logger.warning(
                "[ANPR_READER] camera=%s gate_mode=line_direction but no virtual line — "
                "events will not fire until a line is set in CCTV Live → ANPR zone",
                camera.id,
            )

    def close(self) -> None:
        self._drop_capture(0.0, reset_tracker=False)
        for entry in self._followups.values():
            _remove_files(_payload_files(entry["payload"]))
        self._followups.clear()

    def _drop_capture(self, reconnect_in: float = 3.0, *, reset_tracker: bool = False) -> None:
        if self.grabber is not None:
            # Grabber thread releases its capture when its current read returns
            self.grabber.stop()
            self.grabber = None
        else:
            try:
                if self.cap is not None:
                    self.cap.release()
            except Exception:
                pass
        self.cap = None
        self._last_seq = 0
        self.reconnect_at = time.monotonic() + max(0.0, reconnect_in)
        self._last_frame_fp = None
        self._frame_fp_same_since = 0.0
        if reset_tracker:
            # Drop phantom tracks tied to a frozen overnight frame
            self.tracker = SimpleTracker(
                camera_key=str(self.camera.id)[:8], fps=anpr_settings.detect_fps()
            )

    def ensure_capture(self) -> bool:
        now = time.monotonic()
        if self.grabber is not None:
            return True
        if self.cap and self.cap.isOpened():
            return True
        if now < self.reconnect_at:
            return False
        logger.info("[ANPR_READER] connecting camera=%s name=%s", self.camera.id, self.camera.name)
        self.cap = _open_rtsp(self.camera.rtsp_url)
        if not self.cap:
            self.reconnect_at = now + 5.0
            logger.warning("[ANPR_READER] connect failed camera=%s", self.camera.id)
            return False
        if _use_grab_thread():
            self.grabber = _FrameGrabber(self.cap, str(self.camera.id))
            self._last_seq = 0
            self._last_new_frame_at = now
        return True

    def _read_from_grabber(self):
        now_m = time.monotonic()
        if self.grabber.failed:
            logger.warning("[ANPR_READER] read fail camera=%s — reconnect", self.camera.id)
            self._drop_capture(3.0, reset_tracker=True)
            return None
        frame, seq = self.grabber.latest()
        if frame is None or seq == self._last_seq:
            if now_m - self._last_new_frame_at >= self._freeze_reconnect_sec:
                logger.warning(
                    "[ANPR_READER] no new RTSP frame camera=%s for %.0fs — reconnect",
                    self.camera.id,
                    now_m - self._last_new_frame_at,
                )
                self._drop_capture(2.0, reset_tracker=True)
            return None
        self._last_seq = seq
        self._last_new_frame_at = now_m
        return frame

    def _read_drain(self):
        # Drain RTSP buffer so we process near-live frames (stuck buffer = spam on old plate)
        drain_n = int(os.environ.get("ANPR_RTSP_DRAIN_FRAMES", "12") or 12)
        drain_n = max(3, min(drain_n, 30))
        ok, frame = False, None
        for _ in range(drain_n):
            ok, frame = self.cap.read()
            if not ok:
                break
        if not ok or frame is None:
            logger.warning("[ANPR_READER] read fail camera=%s — reconnect", self.camera.id)
            self._drop_capture(3.0, reset_tracker=True)
            return None
        return frame

    def read_latest(self):
        if not self.ensure_capture():
            return None
        frame = self._read_from_grabber() if self.grabber is not None else self._read_drain()
        if frame is None:
            return None

        # Detect frozen stream (camera keeps sending the same picture)
        fp = _frame_fingerprint(frame)
        now_m = time.monotonic()
        if fp is not None:
            if fp == self._last_frame_fp:
                if self._frame_fp_same_since <= 0:
                    self._frame_fp_same_since = now_m
                elif (now_m - self._frame_fp_same_since) >= self._freeze_reconnect_sec:
                    logger.warning(
                        "[ANPR_READER] frozen RTSP camera=%s for %.0fs — reconnect",
                        self.camera.id,
                        now_m - self._frame_fp_same_since,
                    )
                    self._drop_capture(2.0, reset_tracker=True)
                    return None
            else:
                self._last_frame_fp = fp
                self._frame_fp_same_since = 0.0
        return frame

    def _update_best_frames(self, tr, frame, w: int, h: int) -> None:
        """
        Best plate view = sharp *and* large plate (Laplacian variance on the plate
        box, not the whole frame — a sharp background says nothing about a moving
        bike's plate). The best frame also provides the evidence photo; runner-ups
        are kept for multi-frame OCR voting.
        """
        score = _plate_quality(frame, tr.box_norm, w, h)
        extras_keep = _extra_ocr_frames()
        is_best = score >= tr.best_sharpness
        min_extra = min((s for s, _, _ in tr.extra_ocr), default=-1.0)
        if not is_best and (
            extras_keep <= 0 or (len(tr.extra_ocr) >= extras_keep and score <= min_extra)
        ):
            return

        enc_src, zoom_rect = _plate_zoom(frame, tr.box_norm, w, h)
        ok_ocr, buf_ocr = cv2.imencode(".jpg", enc_src, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
        if not ok_ocr:
            return
        meta = {
            "conf": tr.conf,
            "box_norm": tr.box_norm,
            "sharpness": score,
            "label": tr.vehicle_label or "",
            "zoom_crop": zoom_rect is not None,
            "zoom_rect": zoom_rect,
        }
        if is_best:
            ok_ev, buf_ev = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
            if not ok_ev:
                return
            if tr.best_jpeg and extras_keep > 0:
                tr.extra_ocr.append((tr.best_sharpness, tr.best_jpeg, dict(tr.best_meta or {})))
            tr.best_sharpness = score
            tr.best_jpeg = buf_ocr.tobytes()
            tr.best_evidence_jpeg = buf_ev.tobytes()
            tr.best_meta = meta
            vehicle = _vehicle_for_track(self._tick_vehicles, tr, meta["label"].lower(), w, h)
            tr.best_vehicle = (
                vehicle if vehicle is not None and not _cut_by_crop(vehicle, self._tick_crop, w, h)
                else None
            )
        else:
            tr.extra_ocr.append((score, buf_ocr.tobytes(), meta))
        tr.extra_ocr.sort(key=lambda item: -item[0])
        del tr.extra_ocr[max(0, extras_keep):]

    def _check_ocr_result(self, tr, now_m: float) -> None:
        """
        A queued track stays OCR_QUEUED while visible (no parked re-arm spam). If
        the worker reports a miss (unreadable frame, stale in queue), or never
        answers, re-arm the track so a vehicle standing at the gate is read again
        from fresh frames — up to ANPR_MISS_RETRIES times.
        """
        if now_m - tr.result_checked_at < 2.0:
            return
        tr.result_checked_at = now_m
        outcome, plate = split_result(pop_ocr_result(tr.track_id))
        lost = outcome is None and now_m - tr.queued_at > anpr_settings.stale_frame_sec() + 45
        if outcome == "ok":
            self._commit(tr, plate)
            return
        if outcome != "miss" and not lost:
            return
        if tr.attempts > anpr_settings.miss_retries():
            self._commit(tr, "")
            logger.info(
                "[ANPR_READER] give up track=%s after %s attempts (%s)",
                tr.track_id,
                tr.attempts,
                outcome or "no result",
            )
            return
        # Plate views gathered since the first send are kept for the retry.
        tr.state = "STABLE"
        tr.followup_armed = False
        tr.crossed = bool(tr.cross_dir)
        tr.hold_until = now_m + anpr_settings.retry_collect_sec()
        logger.info(
            "[ANPR_READER] retry track=%s attempt=%s after %s",
            tr.track_id,
            tr.attempts + 1,
            outcome or "no result",
        )

    def _commit(self, tr, plate: str) -> None:
        tr.state = "COMMITTED"
        tr.plate = plate or tr.plate
        tr.park_nx, tr.park_ny = tr.nx, tr.ny
        tr.park_size = max(tr.box_norm[2] - tr.box_norm[0], tr.box_norm[3] - tr.box_norm[1])
        tr.away_hits = 0
        self._reset_views(tr)

    @staticmethod
    def _has_moved(tr) -> bool:
        """Moved more than half its own size since first seen (arriving / passing)."""
        size = max(tr.box_norm[2] - tr.box_norm[0], tr.box_norm[3] - tr.box_norm[1])
        return tr.max_shift > max(0.015, 0.5 * size)

    def _check_leaving(self, tr) -> None:
        """
        A read parked vehicle is ignored while it stands still (people or other
        vehicles moving near it change nothing). Once it drives clearly away from
        its spot on two checks in a row it is read again (its exit).
        """
        if tr.park_nx < 0 or tr.misses:
            return
        away = ((tr.nx - tr.park_nx) ** 2 + (tr.ny - tr.park_ny) ** 2) ** 0.5
        tr.away_hits = tr.away_hits + 1 if away > max(0.06, 1.5 * tr.park_size) else 0
        if tr.away_hits < 2:
            return
        logger.info("[ANPR_READER] parked track=%s plate=%s is leaving — reading again", tr.track_id, tr.plate or "-")
        if self._parked_spots.pop(tr.track_id, None):
            self._parked_dirty = True
        tr.state = "STABLE"
        tr.hits = 1
        tr.attempts = 0
        tr.crossed = False
        tr.cross_dir = 0
        tr.followup_armed = False
        tr.park_nx = tr.park_ny = -1.0
        self._reset_views(tr)

    def _poll_followups(self, now_m: float) -> None:
        """
        A vehicle that left while its first read was queued has its later frames
        held here. Miss → send them (the track itself may be gone); ok → drop them.
        """
        if not self._followups or now_m - self._followups_polled_at < 2.0:
            return
        self._followups_polled_at = now_m
        for tid, entry in list(self._followups.items()):
            outcome, plate = split_result(pop_ocr_result(tid))
            tr = self.tracker.tracks.get(tid)
            if outcome is None:
                if now_m < entry["deadline"]:
                    continue
                _remove_files(_payload_files(entry["payload"]))
            elif outcome == "ok":
                _remove_files(_payload_files(entry["payload"]))
                if tr is not None:
                    self._commit(tr, plate)
            else:
                payload = entry["payload"]
                payload["captured_at"] = datetime.now(dt_timezone.utc).isoformat()
                if enqueue_anpr_frame(payload):
                    logger.info(
                        "[ANPR_READER] follow-up sent track=%s attempt=%s (first read missed)",
                        tid,
                        payload.get("attempt"),
                    )
                    if tr is not None:
                        tr.attempts = int(payload.get("attempt") or tr.attempts + 1)
                        tr.queued_at = now_m
                        tr.result_checked_at = now_m
                else:
                    _remove_files(_payload_files(payload))
            del self._followups[tid]

    def _remember_parked(self, tracks) -> None:
        now = time.time()
        for tr in tracks:
            if tr.state == "COMMITTED" and tr.plate and tr.misses == 0 and tr.park_nx >= 0:
                if tr.track_id not in self._parked_spots:
                    self._parked_dirty = True
                self._parked_spots[tr.track_id] = (tuple(tr.box_norm), tr.plate, now, tr.vehicle_label)
        keep = anpr_settings.parked_memory_sec()
        for tid, spot in list(self._parked_spots.items()):
            if now - spot[2] > keep:
                del self._parked_spots[tid]
                self._parked_dirty = True
        since = now - self._parked_saved_at
        if (self._parked_dirty and since >= 2.0) or (self._parked_spots and since >= 15.0):
            save_parked(
                str(self.camera.id),
                [[list(box), plate, seen, label] for box, plate, seen, label in self._parked_spots.values()],
                keep,
            )
            self._parked_saved_at = now
            self._parked_dirty = False

    def _parked_at(self, tr, box_norm) -> list:
        """(track_id, plate) of read vehicles parked where this track is."""
        return [
            (tid, plate)
            for tid, (spot, plate, _, label) in self._parked_spots.items()
            if tid != tr.track_id and _same_spot(spot, box_norm) and _same_kind(label, tr.vehicle_label)
        ]

    def _should_detect(self, crop) -> bool:
        """
        Idle gate: run the models only while the zone changes, for a short hold
        after motion (a vehicle stopping), while a vehicle is still being
        collected, and every ANPR_IDLE_DETECT_SEC otherwise (parked vehicles stay
        tracked, OCR results are polled). The reference is the last detected
        frame, so slow change also triggers a detection.
        """
        if not anpr_settings.motion_gate():
            return True
        now_m = time.monotonic()
        thumb = _motion_thumb(crop)
        ref = self._motion_ref
        moved = (
            ref is None
            or ref.shape != thumb.shape
            or _changed_fraction(ref, thumb) >= anpr_settings.motion_min_area()
        )
        if moved:
            self._motion_until = now_m + anpr_settings.motion_hold_sec()
        collecting = any(
            tr.state in ("CANDIDATE", "STABLE") for tr in self.tracker.tracks.values()
        )
        if (
            moved
            or now_m < self._motion_until
            or collecting
            or now_m - self._last_detect_run >= anpr_settings.idle_detect_sec()
        ):
            self._motion_ref = thumb
            self._last_detect_run = now_m
            return True
        self._idle_skips += 1
        return False

    def process_tick(self, frame) -> None:
        h, w = frame.shape[:2]
        self._poll_followups(time.monotonic())
        crop, (ox, oy) = crop_roi_bgr(frame, self.roi)
        if not self._should_detect(crop):
            return
        # Downscale crop for detect speed (960 default — 640 missed distant parked plates)
        ch, cw = crop.shape[:2]
        self._tick_crop = (ox, oy, ox + cw, oy + ch)
        self._tick_vehicles = []
        max_side = anpr_settings.detect_max_side()
        scale = 1.0
        det_img = crop
        if max(ch, cw) > max_side:
            scale = max_side / float(max(ch, cw))
            det_img = cv2.resize(
                crop,
                (max(1, int(cw * scale)), max(1, int(ch * scale))),
                interpolation=cv2.INTER_AREA,
            )

        t_det0 = time.monotonic()
        try:
            plates = detect_plates(det_img, conf=anpr_settings.detect_conf())
        except Exception as exc:
            logger.warning("[ANPR_READER] detect_plates failed: %s", exc)
            plates = []
        detect_ms = (time.monotonic() - t_det0) * 1000.0
        if not self._plates_loaded_logged:
            self._plates_loaded_logged = True
            logger.info(
                "[ANPR_READER] first detect done camera=%s plates=%s ms=%.0f "
                "det_size=%sx%s conf=%.2f max_side=%s gate_mode=%s line=%s",
                self.camera.id,
                len(plates),
                detect_ms,
                det_img.shape[1],
                det_img.shape[0],
                anpr_settings.detect_conf(),
                max_side,
                self.gate_mode,
                "yes" if self.line else "no",
            )

        # Parked testing: if plate YOLO sees nothing, still track vehicles so OCR can run.
        used_vehicle_fallback = False
        assisted = 0
        if not plates and self.gate_mode != "line_direction":
            try:
                plates = detect_vehicles(det_img, conf=max(0.25, anpr_settings.detect_conf()))
                used_vehicle_fallback = bool(plates)
                self._tick_vehicles = _to_frame_boxes(plates, scale, ox, oy)
            except Exception as exc:
                logger.debug("[ANPR_READER] vehicle fallback failed: %s", exc)
        elif _vehicle_assist_labels():
            try:
                vehicles = detect_vehicles(det_img, conf=0.3)
                self._tick_vehicles = _to_frame_boxes(vehicles, scale, ox, oy)
                zones = _plate_zones_without_plate(vehicles, plates, _vehicle_assist_labels())
                assisted = len(zones)
                plates = list(plates) + zones
            except Exception as exc:
                logger.debug("[ANPR_READER] vehicle assist failed: %s", exc)

        # Map boxes from det_img → full frame pixels → norm
        detections = []
        for p in plates:
            # scale back to crop coords then add offset
            x1 = p.x1 / scale + ox
            y1 = p.y1 / scale + oy
            x2 = p.x2 / scale + ox
            y2 = p.y2 / scale + oy
            nx = ((x1 + x2) / 2.0) / max(w, 1)
            ny = ((y1 + y2) / 2.0) / max(h, 1)
            if not point_in_roi(nx, ny, self.roi):
                continue
            side = line_side(nx, ny, self.line)
            box_n = (x1 / w, y1 / h, x2 / w, y2 / h)
            veh_label = (getattr(p, "label", "") or "").strip().lower()
            detections.append((nx, ny, side, box_n, float(p.confidence), veh_label))

        tracks = self.tracker.update(detections)
        self._tick_count += 1
        now_m = time.monotonic()
        if now_m - self._last_status_log >= 15.0:
            self._last_status_log = now_m
            states = {}
            for tr in tracks:
                states[tr.state] = states.get(tr.state, 0) + 1
            logger.info(
                "[ANPR_READER] status camera=%s name=%s gate=%s ticks=%s "
                "plates=%s veh_assist=%s dets=%s tracks=%s states=%s veh_fb=%s detect_ms=%.0f "
                "idle_skips=%s",
                self.camera.id,
                self.camera.name,
                self.gate_mode,
                self._tick_count,
                len(plates) - assisted if not used_vehicle_fallback else 0,
                assisted,
                len(detections),
                len(tracks),
                states or "-",
                used_vehicle_fallback,
                detect_ms,
                self._idle_skips,
            )
            self._idle_skips = 0

        self._remember_parked(tracks)
        min_hits = anpr_settings.min_track_hits()
        line_mode = self.gate_mode == "line_direction"
        # ~1 s unseen: a fast vehicle has left the zone (tracks are dropped after ~5 s)
        lost_after = max(2, round(anpr_settings.lost_flush_sec() * self.tracker.fps))
        for tr in tracks:
            # Do NOT re-arm OCR_QUEUED/COOLDOWN while the same track is still visible.
            # Parked bikes / stuck RTSP frames kept matching forever and rearmed every
            # cooldown_sec → overnight IN/OUT spam on the same plate+image.
            # A new visit requires the track to drop (misses) then a fresh STABLE track.
            if tr.state == "OCR_QUEUED" and tr.misses == 0 and tr.track_id not in self._followups:
                self._check_ocr_result(tr, now_m)
            if tr.state == "COMMITTED":
                self._check_leaving(tr)

            # Keep the sharpest plate views while tracking (only when seen this tick —
            # a missed track's box points at where the plate used to be). Queued
            # tracks keep collecting for a retry if the first read misses.
            if tr.misses == 0 and (
                tr.state in ("CANDIDATE", "STABLE")
                or (tr.state == "OCR_QUEUED" and not tr.followup_armed)
            ):
                self._update_best_frames(tr, frame, w, h)

            if tr.misses >= lost_after:
                # Left before a read: a fast pass never gets 4 hits in the zone.
                if (
                    tr.state == "STABLE"
                    and tr.best_jpeg
                    and tr.hits >= min_hits
                    and (not line_mode or (tr.crossed and int(tr.cross_dir or 0) != 0))
                ):
                    if not tr.crossed:
                        tr.cross_dir = 0
                    self._send_track(tr, frame, w, h, now_m, lost=True)
                # Left while queued: hold its later frames in case that read misses.
                elif (
                    tr.state == "OCR_QUEUED"
                    and tr.best_jpeg
                    and not tr.followup_armed
                    and tr.attempts <= anpr_settings.miss_retries()
                ):
                    self._arm_followup(tr, frame, w, h, now_m)
                continue

            # Event readiness depends on gate_mode:
            # - parked_toggle: line cross OR stable/parked in ROI (testing)
            # - line_direction: virtual-line cross required (real gate)
            ready = False
            if line_mode:
                if self.line is None:
                    continue
                ready = (
                    tr.crossed
                    and int(tr.cross_dir or 0) != 0
                    and tr.hits >= min_hits
                    and tr.state == "STABLE"
                    and tr.best_jpeg
                )
            elif self.line is not None:
                ready = (
                    tr.crossed
                    and tr.hits >= min_hits
                    and tr.state == "STABLE"
                    and tr.best_jpeg
                )
                # Parked / weak motion: stable in ROI without a clean cross
                if (
                    not ready
                    and tr.hits >= max(min_hits + 2, 4)
                    and tr.state == "STABLE"
                    and tr.best_jpeg
                    and point_in_roi(tr.nx, tr.ny, self.roi)
                ):
                    ready = True
                    tr.cross_dir = 0
            else:
                # No virtual line: capture when track is stable (ROI optional filter)
                if (
                    tr.hits >= max(min_hits + 2, 4)
                    and tr.state == "STABLE"
                    and tr.best_jpeg
                    and point_in_roi(tr.nx, tr.ny, self.roi)
                ):
                    ready = True
                    tr.cross_dir = 0

            if not ready or now_m < tr.hold_until:
                continue
            self._send_track(tr, frame, w, h, now_m, lost=tr.misses > 0)

    def _choose_vehicle(self, tr, frame, w: int, h: int, veh_label: str, lost: bool):
        """
        (vehicle box, source image) for the evidence crop. A vehicle still in view
        is cropped from the current frame; one that left (or was not seen this
        check) from its best frame — the current frame no longer shows it.
        """
        if not lost:
            # This check's vehicle boxes are from the same frame; the full-frame
            # vehicle model only runs when none matches or the box is cut by the
            # zone edge (evidence must show the whole vehicle).
            chosen = _vehicle_for_track(self._tick_vehicles, tr, veh_label, w, h)
            if chosen is None or _cut_by_crop(chosen, self._tick_crop, w, h):
                chosen = (
                    _vehicle_for_track(detect_vehicles(frame, conf=0.25), tr, veh_label, w, h)
                    or chosen
                )
            return chosen or _rebuild_assist_vehicle(tr, veh_label, w, h), frame

        box_norm = tuple((tr.best_meta or {}).get("box_norm") or tr.box_norm)
        src = None
        if tr.best_evidence_jpeg:
            src = cv2.imdecode(np.frombuffer(tr.best_evidence_jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
        if src is None or src.shape[:2] != (h, w):
            return None, None
        chosen = tr.best_vehicle
        if chosen is None:
            chosen = _vehicle_for_box(detect_vehicles(src, conf=0.25), box_norm, veh_label, w, h)
        return chosen or _rebuild_assist_vehicle(tr, veh_label, w, h, box_norm), src

    def _build_payload(self, tr, frame, w: int, h: int, lost: bool) -> Optional[dict]:
        """Write the track's evidence + plate views to disk → task payload (None on error)."""
        # Vehicle class + box for evidence crop (plate YOLO only returns license_plate)
        veh_label = (tr.vehicle_label or "").strip().lower()
        chosen_vehicle, src = None, None
        try:
            chosen_vehicle, src = self._choose_vehicle(tr, frame, w, h, veh_label, lost)
            if chosen_vehicle is not None and veh_label in (
                "",
                "license_plate",
                "plate",
                "number_plate",
                "full_frame",
            ):
                veh_label = (chosen_vehicle.label or "").strip().lower()
        except Exception as exc:
            logger.debug("[ANPR_READER] vehicle detect skipped: %s", exc)

        meta = dict(tr.best_meta or {})
        if veh_label and veh_label not in (
            "license_plate",
            "plate",
            "number_plate",
            "full_frame",
        ):
            meta["label"] = veh_label
            meta["vehicle_label"] = veh_label

        # Evidence JPEG: prefer padded vehicle crop, else full frame (never plate zoom).
        evidence_bytes = tr.best_evidence_jpeg or tr.best_jpeg
        if chosen_vehicle is not None and src is not None:
            try:
                pad = 0.12
                x1 = max(0, int(chosen_vehicle.x1 - pad * w))
                y1 = max(0, int(chosen_vehicle.y1 - pad * h))
                x2 = min(w, int(chosen_vehicle.x2 + pad * w))
                y2 = min(h, int(chosen_vehicle.y2 + pad * h))
                if x2 > x1 + 40 and y2 > y1 + 40:
                    ok_v, buf_v = cv2.imencode(
                        ".jpg",
                        src[y1:y2, x1:x2],
                        [int(cv2.IMWRITE_JPEG_QUALITY), 88],
                    )
                    if ok_v:
                        evidence_bytes = buf_v.tobytes()
                        meta["evidence"] = "vehicle_crop"
            except Exception as exc:
                logger.debug("[ANPR_READER] vehicle evidence crop failed: %s", exc)
        if not meta.get("evidence"):
            meta["evidence"] = "full_frame" if tr.best_evidence_jpeg else "ocr_fallback"

        path = None
        ocr_path = None
        ocr_extra = []
        try:
            name = f"{tr.track_id}-{int(time.time() * 1000)}.jpg".replace("/", "_")
            path = os.path.join(_pending_dir(), name)
            with open(path, "wb") as f:
                f.write(evidence_bytes)
            # Separate plate-zoom file for OCR when we have one
            if (
                tr.best_jpeg
                and tr.best_evidence_jpeg
                and tr.best_jpeg != tr.best_evidence_jpeg
                and meta.get("zoom_crop")
            ):
                ocr_path = os.path.join(
                    _pending_dir(), name.replace(".jpg", "_ocr.jpg")
                )
                with open(ocr_path, "wb") as f:
                    f.write(tr.best_jpeg)
            for idx, (_, jpeg, extra_meta) in enumerate(tr.extra_ocr, start=2):
                extra_path = os.path.join(
                    _pending_dir(), name.replace(".jpg", f"_ocr{idx}.jpg")
                )
                with open(extra_path, "wb") as f:
                    f.write(jpeg)
                extra_meta = dict(extra_meta)
                if not extra_meta.get("zoom_crop"):
                    extra_meta["evidence"] = "full_frame"
                ocr_extra.append({"path": extra_path, "meta": extra_meta})
        except OSError as exc:
            logger.warning("[ANPR_READER] jpeg write failed: %s", exc)
            _remove_files([path, ocr_path] + [e["path"] for e in ocr_extra])
            return None

        payload = {
            "camera_id": str(self.camera.id),
            "site_id": str(self.camera.site_id),
            "location_id": str(self.camera.site.location_id),
            "track_id": tr.track_id,
            "direction_mode": self.camera.direction or "toggle",
            "gate_mode": self.gate_mode,
            "captured_at": datetime.now(dt_timezone.utc).isoformat(),
            "jpeg_path": path,
            "ocr_jpeg_path": ocr_path,
            "ocr_extra": ocr_extra,
            "cross_dir": int(tr.cross_dir or 0),
            "detect_meta": meta,
            "attempt": tr.attempts + 1,
            "stationary": not int(tr.cross_dir or 0) and not self._has_moved(tr),
        }
        # Read vehicles still standing in view right now. Over one of them (no line
        # cross) the same plate is a duplicate track on it → still parked. Anywhere
        # else their plate is a neighbour's (a parked car's plate next to a passing
        # auto) and must not be booked to this vehicle.
        box_norm = tuple(meta.get("box_norm") or tr.box_norm)
        known, neighbours = set(), set()
        for other in self.tracker.tracks.values():
            if other is tr or other.state != "COMMITTED" or not other.plate or other.misses:
                continue
            if (
                not int(tr.cross_dir or 0)
                and _same_spot(other.box_norm, box_norm)
                and _same_kind(other.vehicle_label, tr.vehicle_label)
            ):
                known.add(other.plate)
            else:
                neighbours.add(other.plate)
        if known:
            payload["known_plates"] = sorted(known)
        if neighbours - known:
            payload["neighbour_plates"] = sorted(neighbours - known)
        return payload

    @staticmethod
    def _reset_views(tr) -> None:
        tr.best_sharpness = -1.0
        tr.best_jpeg = None
        tr.best_evidence_jpeg = None
        tr.best_vehicle = None
        tr.best_meta = {}
        tr.extra_ocr = []

    def _send_track(self, tr, frame, w: int, h: int, now_m: float, *, lost: bool) -> None:
        if not int(tr.cross_dir or 0) and not self._has_moved(tr):
            box_norm = tuple((tr.best_meta or {}).get("box_norm") or tr.box_norm) if lost else tr.box_norm
            parked = self._parked_at(tr, box_norm)
            if parked:
                # Standing still where a read vehicle is parked: the same vehicle
                # seen again (occlusion, extra box, reader restart) — no OCR.
                self._commit(tr, parked[0][1])
                logger.info(
                    "[ANPR_READER] still parked track=%s plate=%s — not read again",
                    tr.track_id,
                    tr.plate,
                )
                return
        payload = self._build_payload(tr, frame, w, h, lost)
        if payload is None:
            return
        if lost and tr.misses > 0:
            logger.info(
                "[ANPR_READER] send track=%s after it left (hits=%s, unseen %s checks)",
                tr.track_id,
                tr.hits,
                tr.misses,
            )
        if not enqueue_anpr_frame(payload):
            # backpressure — delete unused jpeg
            _remove_files(_payload_files(payload))
            return
        tr.state = "OCR_QUEUED"
        tr.attempts += 1
        tr.result_checked_at = now_m
        tr.crossed = False
        tr.queued_at = now_m
        self._reset_views(tr)

    def _arm_followup(self, tr, frame, w: int, h: int, now_m: float) -> None:
        """Queued vehicle left: keep the views gathered since the send for a miss."""
        tr.followup_armed = True
        payload = self._build_payload(tr, frame, w, h, lost=True)
        self._reset_views(tr)
        if payload is None:
            return
        self._followups[tr.track_id] = {
            "payload": payload,
            "deadline": tr.queued_at + anpr_settings.stale_frame_sec() + 45,
        }
        logger.info(
            "[ANPR_READER] follow-up held track=%s (sent only if the first read misses)",
            tr.track_id,
        )


def _is_mysql_gone_away(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return (
        "gone away" in msg
        or "connection reset" in msg
        or "lost connection" in msg
        or "2006" in msg
        or "2013" in msg
    )


def _configure_pool_no_rollback_on_return() -> None:
    """
    dj_db_conn_pool → SQLAlchemy QueuePool defaults to reset_on_return=rollback.
    When a remote MySQL socket dies while idle, that ROLLBACK logs:
      "Exception during reset or similar" / MySQL server has gone away
    Disable reset-on-return for this long-lived process (reader is read-mostly).
    """
    try:
        from dj_db_conn_pool.core import pool_container
        from sqlalchemy.pool.base import ResetStyle

        with pool_container.lock:
            for pool in pool_container.values():
                try:
                    pool._reset_on_return = ResetStyle.reset_none
                except Exception:
                    try:
                        pool._reset_on_return = None
                    except Exception:
                        pass
    except Exception:
        pass


def _release_db_connection() -> None:
    """Return the thread-local connection to the pool immediately after use."""
    from django.db import connections

    _configure_pool_no_rollback_on_return()
    for alias in connections:
        conn = connections[alias]
        try:
            if conn.connection is None:
                continue
            # Invalidate if the socket is already dead (avoids ROLLBACK noise).
            try:
                raw = getattr(conn.connection, "driver_connection", None) or conn.connection
                invalidate = getattr(conn.connection, "invalidate", None)
                if callable(invalidate):
                    # Probe without a full query when possible
                    sock = getattr(raw, "_sock", None)
                    if sock is None:
                        invalidate()
                        conn.connection = None
                        continue
            except Exception:
                pass
            try:
                conn.close()
            except Exception as exc:
                if _is_mysql_gone_away(exc):
                    try:
                        conn.connection = None
                    except Exception:
                        pass
                else:
                    logger.debug("[ANPR_READER] release db: %s", exc)
        except Exception as exc:
            logger.debug("[ANPR_READER] release db alias=%s: %s", alias, exc)


def _db_keepalive() -> None:
    """Ping MySQL so the pool never sits idle past remote wait_timeout / firewalls."""
    from django.db import connection
    from django.db.utils import OperationalError as DjOperationalError

    try:
        connection.ensure_connection()
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
    except Exception as exc:
        if _is_mysql_gone_away(exc) or isinstance(exc, DjOperationalError):
            logger.info("[ANPR_READER] DB keepalive reconnect after %s", type(exc).__name__)
            try:
                if connection.connection is not None:
                    inv = getattr(connection.connection, "invalidate", None)
                    if callable(inv):
                        inv(exc)
                connection.connection = None
            except Exception:
                pass
            try:
                connection.ensure_connection()
                with connection.cursor() as cur:
                    cur.execute("SELECT 1")
            except Exception:
                logger.warning("[ANPR_READER] DB keepalive failed — will retry next tick")
        else:
            logger.debug("[ANPR_READER] DB keepalive: %s", exc)
    finally:
        _release_db_connection()


def load_cameras():
    """
    Load enabled cameras. Always release the DB connection afterwards.

    Root cause of the recurring ERROR: management commands keep a checked-out
    connection for the whole process. After ANPR_CAMERA_REFRESH_SEC (~300s) of
    RTSP-only work, remote MySQL has dropped the socket; close/dispose then
    triggers SQLAlchemy ROLLBACK → "Exception during reset or similar".
    """
    from django.db.utils import OperationalError as DjOperationalError
    from scheduler.models import SiteCamera

    try:
        import pymysql

        pymysql_errors: Tuple[type, ...] = (
            pymysql.err.OperationalError,
            pymysql.err.InterfaceError,
        )
    except ImportError:
        pymysql_errors = ()

    def _fetch():
        qs = (
            SiteCamera.objects.filter(
                is_enabled=True,
                camera_type=SiteCamera.CameraType.VEHICLE,
            )
            .select_related("site")
            .order_by("sort_order", "name")
        )
        return list(qs[: anpr_settings.max_cameras()])

    try:
        try:
            return _fetch()
        except Exception as exc:
            catch_types = (DjOperationalError,) + pymysql_errors
            if not isinstance(exc, catch_types) and not _is_mysql_gone_away(exc):
                raise
            logger.warning(
                "[ANPR_READER] DB error on camera refresh (%s) — retry once",
                type(exc).__name__,
            )
            try:
                from django.db import connection

                if connection.connection is not None:
                    inv = getattr(connection.connection, "invalidate", None)
                    if callable(inv):
                        inv(exc)
                    connection.connection = None
            except Exception:
                pass
            return _fetch()
    finally:
        _release_db_connection()


def run_reader_loop(*, once: bool = False) -> None:
    if not anpr_settings.anpr_enabled():
        logger.error("[ANPR_READER] ANPR_ENABLED is false — set env/settings to start")
        return

    interval = 1.0 / anpr_settings.detect_fps()
    refresh_every = float(anpr_settings.camera_refresh_sec())
    # Keep below typical remote MySQL / NAT idle kills (~120–300s)
    keepalive_every = float(os.environ.get("ANPR_DB_KEEPALIVE_SEC", "60"))
    workers: List[CameraWorker] = []

    def refresh_workers():
        nonlocal workers
        try:
            cams = load_cameras()
        except Exception:
            logger.exception(
                "[ANPR_READER] camera refresh failed (DB?) — keeping current workers"
            )
            return

        by_id = {str(c.id): c for c in cams}
        new_workers: List[CameraWorker] = []
        added = updated = removed = 0

        for w in workers:
            cam = by_id.pop(str(w.camera.id), None)
            if cam is None:
                w.close()
                removed += 1
                logger.info("[ANPR_READER] removed camera=%s", w.camera.id)
                continue
            key = _camera_config_key(cam)
            if key == w.config_key:
                # Unchanged — keep RTSP + tracker state
                new_workers.append(w)
                continue
            w.close()
            new_workers.append(CameraWorker(cam))
            updated += 1
            logger.info("[ANPR_READER] updated camera=%s name=%s", cam.id, cam.name)

        for cam in by_id.values():
            new_workers.append(CameraWorker(cam))
            added += 1
            logger.info("[ANPR_READER] added camera=%s name=%s", cam.id, cam.name)

        workers = new_workers
        if added or updated or removed:
            logger.info(
                "[ANPR_READER] active cameras=%s (added=%s updated=%s removed=%s)",
                len(workers),
                added,
                updated,
                removed,
            )
        else:
            logger.debug(
                "[ANPR_READER] camera refresh: no changes (active=%s)",
                len(workers),
            )

    refresh_workers()
    last_refresh = time.monotonic()
    last_keepalive = time.monotonic()
    logger.info(
        "[ANPR_READER] started fps=%s interval=%.2fs camera_refresh=%ss db_keepalive=%ss",
        anpr_settings.detect_fps(),
        interval,
        int(refresh_every),
        int(keepalive_every),
    )

    while True:
        loop_start = time.monotonic()
        if loop_start - last_keepalive >= keepalive_every:
            _db_keepalive()
            last_keepalive = loop_start
        if loop_start - last_refresh >= refresh_every:
            refresh_workers()
            last_refresh = loop_start

        for w in workers:
            frame = w.read_latest()
            if frame is None:
                continue
            try:
                w.process_tick(frame)
            except Exception:
                logger.exception("[ANPR_READER] tick failed camera=%s", w.camera.id)

        if once:
            break

        elapsed = time.monotonic() - loop_start
        sleep_for = interval - elapsed
        if sleep_for > 0:
            time.sleep(sleep_for)
