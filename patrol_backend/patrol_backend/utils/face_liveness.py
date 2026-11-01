"""
MiniFASNet silent liveness (photo/video-replay spoofing) for checkin_v6 / checkout_v6.

Uses the `uniface` MiniFASNet wrapper when installed; otherwise ONNX Runtime +
MiniFASNetV2.onnx. Session is a process singleton so punch latency stays low.
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
import time
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

_liveness_flag = contextvars.ContextVar("gtms_face_liveness", default=False)

_lock = threading.Lock()
_uniface_spoofers = {}
_uniface_failed = set()
_onnx_session = None
_onnx_failed = False
_onnx_input_name = None

MINIFASNET_HF_URL = (
    "https://huggingface.co/garciafido/minifasnet-v2-anti-spoofing-onnx/resolve/main/MiniFASNetV2.onnx"
)
# Floor on MiniFASNet *live-class* probability. Class label (real vs spoof) is the
# primary decision; 0.80 was too strict and can reject a genuine selfie.
DEFAULT_THRESHOLD = 0.50
CROP_SCALE = 2.7
INPUT_SIZE = 80


def liveness_required() -> bool:
    return bool(_liveness_flag.get())


class liveness_enabled:
    """Context manager so v6 can reuse v5 punch code without changing v5 behaviour."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled
        self._token = None

    def __enter__(self):
        self._token = _liveness_flag.set(bool(self.enabled))
        return self

    def __exit__(self, exc_type, exc, tb):
        if self._token is not None:
            _liveness_flag.reset(self._token)


def _settings():
    try:
        from django.conf import settings
        return settings
    except Exception:
        return None


def get_liveness_threshold() -> float:
    s = _settings()
    return float(getattr(s, "FACE_LIVENESS_THRESHOLD", DEFAULT_THRESHOLD) if s else DEFAULT_THRESHOLD)


def default_model_path() -> str:
    s = _settings()
    override = getattr(s, "FACE_LIVENESS_MODEL_PATH", None) if s else None
    if override:
        return str(override)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.normpath(os.path.join(here, "..", "ml_models", "MiniFASNetV2.onnx"))


def is_liveness_available() -> bool:
    try:
        import uniface.spoofing  # noqa: F401
        return True
    except Exception:
        pass
    try:
        import onnxruntime  # noqa: F401
        return True
    except Exception:
        return False


def _uniface_model_key(model_name) -> str:
    return str(getattr(model_name, "name", None) or getattr(model_name, "value", None) or model_name)


def _uniface_model_list():
    """Punch path uses MiniFASNetV2 only (one ONNX session, ~5ms after warmup)."""
    try:
        from uniface.constants import MiniFASNetWeights

        return [MiniFASNetWeights.V2]
    except Exception:
        return [None]


def _get_uniface_spoofer(model_name=None):
    if model_name is None:
        try:
            from uniface.constants import MiniFASNetWeights

            model_name = MiniFASNetWeights.V2
        except Exception:
            model_name = None
    key = _uniface_model_key(model_name)
    if key in _uniface_failed:
        return None
    cached = _uniface_spoofers.get(key)
    if cached is not None:
        return cached
    with _lock:
        if key in _uniface_failed:
            return None
        cached = _uniface_spoofers.get(key)
        if cached is not None:
            return cached
        try:
            from uniface.spoofing import MiniFASNet

            kwargs = {}
            if model_name is not None:
                kwargs["model_name"] = model_name
            spoofer = MiniFASNet(**kwargs)
            _uniface_spoofers[key] = spoofer
            logger.info("MiniFASNet (uniface) loaded for face liveness model=%s", key)
            return spoofer
        except Exception as exc:
            logger.warning("uniface MiniFASNet not available model=%s: %s", key, exc)
            _uniface_failed.add(key)
            return None


def _ensure_onnx_model(path: str) -> bool:
    if os.path.isfile(path) and os.path.getsize(path) > 1000:
        return True
    os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        import urllib.request

        logger.info("Downloading MiniFASNetV2.onnx to %s", path)
        tmp = path + ".part"
        urllib.request.urlretrieve(MINIFASNET_HF_URL, tmp)
        os.replace(tmp, path)
        return os.path.isfile(path)
    except Exception as exc:
        logger.warning("Could not download MiniFASNet ONNX: %s", exc)
        return False


