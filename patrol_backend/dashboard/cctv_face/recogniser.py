"""
Face CCTV Recogniser — Phase A2: Recognition (dry run).

For each finished person pass:
1. Extract 128-d face embedding from best face crop using dlib / face_recognition.
2. Search location's FAISS face index for enrolled users.
3. Apply strict tolerance (default 0.38) and relaxed tolerance (default 0.45).
4. Save an event snapshot (optional / configurable).
5. Record CctvFaceEvent in the database without marking attendance yet (dry run).
"""

from __future__ import annotations

import logging
import os
import time
from typing import Optional, Tuple

import cv2
import numpy as np
from django.conf import settings
from django.utils import timezone

from . import settings_helpers as face_settings
from .tracker import FaceTrack

logger = logging.getLogger(__name__)


def encode_face_crop(
    crop_bgr: np.ndarray,
    box_in_crop: Optional[Tuple[int, int, int, int]] = None,
) -> Optional[np.ndarray]:
    """
    Extract 128-d face embedding vector from an OpenCV BGR crop.
    Uses box_in_crop (x1, y1, x2, y2) to skip HOG face detection and run direct shape+ResNet.
    """
    if crop_bgr is None or crop_bgr.size == 0:
        return None
    try:
        import face_recognition

        rgb = cv2.cvtColor(crop_bgr, cv2.COLOR_BGR2RGB)
        known_locs = None
        if box_in_crop is not None:
            x1, y1, x2, y2 = box_in_crop
            # face_recognition expects CSS format: (top, right, bottom, left) -> (y1, x2, y2, x1)
            h, w = rgb.shape[:2]
            top = max(0, min(h - 1, y1))
            bottom = max(0, min(h, y2))
            left = max(0, min(w - 1, x1))
            right = max(0, min(w, x2))
            if bottom > top and right > left:
                known_locs = [(top, right, bottom, left)]

        encodings = face_recognition.face_encodings(
            rgb,
            known_face_locations=known_locs,
            num_jitters=0,
        )
        if not encodings and known_locs is not None:
            # Fallback to general detector if box-guided encoding produced no landmarks
            encodings = face_recognition.face_encodings(rgb, num_jitters=0)

        if encodings:
            return np.asarray(encodings[0], dtype=np.float32)
    except Exception as exc:
        logger.warning("[FACE_CCTV] face encoding failed: %s", exc)
    return None


import uuid


def _as_uuid_or_none(val) -> Optional[uuid.UUID]:
    if not val:
        return None
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError):
        return None


def match_user_in_location(
    location_id: str,
    encoding: np.ndarray,
    *,
    strict_tol: Optional[float] = None,
    relaxed_tol: Optional[float] = None,
) -> Tuple[str, Optional[str], Optional[float]]:
    """
    Query location FAISS face index.
    Returns (verdict, matched_user_id, distance).
    verdict: 'recognised' | 'unknown'
    """
    from patrol_backend.utils.face_index import (
        _match_encoding_tiered,
        get_identify_min_margin,
        get_identify_top_k,
        get_or_rebuild_location_index,
    )

    loc_uuid = _as_uuid_or_none(location_id)
    if loc_uuid is None:
        return "unknown", None, None

    entry = get_or_rebuild_location_index(str(loc_uuid))
    if entry.vectors32.shape[0] == 0:
        return "unknown", None, None

    s_tol = face_settings.strict_tolerance() if strict_tol is None else strict_tol
    r_tol = face_settings.relaxed_tolerance() if relaxed_tol is None else relaxed_tol
    min_margin = get_identify_min_margin()
    top_k = get_identify_top_k()

    verdict, user_id, dist, _tier = _match_encoding_tiered(
        entry,
        encoding,
        top_k=top_k,
        strict_tol=s_tol,
        relaxed_tol=r_tol,
        min_margin=min_margin,
    )

    if verdict == "ok" and user_id:
        return "recognised", str(user_id), float(dist)
    return "unknown", (str(user_id) if user_id else None), (float(dist) if dist is not None else None)


