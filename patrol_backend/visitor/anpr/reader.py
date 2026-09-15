"""ANPR Reader — RTSP watch, ROI/line, track, enqueue best JPEG."""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone as dt_timezone
from typing import List, Optional, Tuple

import cv2
import numpy as np
from django.conf import settings

from visitor.ai.detect import detect_plates

from . import settings_helpers as anpr_settings
from .geometry import (
    crop_roi_bgr,
    line_side,
    parse_geometry,
    point_in_roi,
)
from .queue import enqueue_anpr_frame
from .tracker import SimpleTracker

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
        str(getattr(camera, "site_id", "") or ""),
        str(getattr(getattr(camera, "site", None), "location_id", "") or ""),
        geom_s,
    )


def _sharpness(gray: np.ndarray) -> float:
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def _open_rtsp(url: str) -> Optional[cv2.VideoCapture]:
    # Force TCP via FFmpeg options when possible
    os.environ.setdefault("OPENCV_FFMPEG_CAPTURE_OPTIONS", "rtsp_transport;tcp")
    cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
    if not cap.isOpened():
        cap.release()
        return None
    return cap


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


class CameraWorker:
    def __init__(self, camera):
        self.camera = camera
        self.config_key = _camera_config_key(camera)
        self.roi, self.line = parse_geometry(getattr(camera, "anpr_geometry", None) or {})
        self.tracker = SimpleTracker(camera_key=str(camera.id)[:8])
        self.cap: Optional[cv2.VideoCapture] = None
        self.last_detect_at = 0.0
        self.reconnect_at = 0.0

    def close(self) -> None:
        if self.cap is not None:
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None

    def ensure_capture(self) -> bool:
        now = time.monotonic()
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
        return True

    def read_latest(self):
        if not self.ensure_capture():
            return None
        # Drain a few frames to reduce lag
        ok, frame = False, None
        for _ in range(3):
            ok, frame = self.cap.read()
            if not ok:
                break
        if not ok or frame is None:
            logger.warning("[ANPR_READER] read fail camera=%s — reconnect", self.camera.id)
            try:
                self.cap.release()
            except Exception:
                pass
            self.cap = None
            self.reconnect_at = time.monotonic() + 3.0
            return None
        return frame

    def process_tick(self, frame) -> None:
        h, w = frame.shape[:2]
        crop, (ox, oy) = crop_roi_bgr(frame, self.roi)
        # Downscale crop for detect speed
        ch, cw = crop.shape[:2]
        max_side = 640
        scale = 1.0
        det_img = crop
        if max(ch, cw) > max_side:
            scale = max_side / float(max(ch, cw))
            det_img = cv2.resize(
                crop,
                (max(1, int(cw * scale)), max(1, int(ch * scale))),
                interpolation=cv2.INTER_AREA,
            )

        try:
            plates = detect_plates(det_img, conf=anpr_settings.detect_conf())
        except Exception as exc:
            logger.warning("[ANPR_READER] detect_plates failed: %s", exc)
            plates = []

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
            detections.append((nx, ny, side, box_n, float(p.confidence)))

        tracks = self.tracker.update(detections)
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        sharp = _sharpness(gray)
        now_m = time.monotonic()
        rearm_after = float(anpr_settings.cooldown_sec())

        min_hits = anpr_settings.min_track_hits()
        for tr in tracks:
            # After OCR enqueue, parked cars stay matched to same track forever —
            # re-arm after gate cooldown so toggle can fire check-in again.
            if (
                tr.state in ("OCR_QUEUED", "COOLDOWN")
                and tr.queued_at > 0
                and (now_m - tr.queued_at) >= rearm_after
            ):
                logger.info(
                    "[ANPR_READER] rearm track=%s camera=%s after %.0fs",
                    tr.track_id,
                    self.camera.id,
                    rearm_after,
                )
                tr.state = "STABLE"
                tr.crossed = False
                tr.cross_dir = 0
                tr.best_jpeg = None
                tr.best_sharpness = -1.0
                tr.best_meta = {}
                tr.hits = min_hits  # need a few more frames before parked fallback
                tr.queued_at = 0.0

            # Update best frame while tracking
            if tr.state in ("CANDIDATE", "STABLE") and sharp >= tr.best_sharpness:
                ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                if ok:
                    tr.best_sharpness = sharp
                    tr.best_jpeg = buf.tobytes()
                    tr.best_meta = {
                        "conf": tr.conf,
                        "box_norm": tr.box_norm,
                        "sharpness": sharp,
                    }

            # Event: line crossed + stable (only meaningful when a virtual line is set)
            ready = False
            if self.line is not None:
                ready = (
                    tr.crossed
                    and tr.hits >= min_hits
                    and tr.state == "STABLE"
                    and tr.best_jpeg
                )
                # Parked / weak motion: stable in ROI (or full frame) without a clean cross
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

            if not ready:
                continue

            path = None
            try:
                name = f"{tr.track_id}-{int(time.time() * 1000)}.jpg".replace("/", "_")
                path = os.path.join(_pending_dir(), name)
                with open(path, "wb") as f:
                    f.write(tr.best_jpeg)
            except OSError as exc:
                logger.warning("[ANPR_READER] jpeg write failed: %s", exc)
                continue

            payload = {
                "camera_id": str(self.camera.id),
                "site_id": str(self.camera.site_id),
                "location_id": str(self.camera.site.location_id),
                "track_id": tr.track_id,
                "direction_mode": self.camera.direction or "toggle",
                "captured_at": datetime.now(dt_timezone.utc).isoformat(),
                "jpeg_path": path,
                "cross_dir": int(tr.cross_dir or 0),
                "detect_meta": tr.best_meta,
            }
            task_id = enqueue_anpr_frame(payload)
            if task_id:
                tr.state = "OCR_QUEUED"
                tr.crossed = False
                tr.best_jpeg = None
                tr.queued_at = now_m
            else:
                # backpressure — delete unused jpeg
                try:
                    os.remove(path)
                except OSError:
                    pass


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
            SiteCamera.objects.filter(is_enabled=True)
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
