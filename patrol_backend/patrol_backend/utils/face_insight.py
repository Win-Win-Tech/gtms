"""
InsightFace (SCRFD + ArcFace) ONNX engine for ultra-fast, high-accuracy face recognition.
Runs on CPU via ONNX Runtime with AVX2 SIMD optimizations (~30-50ms total).

Backward-compatible:
- If insightface is not installed or models are missing, falls back cleanly to face_recognition (dlib).
- Does not break production 128-d dlib vectors.
"""
from __future__ import annotations

import io
import logging
import time
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_app = None
_app_failed = False
_app_lock = None

DEFAULT_COSINE_THRESHOLD = 0.40  # Standard ArcFace threshold for buffalo_s/buffalo_l


def is_insightface_available() -> bool:
    try:
        import insightface  # noqa: F401
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _get_app_lock():
    global _app_lock
    if _app_lock is None:
        import threading
        _app_lock = threading.Lock()
    return _app_lock


def get_face_analysis_app(model_name: str = "buffalo_s"):
    """
    Lazy load singleton FaceAnalysis app.
    buffalo_s: SCRFD-500M detector + MobileFaceNet ArcFace embedding (~30ms CPU).
    buffalo_l: SCRFD-10G detector + ResNet50 ArcFace embedding (~90ms CPU).
    """
    global _app, _app_failed
    if _app_failed:
        return None
    if _app is not None:
        return _app

    with _get_app_lock():
        if _app is not None:
            return _app
        if not is_insightface_available():
            _app_failed = True
            return None
        try:
            from insightface.app import FaceAnalysis

            app = FaceAnalysis(
                name=model_name,
                providers=["CPUExecutionProvider"],
                allowed_modules=["detection", "recognition"],
            )
            # ctx_id=0 for GPU, -1 for CPU; det_size=(640, 640)
            app.prepare(ctx_id=-1, det_size=(640, 640))
            _app = app
            logger.info("InsightFace FaceAnalysis (%s) initialized on CPU.", model_name)
            return _app
        except Exception as exc:
            logger.warning("Failed to initialize InsightFace FaceAnalysis: %s", exc)
            _app_failed = True
            return None


def extract_insightface_embedding(
    image_bytes_or_rgb: bytes | np.ndarray,
    timing_dict: Optional[dict] = None,
    model_name: str = "buffalo_s",
) -> Tuple[Optional[np.ndarray], int]:
    """
    Detect faces and extract 512-d normalized embedding using InsightFace.
    Returns: (normalized_embedding_or_None, face_count)
    """
    app = get_face_analysis_app(model_name=model_name)
    if app is None:
        return None, 0

    t_start = time.perf_counter()

    # Load into RGB numpy array
    if isinstance(image_bytes_or_rgb, bytes):
        from patrol_backend.utils.face_utils import load_image_rgb_array
        # InsightFace works with BGR or RGB; FaceAnalysis expects BGR (cv2 convention)
        img_rgb = load_image_rgb_array(image_bytes_or_rgb, max_dimension=640, timing_dict=timing_dict)
    else:
        img_rgb = image_bytes_or_rgb

    if img_rgb is None:
        return None, 0

    # Convert RGB to BGR for InsightFace
    import cv2
    img_bgr = cv2.cvtColor(img_rgb, cv2.COLOR_RGB2BGR)

    t_det_start = time.perf_counter()
    faces = app.get(img_bgr)
    t_det_ms = (time.perf_counter() - t_det_start) * 1000.0

    face_count = len(faces)
    if timing_dict is not None:
        timing_dict["detect_ms"] = round(timing_dict.get("detect_ms", 0.0) + t_det_ms, 2)
        timing_dict["face_count"] = face_count

    if not faces:
        return None, 0

    # Pick the largest face in the image
    faces.sort(key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]), reverse=True)
    best_face = faces[0]

    embedding = best_face.normed_embedding  # 512-d unit vector (already normalized)
    t_total_ms = (time.perf_counter() - t_start) * 1000.0
    if timing_dict is not None:
        timing_dict["encode_ms"] = round(t_total_ms - t_det_ms, 2)

    return embedding, face_count


def compare_insight_embeddings(
    emb1: np.ndarray,
    emb2: np.ndarray,
    threshold: float = DEFAULT_COSINE_THRESHOLD,
    timing_dict: Optional[dict] = None,
) -> Tuple[bool, float]:
    """
    Compare two 512-d InsightFace normalized embeddings using Cosine Similarity.
    Since vectors are unit-norm: Cosine Similarity = dot_product(emb1, emb2).
    Returns (is_match, similarity_score). Higher similarity is better (range -1.0 to 1.0).
    A match occurs when similarity >= threshold (default 0.40).
    """
    t0 = time.perf_counter()
    v1 = np.asarray(emb1, dtype=np.float32).flatten()
    v2 = np.asarray(emb2, dtype=np.float32).flatten()

    norm1 = np.linalg.norm(v1)
    norm2 = np.linalg.norm(v2)
    if norm1 > 0:
        v1 = v1 / norm1
    if norm2 > 0:
        v2 = v2 / norm2

    sim = float(np.dot(v1, v2))
    if timing_dict is not None:
        timing_dict["match_ms"] = round((time.perf_counter() - t0) * 1000.0, 3)

    is_match = sim >= threshold
    return is_match, round(sim, 4)


_ENROLLED_EMBEDDINGS_CACHE = {}


def get_user_enrolled_insight_embedding(user) -> Optional[np.ndarray]:
    """
    Get 512-d ArcFace embedding for user's enrolled photo.
    Caches the embedding in memory so we NEVER re-extract the reference photo from disk on every punch.
    """
    if not user:
        return None

    user_id = str(getattr(user, "id", "") or "")
    face_photo = getattr(user, "face_photo", None)
    if not face_photo:
        return None

    photo_name = str(getattr(face_photo, "name", "") or "")
    if not photo_name:
        return None

    cache_key = f"{user_id}:{photo_name}"
    if cache_key in _ENROLLED_EMBEDDINGS_CACHE:
        return _ENROLLED_EMBEDDINGS_CACHE[cache_key]

    try:
        face_photo.open("rb")
        try:
            raw = face_photo.read()
        finally:
            face_photo.close()

        emb, _ = extract_insightface_embedding(raw)
        if emb is not None:
            _ENROLLED_EMBEDDINGS_CACHE[cache_key] = emb
        return emb
    except Exception as exc:
        logger.warning("Could not extract enrolled insightface embedding for user=%s: %s", user_id, exc)
        return None


def invalidate_user_insight_embedding(user_id: str):
    """Remove user from in-memory cache so next call reloads photo."""
    global _ENROLLED_EMBEDDINGS_CACHE
    prefix = f"{user_id}:"
    to_del = [k for k in _ENROLLED_EMBEDDINGS_CACHE if k.startswith(prefix)]
    for k in to_del:
        _ENROLLED_EMBEDDINGS_CACHE.pop(k, None)


