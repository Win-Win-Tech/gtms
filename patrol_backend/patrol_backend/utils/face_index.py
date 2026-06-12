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


def _margin_ok(ranked: List[Tuple[str, float]], min_margin: float) -> bool:
    if len(ranked) < 2:
        return True
    best_user_id, best_dist = ranked[0]
    second_user_id, second_dist = ranked[1]
    if second_user_id == best_user_id:
        return True
    return (second_dist - best_dist) >= min_margin


def _match_encoding_tiered(
    entry: FaceLocationIndex,
    encoding: Optional[np.ndarray],
    top_k: int,
    strict_tol: float,
    relaxed_tol: float,
    min_margin: float,
) -> Tuple[str, Optional[str], Optional[float], str]:
    """
    One FAISS search; accept strict first, then relaxed on the same vector.
    Returns (verdict, user_id, distance, tier) where tier is strict|relaxed|none.
    """
    if encoding is None:
        return "no_face", None, None, "none"

    query = np.asarray(encoding, dtype=np.float32).reshape(1, -1)
    ranked = _search_top_k(entry, query, top_k)
    if not ranked:
        return "no_match", None, None, "none"

    best_user_id, best_dist = ranked[0]
    if not _margin_ok(ranked, min_margin):
        return "ambiguous", best_user_id, best_dist, "none"

    if best_dist <= strict_tol:
        return "ok", best_user_id, best_dist, "strict"
    if best_dist <= relaxed_tol:
        return "ok", best_user_id, best_dist, "relaxed"
    return "over_tolerance", best_user_id, best_dist, "none"


def identify_user_in_location(
    location_id: str,
    live_image_bytes: bytes,
    tolerance: Optional[float] = None,
) -> Tuple[Optional[str], str, Optional[float]]:
    """
    Returns (matched_user_id, code, distance):
    - code: success | face_not_detected | multiple_faces_detected |
            no_enrolled_faces | ambiguous_match | face_not_matched

    One fast encode (~1s) + one FAISS search:
      strict tolerance (0.38) then relaxed (0.45) on same vector, with margin check.
    Quality encode only when fast detect finds no face.
    """
    loc = str(location_id)
    relaxed_tol = float(tolerance if tolerance is not None else get_identify_tolerance())
    strict_tol = get_identify_tolerance_strict()
    min_margin = get_identify_min_margin()
    top_k = get_identify_top_k()
    reject_multi = getattr(settings, "FACE_IDENTIFY_REJECT_MULTIPLE_FACES", True)
    quality_retry = getattr(settings, "FACE_IDENTIFY_QUALITY_RETRY", True)

    entry = get_or_rebuild_location_index(loc)
    if entry.vectors32.shape[0] == 0:
        return _identify_fail(loc, "no_enrolled_faces", live_image_bytes)

    def _dual_match(frame: IdentifyFrameResult, tier_label: str):
        if reject_multi and frame.face_count > 1:
            return "multi", None, None
        verdict, user_id, dist, match_tier = _match_encoding_tiered(
            entry,
            frame.encoding,
            top_k,
            strict_tol,
            relaxed_tol,
            min_margin,
        )
        if verdict == "ok" and user_id:
            logger.info(
                "Face identify success (%s/%s) location=%s user=%s dist=%.4f",
                tier_label,
                match_tier,
                loc,
                user_id,
                dist,
            )
            return "ok", user_id, dist
        if verdict == "ambiguous":
            return "ambiguous", user_id, dist
        if frame.encoding is None:
            return "no_face", None, None
        return "no_match", user_id, dist

    fast_frame = extract_identify_frame(live_image_bytes, use_quality=False)
    outcome, user_id, dist = _dual_match(fast_frame, "fast")
    if outcome == "multi":
        return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)
    if outcome == "ok" and user_id:
        return user_id, "success", dist
    if outcome == "ambiguous":
        return _identify_fail(loc, "ambiguous_match", live_image_bytes, dist)
    if outcome == "no_match":
        return _identify_fail(loc, "face_not_matched", live_image_bytes, dist)

    if not quality_retry:
        return _identify_fail(loc, "face_not_detected", live_image_bytes)

    quality_frame = extract_identify_frame(live_image_bytes, use_quality=True)
    outcome, user_id, dist = _dual_match(quality_frame, "quality")
    if outcome == "multi":
        return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)
    if outcome == "ok" and user_id:
        return user_id, "success", dist
    if outcome == "ambiguous":
        return _identify_fail(loc, "ambiguous_match", live_image_bytes, dist)
    if outcome == "no_face":
        return _identify_fail(loc, "face_not_detected", live_image_bytes)
    return _identify_fail(loc, "face_not_matched", live_image_bytes, dist)
