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
from django.conf import settings

from authapp.models import User
from patrol_backend.utils.face_identify_log import save_face_identify_error_image
from patrol_backend.utils.face_utils import (
    IdentifyFrameResult,
    bytes_to_encoding,
    extract_identify_frame,
    get_identify_min_margin,
    get_identify_tolerance,
    get_identify_tolerance_strict,
    get_identify_top_k,
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


def _search_top_k(
    entry: FaceLocationIndex,
    query: np.ndarray,
    k: int,
) -> List[Tuple[str, float]]:
    """Return [(user_id, distance), ...] sorted best-first."""
    n = int(entry.vectors32.shape[0])
    if n <= 0:
        return []
    k = min(max(1, int(k)), n)
    if entry.faiss_index is not None:
        dists2, idxs = entry.faiss_index.search(query, k)
        results: List[Tuple[str, float]] = []
        for i in range(k):
            idx = int(idxs[0][i])
            if idx < 0 or idx >= len(entry.user_ids):
                continue
            dist = float(sqrt(float(dists2[0][i])))
            results.append((entry.user_ids[idx], dist))
        return results

    deltas = entry.vectors32 - query
    d2 = np.sum(deltas * deltas, axis=1)
    order = np.argsort(d2)[:k]
    return [
        (entry.user_ids[int(i)], float(sqrt(float(d2[int(i)]))))
        for i in order
    ]


def _evaluate_ranked(
    ranked: List[Tuple[str, float]],
    tolerance: float,
    min_margin: float,
) -> Tuple[str, Optional[str], Optional[float]]:
    """
    Returns (verdict, user_id, distance).
    verdict: ok | over_tolerance | ambiguous | no_match
    """
    if not ranked:
        return "no_match", None, None

    best_user_id, best_dist = ranked[0]
    if best_dist > tolerance:
        return "over_tolerance", best_user_id, best_dist

    if len(ranked) >= 2:
        second_user_id, second_dist = ranked[1]
        margin = second_dist - best_dist
        if second_user_id != best_user_id and margin < min_margin:
            return "ambiguous", best_user_id, best_dist

    return "ok", best_user_id, best_dist


def _match_from_frame(
    entry: FaceLocationIndex,
    frame: IdentifyFrameResult,
    top_k: int,
    tolerance: float,
    min_margin: float,
) -> Tuple[str, Optional[str], Optional[float]]:
    if frame.encoding is None:
        return "no_face", None, None

    query = np.asarray(frame.encoding, dtype=np.float32).reshape(1, -1)
    ranked = _search_top_k(entry, query, top_k)
    return _evaluate_ranked(ranked, tolerance, min_margin)


def _should_quality_retry(verdict: str, distance: Optional[float], relaxed_tol: float) -> bool:
    if not getattr(settings, "FACE_IDENTIFY_QUALITY_RETRY", True):
        return False
    if verdict == "no_face":
        return True
    if verdict == "ambiguous":
        return True
    if verdict == "over_tolerance" and distance is not None and distance <= relaxed_tol + 0.08:
        return True
    return False


def identify_user_in_location(
    location_id: str,
    live_image_bytes: bytes,
    tolerance: Optional[float] = None,
) -> Tuple[Optional[str], str, Optional[float]]:
    """
    Returns (matched_user_id, code, distance):
    - code: success | face_not_detected | multiple_faces_detected |
            no_enrolled_faces | ambiguous_match | face_not_matched

    Fast path first (~1s); quality retry only for borderline / no-face cases.
    """
    loc = str(location_id)
    relaxed_tol = float(tolerance if tolerance is not None else get_identify_tolerance())
    strict_tol = get_identify_tolerance_strict()
    min_margin = get_identify_min_margin()
    top_k = get_identify_top_k()
    reject_multi = getattr(settings, "FACE_IDENTIFY_REJECT_MULTIPLE_FACES", True)

    entry = get_or_rebuild_location_index(loc)
    if entry.vectors32.shape[0] == 0:
        return _identify_fail(loc, "no_enrolled_faces", live_image_bytes)

    # --- Tier 1: fast detect (640px, same as kiosk) ---
    fast_frame = extract_identify_frame(live_image_bytes, use_quality=False)
    if reject_multi and fast_frame.face_count > 1:
        logger.info(
            "Face identify rejected: multiple_faces_detected location=%s count=%s",
            loc,
            fast_frame.face_count,
        )
        return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)

    verdict, user_id, dist = _match_from_frame(
        entry, fast_frame, top_k, strict_tol, min_margin
    )
    if verdict == "ok" and user_id:
        logger.info(
            "Face identify success (fast) location=%s user=%s dist=%.4f",
            loc,
            user_id,
            dist,
        )
        return user_id, "success", dist

    # --- Tier 2: quality retry for haircut / blur / borderline only ---
    need_retry = _should_quality_retry(verdict, dist, relaxed_tol)
    if need_retry:
        quality_frame = extract_identify_frame(live_image_bytes, use_quality=True)
        if reject_multi and quality_frame.face_count > 1:
            return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)

        q_verdict, q_user_id, q_dist = _match_from_frame(
            entry, quality_frame, top_k, relaxed_tol, min_margin
        )
        if q_verdict == "ok" and q_user_id:
            logger.info(
                "Face identify success (quality) location=%s user=%s dist=%.4f",
                loc,
                q_user_id,
                q_dist,
            )
            return q_user_id, "success", q_dist
        if q_verdict == "ambiguous":
            return _identify_fail(loc, "ambiguous_match", live_image_bytes, q_dist)
        if quality_frame.encoding is None and fast_frame.encoding is None:
            return _identify_fail(loc, "face_not_detected", live_image_bytes)
        return _identify_fail(loc, "face_not_matched", live_image_bytes, q_dist or dist)

    if fast_frame.encoding is None:
        return _identify_fail(loc, "face_not_detected", live_image_bytes)
    if verdict == "ambiguous":
        return _identify_fail(loc, "ambiguous_match", live_image_bytes, dist)
    return _identify_fail(loc, "face_not_matched", live_image_bytes, dist)
