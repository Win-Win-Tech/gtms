"""
Face CCTV reader — watch Face cameras, find every face, follow each person.

Phase A1 (this file): detection + tracking only. Each finished pass is logged
(and optionally its best face crop saved) so camera placement and server load can
be checked on live before recognition / attendance are switched on.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Dict, Optional, Tuple

import cv2
from django.conf import settings

from visitor.anpr.reader import (
    _FrameGrabber,
    _changed_fraction,
    _db_keepalive,
    _frame_fingerprint,
    _is_mysql_gone_away,
    _motion_thumb,
    _open_rtsp,
    _release_db_connection,
)

from . import settings_helpers as face_settings
from .detector import FaceDetector
from .tracker import FaceTrack, FaceTracker

logger = logging.getLogger(__name__)

FREEZE_RECONNECT_SEC = 20.0
STATUS_LOG_SEC = 60.0
GRABBER_STOP_WAIT_SEC = 5.0


def _camera_config_key(camera) -> Tuple:
    """Fields that need a fresh worker (new stream / site / features) when changed."""
    return (
        str(camera.id),
        str(getattr(camera, "rtsp_url", "") or ""),
        str(getattr(camera, "direction", "") or "toggle"),
        str(getattr(camera, "site_id", "") or ""),
        str(getattr(getattr(camera, "site", None), "location_id", "") or ""),
        tuple(sorted(getattr(camera, "features", None) or [])),
    )


def load_face_cameras():
    """Enabled Face cameras with face attendance on. Always releases the DB connection."""
    from django.db.utils import OperationalError as DjOperationalError
    from scheduler.models import SiteCamera

    def _fetch():
        qs = (
            SiteCamera.objects.filter(
                is_enabled=True,
                camera_type=SiteCamera.CameraType.FACE,
            )
            .select_related("site")
            .order_by("sort_order", "name")
        )
        cams = [c for c in qs if c.has_feature(SiteCamera.Feature.FACE_ATTENDANCE)]
        return cams[: face_settings.max_cameras()]

    try:
        try:
            return _fetch()
        except Exception as exc:
            if not isinstance(exc, DjOperationalError) and not _is_mysql_gone_away(exc):
                raise
            logger.warning("[FACE_CCTV] DB error on camera refresh (%s) — retry once", type(exc).__name__)
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


def _debug_dir() -> str:
    root = getattr(settings, "MEDIA_ROOT", None) or "/tmp"
    path = os.path.join(str(root), "face_cctv_debug")
    os.makedirs(path, exist_ok=True)
    return path


def _prune_debug_dir(path: str, keep: int) -> None:
    try:
        files = sorted(
            (os.path.join(path, n) for n in os.listdir(path) if n.endswith(".jpg")),
            key=os.path.getmtime,
        )
        for old in files[:-keep]:
            os.remove(old)
    except OSError as exc:
        logger.debug("[FACE_CCTV] debug prune: %s", exc)


class FaceCameraWorker:
    def __init__(self, camera, detector: FaceDetector):
        self.camera = camera
        self.config_key = _camera_config_key(camera)
        self.detector = detector
        self.tracker = self._new_tracker()
        self.grabber: Optional[_FrameGrabber] = None
        self.reconnect_at = 0.0
        self._last_seq = 0
        self._last_new_frame_at = 0.0
        self._last_frame_fp = None
        self._frame_fp_same_since = 0.0
        self._motion_ref = None
        self._motion_until = 0.0
        self._last_detect_run = 0.0
        self._last_status_log = time.monotonic()
        self._reset_stats()

    def _new_tracker(self) -> FaceTracker:
        return FaceTracker(
            camera_key=str(self.camera.id)[:8],
            min_hits=face_settings.min_track_hits(),
            lost_sec=face_settings.track_lost_sec(),
            min_face_px=face_settings.min_face_px(),
        )

    def _reset_stats(self) -> None:
        self.stats = {
            "ticks": 0,
            "detects": 0,
            "idle_skips": 0,
            "detect_ms": 0.0,
            "max_faces": 0,
            "people": 0,
            "good_faces": 0,
            "small_faces": 0,
        }

    # ---- RTSP -------------------------------------------------------------

    def close(self) -> None:
        for tr in self.tracker.flush():
            self._on_track_finished(tr)
        self._drop_capture(0.0, wait=GRABBER_STOP_WAIT_SEC)

    def _drop_capture(self, reconnect_in: float = 3.0, *, wait: float = 0.0) -> None:
        if self.grabber is not None:
            self.grabber.stop(wait=wait)
            self.grabber = None
        self._last_seq = 0
        self._last_frame_fp = None
        self._frame_fp_same_since = 0.0
        self.reconnect_at = time.monotonic() + max(0.0, reconnect_in)

    def ensure_capture(self) -> bool:
        if self.grabber is not None:
            return True
        now = time.monotonic()
        if now < self.reconnect_at:
            return False
        logger.info("[FACE_CCTV] connecting camera=%s name=%s", self.camera.id, self.camera.name)
        cap = _open_rtsp(self.camera.rtsp_url)
        if not cap:
            self.reconnect_at = now + 5.0
            logger.warning("[FACE_CCTV] connect failed camera=%s", self.camera.id)
            return False
        self.grabber = _FrameGrabber(cap, str(self.camera.id))
        self._last_new_frame_at = now
        return True

    def read_latest(self):
        if not self.ensure_capture():
            return None
        now_m = time.monotonic()
        if self.grabber.failed:
            logger.warning("[FACE_CCTV] read fail camera=%s — reconnect", self.camera.id)
            self._drop_capture(3.0)
            return None
        frame, seq = self.grabber.latest()
        if frame is None or seq == self._last_seq:
            if now_m - self._last_new_frame_at >= FREEZE_RECONNECT_SEC:
                logger.warning("[FACE_CCTV] no new frame camera=%s — reconnect", self.camera.id)
                self._drop_capture(2.0)
            return None
        self._last_seq = seq
        self._last_new_frame_at = now_m

        fp = _frame_fingerprint(frame)
        if fp is not None:
            if fp == self._last_frame_fp:
                if self._frame_fp_same_since <= 0:
                    self._frame_fp_same_since = now_m
                elif now_m - self._frame_fp_same_since >= FREEZE_RECONNECT_SEC:
                    logger.warning("[FACE_CCTV] frozen stream camera=%s — reconnect", self.camera.id)
                    self._drop_capture(2.0)
                    return None
            else:
                self._last_frame_fp = fp
                self._frame_fp_same_since = 0.0
        return frame

    # ---- per frame --------------------------------------------------------

    def _should_detect(self, frame, now_m: float) -> bool:
        """Run the detector while the picture changes or a face is being followed."""
        if not face_settings.motion_gate():
            return True
        thumb = _motion_thumb(frame)
        ref = self._motion_ref
        moved = (
            ref is None
            or ref.shape != thumb.shape
            or _changed_fraction(ref, thumb) >= face_settings.motion_min_area()
        )
        if moved:
            self._motion_until = now_m + face_settings.motion_hold_sec()
        if (
            moved
            or now_m < self._motion_until
            or self.tracker.tracks
            or now_m - self._last_detect_run >= face_settings.idle_detect_sec()
        ):
            self._motion_ref = thumb
            self._last_detect_run = now_m
            return True
        self.stats["idle_skips"] += 1
        return False

    def process_tick(self, frame, now_m: Optional[float] = None) -> None:
        now_m = time.monotonic() if now_m is None else now_m
        self.stats["ticks"] += 1
        if not self._should_detect(frame, now_m):
            finished = self.tracker.expire(now_m)
        else:
            t0 = time.perf_counter()
            faces = self.detector.detect(frame)
            self.stats["detects"] += 1
            self.stats["detect_ms"] += (time.perf_counter() - t0) * 1000.0
            self.stats["max_faces"] = max(self.stats["max_faces"], len(faces))
            finished = self.tracker.update(faces, frame, now_m)
        for tr in finished:
            self._on_track_finished(tr)
        self._maybe_log_status(now_m)

    def _on_track_finished(self, tr: FaceTrack) -> None:
        if not self.tracker.is_confirmed(tr):
            return
        self.stats["people"] += 1
        good = tr.best_crop is not None
        self.stats["good_faces" if good else "small_faces"] += 1
        logger.info(
            "[FACE_CCTV] person camera=%s track=%s hits=%d seconds=%.1f face_px=%d score=%.2f recognisable=%s",
            self.camera.id,
            tr.track_id,
            tr.hits,
            tr.last_seen - tr.first_seen,
            tr.best_face_px or tr.box.width,
            tr.best_score or tr.box.score,
            "yes" if good else "no (face too small)",
        )
        if good and face_settings.save_debug_crops():
            self._save_debug_crop(tr)

    def _save_debug_crop(self, tr: FaceTrack) -> None:
        path = _debug_dir()
        name = f"{str(self.camera.id)[:8]}_{time.strftime('%Y%m%d-%H%M%S')}_{tr.track_id}.jpg"
        cv2.imwrite(os.path.join(path, name), tr.best_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        _prune_debug_dir(path, face_settings.debug_keep())

    def _maybe_log_status(self, now_m: float) -> None:
        if now_m - self._last_status_log >= STATUS_LOG_SEC:
            self.log_status(now_m)

    def log_status(self, now_m: float) -> None:
        s = self.stats
        logger.info(
            "[FACE_CCTV] status camera=%s name=%s ticks=%d detects=%d idle_skips=%d "
            "detect_ms=%d max_faces=%d people=%d recognisable=%d too_small=%d tracks_now=%d",
            self.camera.id,
            self.camera.name,
            s["ticks"],
            s["detects"],
            s["idle_skips"],
            int(s["detect_ms"] / s["detects"]) if s["detects"] else 0,
            s["max_faces"],
            s["people"],
            s["good_faces"],
            s["small_faces"],
            len(self.tracker.tracks),
        )
        self._last_status_log = now_m
        self._reset_stats()


def run_face_reader_loop(*, once: bool = False) -> None:
    if not face_settings.enabled():
        logger.error("[FACE_CCTV] FACE_CCTV_ENABLED is false — set env/settings to start")
        return
    cv2.setNumThreads(face_settings.cv_threads())
    detector = FaceDetector()
    if not detector.available:
        logger.error("[FACE_CCTV] face detector model could not be loaded — stopping")
        return

    interval = 1.0 / face_settings.fps()
    refresh_every = float(face_settings.camera_refresh_sec())
    keepalive_every = float(os.environ.get("FACE_CCTV_DB_KEEPALIVE_SEC", "60"))
    workers: Dict[str, FaceCameraWorker] = {}

    def refresh_workers() -> None:
        nonlocal workers
        try:
            cams = load_face_cameras()
        except Exception:
            logger.exception("[FACE_CCTV] camera refresh failed (DB?) — keeping current cameras")
            return
        fresh: Dict[str, FaceCameraWorker] = {}
        for cam in cams:
            key = str(cam.id)
            old = workers.pop(key, None)
            if old is not None and old.config_key == _camera_config_key(cam):
                fresh[key] = old
                continue
            if old is not None:
                old.close()
            fresh[key] = FaceCameraWorker(cam, detector)
            logger.info("[FACE_CCTV] watching camera=%s name=%s", cam.id, cam.name)
        for old in workers.values():
            logger.info("[FACE_CCTV] stopped camera=%s", old.camera.id)
            old.close()
        workers = fresh
        if not workers:
            logger.info("[FACE_CCTV] no enabled Face cameras with face attendance")

    refresh_workers()
    last_refresh = last_keepalive = time.monotonic()
    logger.info(
        "[FACE_CCTV] started fps=%s detect_max_side=%s min_face_px=%s cameras=%d",
        face_settings.fps(),
        face_settings.detect_max_side(),
        face_settings.min_face_px(),
        len(workers),
    )

    once_deadline = time.monotonic() + 15.0
    once_done: set = set()
    try:
        while True:
            loop_start = time.monotonic()
            if loop_start - last_keepalive >= keepalive_every:
                _db_keepalive()
                last_keepalive = loop_start
            if loop_start - last_refresh >= refresh_every:
                refresh_workers()
                last_refresh = loop_start

            for key, w in workers.items():
                frame = w.read_latest()
                if frame is None:
                    continue
                try:
                    w.process_tick(frame)
                    once_done.add(key)
                except Exception:
                    logger.exception("[FACE_CCTV] tick failed camera=%s", w.camera.id)

            if once and (len(once_done) >= len(workers) or loop_start >= once_deadline):
                return

            sleep_for = interval - (time.monotonic() - loop_start)
            if sleep_for > 0:
                time.sleep(sleep_for)
    finally:
        for w in workers.values():
            w.close()
            w.log_status(time.monotonic())
