"""
FAISS-backed face identification (1:N) for kiosk flows.

- Scoped by location for tenant safety.
- Rebuilds indexes from enrolled user.face_encoding values.
- Supports fallback linear search if FAISS is unavailable.
"""
import logging
import threading
from dataclasses import dataclass
from math import sqrt
from typing import Dict, List, Optional, Tuple

import numpy as np

from authapp.models import User
from patrol_backend.utils.face_identify_log import save_face_identify_error_image
from patrol_backend.utils.face_utils import (
    DEFAULT_FACE_TOLERANCE,
    bytes_to_encoding,
    get_face_encoding_kiosk,
)

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_INDEXES: Dict[str, "FaceLocationIndex"] = {}
_FAISS = None
_FAISS_FAILED = False


@dataclass
class FaceLocationIndex:
    location_id: str
    user_ids: List[str]
    vectors32: np.ndarray
    faiss_index: Optional[object]


def _load_faiss():
    global _FAISS, _FAISS_FAILED
    if _FAISS_FAILED:
        return None
    if _FAISS is not None:
        return _FAISS
    try:
        import faiss  # type: ignore

        _FAISS = faiss
        return faiss
    except Exception as exc:
        logger.warning("FAISS not available, using linear fallback: %s", exc)
        _FAISS_FAILED = True
        return None


def is_faiss_available() -> bool:
    return _load_faiss() is not None


def _build_vectors_for_location(location_id: str) -> Tuple[List[str], np.ndarray]:
    qs = User.objects.filter(
        location_id=location_id,
        is_deleted=False,
        is_active=True,
    ).exclude(face_encoding__isnull=True)

    user_ids: List[str] = []
    vecs: List[np.ndarray] = []
    for u in qs.only("id", "face_encoding"):
        enc = bytes_to_encoding(bytes(u.face_encoding)) if u.face_encoding else None
        if enc is None:
            continue
        user_ids.append(str(u.id))
        vecs.append(np.asarray(enc, dtype=np.float32))

    if not vecs:
        return [], np.empty((0, 128), dtype=np.float32)

    vectors32 = np.vstack(vecs).astype(np.float32)
    return user_ids, vectors32


def rebuild_location_index(location_id: str) -> int:
    user_ids, vectors32 = _build_vectors_for_location(location_id)
    faiss = _load_faiss()
    idx_obj = None
    if faiss is not None and vectors32.shape[0] > 0:
        idx_obj = faiss.IndexFlatL2(vectors32.shape[1])
        idx_obj.add(vectors32)

    entry = FaceLocationIndex(
        location_id=str(location_id),
        user_ids=user_ids,
        vectors32=vectors32,
        faiss_index=idx_obj,
    )
    with _LOCK:
        _INDEXES[str(location_id)] = entry
    logger.info("Face index rebuilt: location=%s users=%s", location_id, len(user_ids))
    return len(user_ids)


def rebuild_all_indexes() -> int:
    location_ids = (
        User.objects.filter(is_deleted=False, is_active=True, location_id__isnull=False)
        .exclude(face_encoding__isnull=True)
        .values_list("location_id", flat=True)
        .distinct()
    )
    total = 0
    for loc_id in location_ids:
        total += rebuild_location_index(str(loc_id))
    logger.info("Face index warmup complete. total_indexed_faces=%s", total)
    return total


def get_or_rebuild_location_index(location_id: str) -> FaceLocationIndex:
    with _LOCK:
        entry = _INDEXES.get(str(location_id))
    if entry is not None:
        return entry
    rebuild_location_index(str(location_id))
    with _LOCK:
        return _INDEXES.get(str(location_id)) or FaceLocationIndex(
            location_id=str(location_id),
            user_ids=[],
            vectors32=np.empty((0, 128), dtype=np.float32),
            faiss_index=None,
        )


def _identify_fail(
    location_id: str,
    code: str,
    live_image_bytes: bytes,
    distance: Optional[float] = None,
) -> Tuple[Optional[str], str, Optional[float]]:
    save_face_identify_error_image(str(location_id), code, live_image_bytes)
    return None, code, distance


def identify_user_in_location(
    location_id: str,
    live_image_bytes: bytes,
    tolerance: float = DEFAULT_FACE_TOLERANCE,
) -> Tuple[Optional[str], str, Optional[float]]:
    """
    Returns (matched_user_id, code, distance):
    - code: success | face_not_detected | no_enrolled_faces | face_not_matched

    Failed identification images are stored under media/logphoto/{date}/{location_id}/.
    """
    loc = str(location_id)
    live = get_face_encoding_kiosk(live_image_bytes)
    if live is None:
        return _identify_fail(loc, "face_not_detected", live_image_bytes)

    entry = get_or_rebuild_location_index(loc)
    if entry.vectors32.shape[0] == 0:
        return _identify_fail(loc, "no_enrolled_faces", live_image_bytes)

    query = np.asarray(live, dtype=np.float32).reshape(1, -1)
    if entry.faiss_index is not None:
        dists2, idxs = entry.faiss_index.search(query, 1)
        best_idx = int(idxs[0][0])
        if best_idx < 0 or best_idx >= len(entry.user_ids):
            return _identify_fail(loc, "face_not_matched", live_image_bytes)
        dist = float(sqrt(float(dists2[0][0])))
    else:
        # Fallback: linear L2 distance (same metric as face_recognition distance).
        deltas = entry.vectors32 - query
        d2 = np.sum(deltas * deltas, axis=1)
        best_idx = int(np.argmin(d2))
        dist = float(sqrt(float(d2[best_idx])))

    if dist > float(tolerance):
        return _identify_fail(loc, "face_not_matched", live_image_bytes, dist)

    return entry.user_ids[best_idx], "success", dist