def _get_onnx_session():
    global _onnx_session, _onnx_failed, _onnx_input_name
    if _onnx_failed:
        return None
    if _onnx_session is not None:
        return _onnx_session
    with _lock:
        if _onnx_session is not None or _onnx_failed:
            return _onnx_session
        try:
            import onnxruntime as ort

            path = default_model_path()
            if not _ensure_onnx_model(path):
                _onnx_failed = True
                return None
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = 1
            opts.inter_op_num_threads = 1
            opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess = ort.InferenceSession(path, sess_options=opts, providers=["CPUExecutionProvider"])
            _onnx_session = sess
            _onnx_input_name = sess.get_inputs()[0].name
            logger.info("MiniFASNetV2 ONNX session ready (%s)", path)
            return _onnx_session
        except Exception as exc:
            logger.warning("MiniFASNet ONNX session failed: %s", exc)
            _onnx_failed = True
            return None


def _crop_face_bgr(img_bgr: np.ndarray, bbox_xyxy, scale: float = CROP_SCALE, size: int = INPUT_SIZE) -> np.ndarray:
    import cv2

    h, w = img_bgr.shape[:2]
    x1, y1, x2, y2 = [float(v) for v in bbox_xyxy]
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    cx = x1 + box_w / 2.0
    cy = y1 + box_h / 2.0
    scale = min((h - 1) / box_h, (w - 1) / box_w, scale)
    nw = box_w * scale
    nh = box_h * scale
    nx1 = max(0, int(cx - nw / 2.0))
    ny1 = max(0, int(cy - nh / 2.0))
    nx2 = min(w - 1, int(cx + nw / 2.0))
    ny2 = min(h - 1, int(cy + nh / 2.0))
    crop = img_bgr[ny1 : ny2 + 1, nx1 : nx2 + 1]
    if crop.size == 0:
        crop = img_bgr
    return cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR)


def _softmax(logits: np.ndarray) -> np.ndarray:
    x = logits.astype(np.float64)
    x = x - np.max(x)
    e = np.exp(x)
    return e / np.sum(e)


def _score_onnx(img_bgr: np.ndarray, bbox_xyxy) -> Tuple[bool, float, str]:
    sess = _get_onnx_session()
    if sess is None:
        return False, 0.0, "liveness_unavailable"
    crop = _crop_face_bgr(img_bgr, bbox_xyxy)
    tensor = crop.astype(np.float32) / 255.0
    tensor = np.transpose(tensor, (2, 0, 1))[None, ...]
    outputs = sess.run(None, {_onnx_input_name: tensor})
    logits = np.asarray(outputs[0]).reshape(-1)
    probs = _softmax(logits)
    if probs.size == 3:
        live_score = float(probs[0])
        label = "live" if live_score >= max(float(probs[1]), float(probs[2])) else "spoof"
    else:
        idx = int(np.argmax(probs))
        live_score = float(probs[idx]) if idx == 1 else float(1.0 - probs[idx])
        label = "live" if idx == 1 else "spoof"
    is_live = label == "live" and live_score >= get_liveness_threshold()
    return is_live, live_score, label


def _parse_spoof_result(result) -> Tuple[bool, float]:
    if isinstance(result, dict):
        is_real = str(result.get("label", "")).lower() in ("real", "live", "1")
        score = float(result.get("score") or result.get("confidence") or 0.0)
        return is_real, score
    is_real = bool(getattr(result, "is_real", False))
    score = float(getattr(result, "confidence", None) or getattr(result, "score", 0.0) or 0.0)
    return is_real, score


def _score_uniface(img_bgr: np.ndarray, bbox_xyxy) -> Tuple[bool, float, str]:
    live_scores = []
    loaded = 0
    for model_name in _uniface_model_list():
        spoofer = _get_uniface_spoofer(model_name)
        if spoofer is None:
            continue
        loaded += 1
        is_real, score = _parse_spoof_result(spoofer.predict(img_bgr, bbox_xyxy))
        if not is_real:
            return False, score, "spoof"
        live_scores.append(score)
    if loaded == 0:
        return False, 0.0, "liveness_unavailable"
    live_score = min(live_scores)
    if live_score < get_liveness_threshold():
        return False, live_score, "spoof"
    return True, live_score, "live"