def save_event_snapshot(camera, track: FaceTrack, subfolder: str = "face_cctv_events") -> str:
    """Save face crop to MEDIA_ROOT/face_cctv_events/YYYY-MM-DD/... returning relative path."""
    if track.best_crop is None:
        return ""
    try:
        media_root = getattr(settings, "MEDIA_ROOT", None) or "/tmp"
        date_str = timezone.now().strftime("%Y-%m-%d")
        folder = os.path.join(str(media_root), subfolder, date_str)
        os.makedirs(folder, exist_ok=True)
        filename = f"{str(camera.id)[:8]}_{time.strftime('%H%M%S')}_{track.track_id}.jpg"
        full_path = os.path.join(folder, filename)
        cv2.imwrite(full_path, track.best_crop, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
        return f"{subfolder}/{date_str}/{filename}"
    except Exception as exc:
        logger.warning("[FACE_CCTV] failed to save event snapshot: %s", exc)
        return ""


def record_face_event(
    camera,
    track: FaceTrack,
    result: str,
    user_id: Optional[str] = None,
    distance: Optional[float] = None,
    snapshot_path: str = "",
) -> Optional[object]:
    """Persist CctvFaceEvent row to database. Resilient against connection drops."""
    from scheduler.models import CctvFaceEvent
    from visitor.anpr.reader import _release_db_connection

    try:
        site_uuid = _as_uuid_or_none(getattr(camera, "site_id", None))
        cam_uuid = _as_uuid_or_none(getattr(camera, "id", None))
        user_uuid = _as_uuid_or_none(user_id)
        camera_name = str(getattr(camera, "name", "") or "")

        event = CctvFaceEvent.objects.create(
            site_id=site_uuid,
            camera_id=cam_uuid,
            camera_name=camera_name,
            user_id=user_uuid,
            distance=distance,
            result=result,
            snapshot=snapshot_path,
            track_id=track.track_id,
            face_px=track.best_face_px or (track.box.width if track.box else 0),
            detect_score=track.best_score or (track.box.score if track.box else 0.0),
        )
        return event
    except Exception as exc:
        logger.warning("[FACE_CCTV] failed to record CctvFaceEvent: %s", exc)
        return None
    finally:
        _release_db_connection()


def process_finished_track(camera, track: FaceTrack) -> Tuple[str, Optional[str], Optional[float]]:
    """
    Main Phase A2 entrypoint for a finished person track.
    Returns (result, user_id, distance).
    """
    if track.best_crop is None:
        result = "too_small"
        user_id = None
        dist = None
        snapshot = ""
        logger.info(
            "[FACE_CCTV] pass camera=%s track=%s result=too_small hits=%d face_px=%d",
            camera.id,
            track.track_id,
            track.hits,
            track.best_face_px,
        )
    else:
        location_id = getattr(getattr(camera, "site", None), "location_id", None)
        encoding = encode_face_crop(track.best_crop, track.best_box_in_crop)
        if encoding is None:
            result = "unknown"
            user_id = None
            dist = None
        else:
            verdict, user_id, dist = match_user_in_location(str(location_id or ""), encoding)
            result = "recognised" if verdict == "recognised" else "unknown"

        # Save snapshot if recognized, or if unknown crop saving is enabled
        snapshot = ""
        if result == "recognised" or face_settings.save_unknown_crops() or face_settings.save_debug_crops():
            snapshot = save_event_snapshot(camera, track)

        user_display = f"user={user_id}" if user_id else "unknown"
        dist_display = f"dist={dist:.4f}" if dist is not None else "no_match"
        logger.info(
            "[FACE_CCTV] pass camera=%s track=%s result=%s %s %s face_px=%d score=%.2f hits=%d",
            camera.id,
            track.track_id,
            result,
            user_display,
            dist_display,
            track.best_face_px,
            track.best_score,
            track.hits,
        )

    # Persist event (dry run)
    record_face_event(
        camera=camera,
        track=track,
        result=result,
        user_id=user_id,
        distance=dist,
        snapshot_path=snapshot,
    )

    return result, user_id, dist
