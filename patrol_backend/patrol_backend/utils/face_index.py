"""
FAISS-backed face identification (1:N) for kiosk flows.

- Scoped by location for tenant safety.
- Rebuilds indexes from enrolled user.face_encoding values.
- Supports fallback linear search if FAISS is unavailable.
"""
import io
import logging
import threading
import time
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
    insight_user_ids: Optional[List[str]] = None
    insight_vectors32: Optional[np.ndarray] = None
    insight_faiss_index: Optional[object] = None


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


def _build_insight_vectors_for_location(location_id: str) -> Tuple[List[str], np.ndarray]:
    from patrol_backend.utils.face_insight import (
        get_user_enrolled_insight_embedding,
        is_insightface_available,
    )

    if not is_insightface_available():
        return [], np.empty((0, 512), dtype=np.float32)

    qs = User.objects.filter(
        location_id=location_id,
        is_deleted=False,
        is_active=True,
    ).exclude(face_photo="").only("id", "face_photo")

    user_ids: List[str] = []
    vecs: List[np.ndarray] = []
    for u in qs:
        try:
            emb = get_user_enrolled_insight_embedding(u)
            if emb is not None:
                user_ids.append(str(u.id))
                vecs.append(np.asarray(emb, dtype=np.float32))
        except Exception as exc:
            logger.warning("Error fetching enrolled insight embedding user=%s: %s", u.id, exc)

    if not vecs:
        return [], np.empty((0, 512), dtype=np.float32)

    vectors32 = np.vstack(vecs).astype(np.float32)
    return user_ids, vectors32


def rebuild_location_index(location_id: str) -> int:
    # 1. dlib vectors (128-d)
    user_ids, vectors32 = _build_vectors_for_location(location_id)
    faiss = _load_faiss()
    idx_obj = None
    if faiss is not None and vectors32.shape[0] > 0:
        idx_obj = faiss.IndexFlatL2(vectors32.shape[1])
        idx_obj.add(vectors32)

    # 2. InsightFace vectors (512-d) & FAISS Inner Product index
    backend = getattr(settings, "FACE_RECOGNITION_BACKEND", "dlib")
    insight_user_ids: List[str] = []
    insight_vectors32: np.ndarray = np.empty((0, 512), dtype=np.float32)
    insight_faiss_index = None

    if backend == "insightface":
        try:
            insight_user_ids, insight_vectors32 = _build_insight_vectors_for_location(location_id)
            if faiss is not None and insight_vectors32.shape[0] > 0:
                insight_faiss_index = faiss.IndexFlatIP(insight_vectors32.shape[1])
                insight_faiss_index.add(insight_vectors32)
        except Exception as exc:
            logger.warning("Failed building InsightFace index for location=%s: %s", location_id, exc)

    entry = FaceLocationIndex(
        location_id=str(location_id),
        user_ids=user_ids,
        vectors32=vectors32,
        faiss_index=idx_obj,
        insight_user_ids=insight_user_ids,
        insight_vectors32=insight_vectors32,
        insight_faiss_index=insight_faiss_index,
    )
    with _LOCK:
        _INDEXES[str(location_id)] = entry
    logger.info(
        "Face index rebuilt: location=%s dlib_users=%s insight_users=%s",
        location_id,
        len(user_ids),
        len(insight_user_ids),
    )
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
    backend = getattr(settings, "FACE_RECOGNITION_BACKEND", "dlib")
    if entry is not None:
        if backend == "insightface" and entry.insight_user_ids is None:
            pass  # Needs rebuild to populate insightface index
        else:
            return entry
    rebuild_location_index(str(location_id))
    with _LOCK:
        return _INDEXES.get(str(location_id)) or FaceLocationIndex(
            location_id=str(location_id),
            user_ids=[],
            vectors32=np.empty((0, 128), dtype=np.float32),
            faiss_index=None,
            insight_user_ids=[],
            insight_vectors32=np.empty((0, 512), dtype=np.float32),
            insight_faiss_index=None,
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
    timing_dict: Optional[dict] = None,
) -> List[Tuple[str, float]]:
    """Return [(user_id, distance), ...] sorted best-first."""
    t_start = time.perf_counter()
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
        if timing_dict is not None:
            timing_dict["faiss_ms"] = round(
                timing_dict.get("faiss_ms", 0.0) + (time.perf_counter() - t_start) * 1000.0, 2
            )
        return results

    deltas = entry.vectors32 - query
    d2 = np.sum(deltas * deltas, axis=1)
    order = np.argsort(d2)[:k]
    results = [
        (entry.user_ids[int(i)], float(sqrt(float(d2[int(i)]))))
        for i in order
    ]
    if timing_dict is not None:
        timing_dict["faiss_ms"] = round(
            timing_dict.get("faiss_ms", 0.0) + (time.perf_counter() - t_start) * 1000.0, 2
        )
    return results


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
    timing_dict: Optional[dict] = None,
) -> Tuple[str, Optional[str], Optional[float], str]:
    """
    One FAISS search; accept strict first, then relaxed on the same vector.
    Returns (verdict, user_id, distance, tier) where tier is strict|relaxed|none.
    """
    if encoding is None:
        return "no_face", None, None, "none"

    query = np.asarray(encoding, dtype=np.float32).reshape(1, -1)
    ranked = _search_top_k(entry, query, top_k, timing_dict=timing_dict)
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


