"""MediaMTX helpers for CCTV live HLS (Phase 2). Best-effort; config save works without MediaMTX."""

from __future__ import annotations

import logging
from typing import Optional

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def mediamtx_enabled() -> bool:
    return bool(getattr(settings, "MEDIAMTX_ENABLED", True))


def hls_base_url() -> str:
    return (getattr(settings, "MEDIAMTX_HLS_BASE_URL", None) or "http://127.0.0.1:8888").rstrip("/")


def webrtc_base_url() -> str:
    return (getattr(settings, "MEDIAMTX_WEBRTC_BASE_URL", None) or "http://127.0.0.1:8889").rstrip("/")


def api_base_url() -> str:
    return (getattr(settings, "MEDIAMTX_API_URL", None) or "http://127.0.0.1:9997").rstrip("/")


def build_stream_path(camera_id) -> str:
    """Stable MediaMTX path name from camera UUID."""
    return f"cam-{str(camera_id).replace('-', '')[:16]}"


def build_hls_url(stream_path: str) -> Optional[str]:
    path = (stream_path or "").strip().strip("/")
    if not path:
        return None
    return f"{hls_base_url()}/{path}/index.m3u8"


def build_whep_url(stream_path: str) -> Optional[str]:
    """MediaMTX WebRTC WHEP endpoint — low latency live (CP Plus-like)."""
    path = (stream_path or "").strip().strip("/")
    if not path:
        return None
    return f"{webrtc_base_url()}/{path}/whep"


def sync_camera_path(stream_path: str, rtsp_url: str) -> bool:
    """
    Upsert a MediaMTX path that pulls from RTSP (on-demand).
    Returns True if MediaMTX accepted the config.
    """
    if not mediamtx_enabled():
        return False
    path = (stream_path or "").strip().strip("/")
    rtsp = (rtsp_url or "").strip()
    if not path or not rtsp:
        return False

    # Keep RTSP pull alive while the user watches (avoids freeze when HLS
    # briefly stalls). TCP required for many CP Plus / NVR cameras.
    # record=False: live view only — do not write stream to disk (Phase 2).
    payload = {
        "name": path,
        "source": rtsp,
        "sourceOnDemand": True,
        "sourceOnDemandStartTimeout": "20s",
        "sourceOnDemandCloseAfter": "5m",
        "rtspTransport": "tcp",
        "record": False,
    }
    try:
        # Prefer add; if exists, patch
        add_url = f"{api_base_url()}/v3/config/paths/add/{path}"
        resp = requests.post(add_url, json=payload, timeout=5)
        if resp.status_code in (200, 201):
            logger.info("[MEDIAMTX] Added path %s", path)
            return True
        if resp.status_code in (400, 409):
            patch_url = f"{api_base_url()}/v3/config/paths/patch/{path}"
            resp2 = requests.post(patch_url, json=payload, timeout=5)
            if resp2.status_code in (200, 201):
                logger.info("[MEDIAMTX] Patched path %s", path)
                return True
            logger.warning(
                "[MEDIAMTX] Patch path %s failed status=%s body=%s",
                path,
                resp2.status_code,
                (resp2.text or "")[:300],
            )
            return False
        logger.warning(
            "[MEDIAMTX] Add path %s failed status=%s body=%s",
            path,
            resp.status_code,
            (resp.text or "")[:300],
        )
        return False
    except requests.RequestException as exc:
        logger.warning("[MEDIAMTX] Sync path %s failed: %s", path, exc)
        return False


def delete_camera_path(stream_path: str) -> None:
    if not mediamtx_enabled():
        return
    path = (stream_path or "").strip().strip("/")
    if not path:
        return
    try:
        url = f"{api_base_url()}/v3/config/paths/delete/{path}"
        resp = requests.post(url, timeout=5)
        if resp.status_code not in (200, 201, 404):
            logger.warning(
                "[MEDIAMTX] Delete path %s status=%s",
                path,
                resp.status_code,
            )
    except requests.RequestException as exc:
        logger.warning("[MEDIAMTX] Delete path %s failed: %s", path, exc)
