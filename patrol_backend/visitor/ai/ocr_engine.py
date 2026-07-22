"""
PaddleOCR wrapper — CPU only, light defaults for 8 GB hosts.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import List, Tuple

logger = logging.getLogger(__name__)

_ocr_lock = threading.Lock()
_ocr_engine = None
_use_angle_cls = False


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def _mkldnn_enabled() -> bool:
    return _env_bool("VISITOR_AI_ENABLE_MKLDNN", False)


def _apply_mkldnn_flags(enabled: bool) -> None:
    # Force — do not use setdefault; paddle may already have set FLAGS.
    os.environ["FLAGS_use_mkldnn"] = "1" if enabled else "0"
    if not enabled:
        os.environ["FLAGS_onednn"] = "0"


def _cpu_threads() -> int:
    try:
        return max(1, int(os.environ.get("VISITOR_AI_CPU_THREADS", "2")))
    except ValueError:
        return 2


def ensure_ocr_ready() -> None:
    """Eagerly load PaddleOCR (call before soft timeout starts)."""
    _get_ocr()


def unload_ocr() -> None:
    """Drop the warm PaddleOCR instance so RSS can shrink after idle."""
    global _ocr_engine
    with _ocr_lock:
        if _ocr_engine is None:
            return
        logger.info("Unloading PaddleOCR engine")
        _ocr_engine = None


def _get_ocr():
    global _ocr_engine, _use_angle_cls
    if _ocr_engine is not None:
        return _ocr_engine
    with _ocr_lock:
        if _ocr_engine is not None:
            return _ocr_engine
        try:
            from paddleocr import PaddleOCR
        except ImportError as exc:
            raise ImportError(
                "paddleocr is required for visitor AI. "
                "Install paddlepaddle (CPU) then paddleocr — see visitor/docs/VISITOR_AI_OCR.md"
            ) from exc

        use_gpu = _env_bool("VISITOR_AI_USE_GPU", False)
        # Angle classifier loads an extra model (~100MB+) — off by default on 8 GB.
        use_angle_cls = _env_bool("VISITOR_AI_USE_ANGLE_CLS", False)
        lang = os.environ.get("VISITOR_AI_OCR_LANG", "en")
        enable_mkldnn = _mkldnn_enabled()
        cpu_threads = _cpu_threads()
        _apply_mkldnn_flags(enable_mkldnn)
        _use_angle_cls = use_angle_cls

        # Cap paddle intra-op threads (helps RAM + CPU contention with Django).
        os.environ.setdefault("OMP_NUM_THREADS", str(cpu_threads))
        os.environ.setdefault("MKL_NUM_THREADS", str(cpu_threads))
        os.environ.setdefault("OPENBLAS_NUM_THREADS", str(cpu_threads))
        try:
            import paddle

            paddle.set_num_threads(cpu_threads)
        except Exception:
            pass

        logger.info(
            "Loading PaddleOCR (gpu=%s lang=%s mkldnn=%s angle_cls=%s threads=%s)",
            use_gpu,
            lang,
            enable_mkldnn,
            use_angle_cls,
            cpu_threads,
        )
        kwargs = dict(
            use_angle_cls=use_angle_cls,
            lang=lang,
            use_gpu=use_gpu,
            enable_mkldnn=enable_mkldnn,
            cpu_threads=cpu_threads,
        )
        try:
            _ocr_engine = PaddleOCR(show_log=False, **kwargs)
        except TypeError:
            kwargs.pop("cpu_threads", None)
            try:
                _ocr_engine = PaddleOCR(show_log=False, **kwargs)
            except TypeError:
                try:
                    _ocr_engine = PaddleOCR(**kwargs)
                except TypeError:
                    _ocr_engine = PaddleOCR(
                        use_angle_cls=use_angle_cls,
                        lang=lang,
                    )
        return _ocr_engine


def run_ocr(bgr_image) -> List[Tuple[str, float]]:
    """
    Run OCR on BGR image.

    Returns list of (text, confidence) sorted by confidence desc.
    """
    global _ocr_engine
    if bgr_image is None or getattr(bgr_image, "size", 0) == 0:
        return []
    ocr = _get_ocr()
    use_cls = _use_angle_cls
    try:
        result = ocr.ocr(bgr_image, cls=use_cls)
    except TypeError:
        result = ocr.ocr(bgr_image)
    except RuntimeError as exc:
        # Rare residual oneDNN failure: recreate engine once without MKLDNN
        if "primitive" not in str(exc).lower():
            raise
        logger.warning("PaddleOCR primitive error — reloading with MKLDNN off: %s", exc)
        with _ocr_lock:
            _ocr_engine = None
        os.environ["VISITOR_AI_ENABLE_MKLDNN"] = "0"
        _apply_mkldnn_flags(False)
        ocr = _get_ocr()
        try:
            result = ocr.ocr(bgr_image, cls=use_cls)
        except TypeError:
            result = ocr.ocr(bgr_image)
    lines: List[Tuple[str, float]] = []
    if not result:
        return lines

    # Legacy PaddleOCR: result[0] = list of [box, (text, score)]
    page = result[0] if isinstance(result, list) else result
    if page is None:
        return lines

    # Some versions return dict-like / OCRResult
    if hasattr(page, "get") and not isinstance(page, (list, tuple)):
        texts = page.get("rec_texts") or page.get("texts") or []
        scores = page.get("rec_scores") or page.get("scores") or []
        for i, text in enumerate(texts):
            text = str(text).strip()
            score = float(scores[i]) if i < len(scores) else 0.0
            if text:
                lines.append((text, score))
        lines.sort(key=lambda t: t[1], reverse=True)
        return lines

    if not isinstance(page, (list, tuple)):
        return lines

    for item in page:
        if not item or len(item) < 2:
            continue
        text_info = item[1]
        if not text_info:
            continue
        if isinstance(text_info, (list, tuple)):
            text = str(text_info[0]).strip()
            score = float(text_info[1]) if len(text_info) > 1 else 0.0
        else:
            text = str(text_info).strip()
            score = 0.0
        if text:
            lines.append((text, score))
    lines.sort(key=lambda t: t[1], reverse=True)
    return lines