def _search_insight_top(
    entry: FaceLocationIndex,
    query_emb: np.ndarray,
    k: int = 3,
    timing_dict: Optional[dict] = None,
) -> List[Tuple[str, float]]:
    """Return [(user_id, cosine_similarity), ...] sorted highest similarity first."""
    t_start = time.perf_counter()
    if entry.insight_vectors32 is None or entry.insight_vectors32.shape[0] == 0:
        return []
    if not entry.insight_user_ids:
        return []

    n = int(entry.insight_vectors32.shape[0])
    k = min(max(1, int(k)), n)

    query = np.asarray(query_emb, dtype=np.float32).flatten()
    norm = np.linalg.norm(query)
    if norm > 0:
        query = query / norm

    if entry.insight_faiss_index is not None:
        query_2d = query.reshape(1, -1)
        sims, idxs = entry.insight_faiss_index.search(query_2d, k)
        results: List[Tuple[str, float]] = []
        for i in range(k):
            idx = int(idxs[0][i])
            if idx < 0 or idx >= len(entry.insight_user_ids):
                continue
            sim = float(sims[0][i])
            results.append((entry.insight_user_ids[idx], round(sim, 4)))
        if timing_dict is not None:
            timing_dict["faiss_ms"] = round(
                timing_dict.get("faiss_ms", 0.0) + (time.perf_counter() - t_start) * 1000.0, 2
            )
        return results

    # Linear fallback (Cosine similarity == dot product of unit vectors)
    sims = np.dot(entry.insight_vectors32, query)
    order = np.argsort(sims)[::-1][:k]
    results = [(entry.insight_user_ids[int(i)], round(float(sims[int(i)]), 4)) for i in order]
    if timing_dict is not None:
        timing_dict["faiss_ms"] = round(
            timing_dict.get("faiss_ms", 0.0) + (time.perf_counter() - t_start) * 1000.0, 2
        )
    return results


def _match_insight_top(
    entry: FaceLocationIndex,
    live_emb: np.ndarray,
    threshold: float,
    min_margin: float,
    top_k: int = 3,
    timing_dict: Optional[dict] = None,
) -> Tuple[str, Optional[str], Optional[float]]:
    """
    Returns (verdict, user_id, similarity)
    verdict: 'ok' | 'no_match' | 'ambiguous'
    """
    ranked = _search_insight_top(entry, live_emb, k=top_k, timing_dict=timing_dict)
    if not ranked:
        return "no_match", None, None

    best_user_id, best_sim = ranked[0]

    # Margin check between top 1 and top 2 if different users
    if len(ranked) >= 2:
        second_user_id, second_sim = ranked[1]
        if second_user_id != best_user_id:
            margin = best_sim - second_sim
            if margin < min_margin and best_sim >= threshold:
                return "ambiguous", best_user_id, best_sim

    if best_sim >= threshold:
        return "ok", best_user_id, best_sim

    return "no_match", best_user_id, best_sim