def assert_live_face(
    img_bgr: np.ndarray,
    bbox_xyxy,
    timing_dict: Optional[dict] = None,
) -> Tuple[bool, str, float]:
    """
    Score one already-detected face crop. Does not re-run face recognition.
    Returns (ok, error_message, live_score).
    """
    t0 = time.perf_counter()
    if img_bgr is None or bbox_xyxy is None:
        return False, "No face detected for liveness check", 0.0

    engine = "uniface"
    is_live, score, label = (False, 0.0, "liveness_unavailable")
    if _get_uniface_spoofer() is not None:
        is_live, score, label = _score_uniface(img_bgr, bbox_xyxy)
    else:
        engine = "onnx"
        is_live, score, label = _score_onnx(img_bgr, bbox_xyxy)

    if timing_dict is not None:
        timing_dict["liveness_ms"] = round((time.perf_counter() - t0) * 1000.0, 2)
        timing_dict["liveness_score"] = round(float(score), 4)
        timing_dict["liveness_label"] = label
        timing_dict["liveness_engine"] = engine

    logger.info(
        "MiniFASNet liveness engine=%s label=%s score=%.4f live=%s",
        engine,
        label,
        float(score),
        is_live,
    )
    if label == "liveness_unavailable":
        return False, "Face liveness (MiniFASNet) is not installed on the server", score
    if not is_live:
        return False, "Liveness check failed: possible photo or video spoof", score
    return True, "", score


def _decode_image_bgr(image_bytes: bytes, timing_dict: Optional[dict] = None):
    """Decode upload to BGR without requiring face_recognition / dlib."""
    import io

    import cv2
    from PIL import Image, ImageOps

    if not image_bytes:
        return None
    t0 = time.perf_counter()
    try:
        with Image.open(io.BytesIO(image_bytes)) as img:
            img = ImageOps.exif_transpose(img)
            img = img.convert("RGB")
            w, h = img.size
            max_dim = 640
            if max(w, h) > max_dim and max(w, h) > 0:
                scale = max_dim / float(max(w, h))
                img = img.resize(
                    (max(1, int(w * scale)), max(1, int(h * scale))),
                    Image.Resampling.LANCZOS,
                )
            rgb = np.asarray(img)
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        if timing_dict is not None:
            timing_dict["decode_ms"] = round(
                timing_dict.get("decode_ms", 0.0) + (time.perf_counter() - t0) * 1000.0, 2
            )
        return bgr
    except Exception as exc:
        logger.warning("liveness image decode failed: %s", exc)
        return None


def warmup_liveness() -> bool:
    """Load MiniFASNetV2 and run one dummy inference so the first punch is not a cold start."""
    t0 = time.perf_counter()
    dummy = np.zeros((80, 80, 3), dtype=np.uint8)
    is_live, score, label = (False, 0.0, "liveness_unavailable")
    if _get_uniface_spoofer() is not None:
        is_live, score, label = _score_uniface(dummy, [0, 0, 79, 79])
    else:
        is_live, score, label = _score_onnx(dummy, [0, 0, 79, 79])
    ms = round((time.perf_counter() - t0) * 1000.0, 1)
    if label == "liveness_unavailable":
        logger.warning("MiniFASNet warmup skipped: model unavailable (%.1fms)", ms)
        return False
    logger.info(
        "MiniFASNet warmup complete ms=%.1f label=%s score=%.4f live=%s",
        ms,
        label,
        float(score),
        is_live,
    )
    return True


def assert_live_image_bytes(
    image_bytes: bytes,
    timing_dict: Optional[dict] = None,
) -> Tuple[bool, str, float]:
    """Run MiniFASNet on a punch photo (v6 even when face attendance is off)."""
    img_bgr = _decode_image_bgr(image_bytes, timing_dict=timing_dict)
    if img_bgr is None:
        return False, "Liveness check requires a valid image", 0.0
    h, w = img_bgr.shape[:2]
    return assert_live_face(img_bgr, [0, 0, w - 1, h - 1], timing_dict=timing_dict)