def _distance_to_user(entry: FaceLocationIndex, user_id: str, encoding: Optional[np.ndarray]) -> Optional[float]:
    if encoding is None:
        return None
    try:
        idx = entry.user_ids.index(str(user_id))
    except ValueError:
        return None
    ref = entry.vectors32[idx]
    query = np.asarray(encoding, dtype=np.float32)
    return float(np.linalg.norm(ref - query))


def identify_user_in_location(
    location_id: str,
    live_image_bytes: bytes,
    tolerance: Optional[float] = None,
    timing_dict: Optional[dict] = None,
) -> Tuple[Optional[str], str, Optional[float]]:
    """
    Returns (matched_user_id, code, distance):
    - code: success | face_not_detected | multiple_faces_detected |
            no_enrolled_faces | ambiguous_match | face_not_matched

    One fast encode (~1s) + one FAISS search:
      strict tolerance (0.38) then relaxed (0.45) on same vector, with margin check.
    Quality encode only when fast detect finds no face.
    """
    t_id_start = time.perf_counter()
    loc = str(location_id)
    relaxed_tol = float(tolerance if tolerance is not None else get_identify_tolerance())
    strict_tol = get_identify_tolerance_strict()
    min_margin = get_identify_min_margin()
    top_k = get_identify_top_k()
    reject_multi = getattr(settings, "FACE_IDENTIFY_REJECT_MULTIPLE_FACES", True)
    quality_retry = getattr(settings, "FACE_IDENTIFY_QUALITY_RETRY", True)
    second_pass_verify = getattr(settings, "FACE_IDENTIFY_SECOND_PASS_VERIFY", True)
    second_pass_relaxed_only = getattr(settings, "FACE_IDENTIFY_SECOND_PASS_RELAXED_ONLY", True)
    second_pass_use_quality = getattr(settings, "FACE_IDENTIFY_SECOND_PASS_USE_QUALITY", True)
    second_pass_tol = float(
        getattr(settings, "FACE_IDENTIFY_SECOND_PASS_TOLERANCE", strict_tol)
    )

    entry = get_or_rebuild_location_index(loc)
    total_enrolled = entry.vectors32.shape[0] + (
        entry.insight_vectors32.shape[0] if entry.insight_vectors32 is not None else 0
    )
    if total_enrolled == 0:
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return _identify_fail(loc, "no_enrolled_faces", live_image_bytes)

    # -------------------------------------------------------------
    # FAST PATH: InsightFace ONNX Engine (1:N search with FAISS IP)
    # -------------------------------------------------------------
    backend = getattr(settings, "FACE_RECOGNITION_BACKEND", "dlib")
    if backend == "insightface" and entry.insight_vectors32 is not None and entry.insight_vectors32.shape[0] > 0:
        try:
            from patrol_backend.utils.face_insight import (
                extract_insightface_embedding,
                is_insightface_available,
            )
            if is_insightface_available():
                live_emb, face_count = extract_insightface_embedding(live_image_bytes, timing_dict=timing_dict)
                if reject_multi and face_count > 1:
                    if timing_dict is not None:
                        timing_dict["engine"] = "insightface"
                        timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
                    return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)

                if face_count == 0 or live_emb is None:
                    if not quality_retry or entry.vectors32.shape[0] == 0:
                        if timing_dict is not None:
                            timing_dict["engine"] = "insightface"
                            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
                        return _identify_fail(loc, "face_not_detected", live_image_bytes)
                    # If quality_retry is enabled and dlib vectors exist, proceed to dlib quality retry below
                else:
                    thresh = float(getattr(settings, "FACE_INSIGHT_SIMILARITY_THRESHOLD", 0.40))
                    min_margin_insight = float(getattr(settings, "FACE_INSIGHT_IDENTIFY_MIN_MARGIN", 0.05))
                    verdict, user_id, sim = _match_insight_top(
                        entry,
                        live_emb,
                        threshold=thresh,
                        min_margin=min_margin_insight,
                        top_k=top_k,
                        timing_dict=timing_dict,
                    )
                    if timing_dict is not None:
                        timing_dict["engine"] = "insightface"
                        timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)

                    if verdict == "ok" and user_id:
                        logger.info("Face identify success (insightface) location=%s user=%s sim=%.4f", loc, user_id, sim)
                        return user_id, "success", sim
                    if verdict == "ambiguous":
                        return _identify_fail(loc, "ambiguous_match", live_image_bytes, sim)
                    # If no match and all enrolled users in this location are indexed by insightface (or dlib has none)
                    num_dlib = entry.vectors32.shape[0]
                    num_insight = len(entry.insight_user_ids or [])
                    if num_insight >= num_dlib or num_dlib == 0:
                        return _identify_fail(loc, "face_not_matched", live_image_bytes, sim)
                    # Otherwise fall through to dlib check for users who only have 128-d dlib vectors
        except Exception as exc:
            logger.warning("InsightFace 1:N identification failed, falling back to dlib: %s", exc)

    if entry.vectors32.shape[0] == 0:
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return _identify_fail(loc, "no_enrolled_faces", live_image_bytes)

    def _dual_match(frame: IdentifyFrameResult, tier_label: str):
        if reject_multi and frame.face_count > 1:
            return "multi", None, None, "none"
        verdict, user_id, dist, match_tier = _match_encoding_tiered(
            entry,
            frame.encoding,
            top_k,
            strict_tol,
            relaxed_tol,
            min_margin,
            timing_dict=timing_dict,
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
            return "ok", user_id, dist, match_tier
        if verdict == "ambiguous":
            return "ambiguous", user_id, dist, "none"
        if frame.encoding is None:
            return "no_face", None, None, "none"
        return "no_match", user_id, dist, "none"

    fast_frame = extract_identify_frame(live_image_bytes, use_quality=False, timing_dict=timing_dict)
    outcome, user_id, dist, match_tier = _dual_match(fast_frame, "fast")
    if outcome == "multi":
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)
    if outcome == "ok" and user_id:
        needs_second_pass = second_pass_verify and (
            (not second_pass_relaxed_only) or match_tier == "relaxed"
        )
        if needs_second_pass:
            verify_frame = (
                extract_identify_frame(live_image_bytes, use_quality=True, timing_dict=timing_dict)
                if second_pass_use_quality
                else fast_frame
            )
            if reject_multi and verify_frame.face_count > 1:
                if timing_dict is not None:
                    timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
                return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)
            verify_dist = _distance_to_user(entry, user_id, verify_frame.encoding)
            if verify_dist is None or verify_dist > second_pass_tol:
                logger.info(
                    "Face identify second-pass rejected location=%s user=%s dist=%s tol=%.4f tier=%s",
                    loc,
                    user_id,
                    f"{verify_dist:.4f}" if verify_dist is not None else "none",
                    second_pass_tol,
                    match_tier,
                )
                if timing_dict is not None:
                    timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
                return _identify_fail(
                    loc,
                    "face_not_matched",
                    live_image_bytes,
                    verify_dist if verify_dist is not None else dist,
                )
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return user_id, "success", dist
    if outcome == "ambiguous":
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return _identify_fail(loc, "ambiguous_match", live_image_bytes, dist)
    if outcome == "no_match":
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return _identify_fail(loc, "face_not_matched", live_image_bytes, dist)

    if not quality_retry:
        if timing_dict is not None:
            timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
        return _identify_fail(loc, "face_not_detected", live_image_bytes)

    quality_frame = extract_identify_frame(live_image_bytes, use_quality=True, timing_dict=timing_dict)
    outcome, user_id, dist, _match_tier = _dual_match(quality_frame, "quality")
    if timing_dict is not None:
        timing_dict["face_identify_total_ms"] = round((time.perf_counter() - t_id_start) * 1000.0, 2)
    if outcome == "multi":
        return _identify_fail(loc, "multiple_faces_detected", live_image_bytes)
    if outcome == "ok" and user_id:
        return user_id, "success", dist
    if outcome == "ambiguous":
        return _identify_fail(loc, "ambiguous_match", live_image_bytes, dist)
    if outcome == "no_face":
        return _identify_fail(loc, "face_not_detected", live_image_bytes)
    return _identify_fail(loc, "face_not_matched", live_image_bytes, dist)
