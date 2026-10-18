"""Per-site CCTV camera configuration + live list APIs (additive; does not alter LocationSite)."""

import logging

from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.site_access import (
    allowed_site_ids,
    assert_caller_can_access_site,
    get_site_or_error,
    is_org_admin,
)

from . import mediamtx
from .models import SiteCamera
from .serializers import SiteCameraBulkSerializer, SiteCameraSerializer

logger = logging.getLogger(__name__)


def _assert_can_manage_site_cameras(user, site):
    if user.is_superuser:
        return
    if not is_org_admin(user):
        raise PermissionDenied("Only organisation admins can manage site cameras.")
    if not user.location_id or str(site.location_id) != str(user.location_id):
        raise PermissionDenied("Site does not belong to your organisation.")


def _camera_live_payload(camera: SiteCamera) -> dict:
    path = (camera.stream_path or "").strip() or mediamtx.build_stream_path(camera.id)
    return {
        "id": str(camera.id),
        "name": camera.name,
        "site_id": str(camera.site_id),
        "site_name": camera.site.name if camera.site_id else "",
        "camera_type": camera.camera_type or SiteCamera.CameraType.VEHICLE,
        "features": camera.features or [],
        "direction": camera.direction,
        "gate_mode": getattr(camera, "gate_mode", None) or "parked_toggle",
        "is_enabled": camera.is_enabled,
        "rtsp_url": camera.rtsp_url,
        "stream_path": path,
        "hls_url": mediamtx.build_hls_url(path),
        "whep_url": mediamtx.build_whep_url(path),
        "sort_order": camera.sort_order,
        "anpr_geometry": camera.anpr_geometry or {},
    }


class SiteCameraListReplaceView(APIView):
    """
    GET  /scheduler/sites/<site_id>/cameras/
    PUT  /scheduler/sites/<site_id>/cameras/  — replace camera list for site
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, site_id):
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(request.user, site)
        cameras = SiteCamera.objects.filter(site=site)
        return Response(SiteCameraSerializer(cameras, many=True).data)

    def put(self, request, site_id):
        site = get_site_or_error(site_id)
        _assert_can_manage_site_cameras(request.user, site)

        serializer = SiteCameraBulkSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        items = serializer.validated_data["cameras"]

        seen_names = set()
        for item in items:
            name = (item.get("name") or "").strip()
            rtsp = (item.get("rtsp_url") or "").strip()
            if not name:
                raise ValidationError({"cameras": "Each camera needs a name."})
            if not rtsp:
                raise ValidationError({"cameras": "Each camera needs an RTSP URL."})
            if not rtsp.lower().startswith(("rtsp://", "rtsps://")):
                raise ValidationError(
                    {"cameras": f"RTSP URL must start with rtsp:// or rtsps:// (camera: {name})."}
                )
            key = name.lower()
            if key in seen_names:
                raise ValidationError({"cameras": f"Duplicate camera name: {name}"})
            seen_names.add(key)

        old_paths = list(
            SiteCamera.objects.filter(site=site).exclude(stream_path="").values_list(
                "stream_path", flat=True
            )
        )
        SiteCamera.objects.filter(site=site).delete()
        for path in old_paths:
            mediamtx.delete_camera_path(path)

        created = []
        for index, item in enumerate(items):
            cam = SiteCamera(
                site=site,
                name=item["name"].strip(),
                rtsp_url=item["rtsp_url"].strip(),
                camera_type=item.get("camera_type") or SiteCamera.CameraType.VEHICLE,
                features=item.get("features") or [],
                direction=item.get("direction") or SiteCamera.Direction.TOGGLE,
                gate_mode=item.get("gate_mode") or SiteCamera.GateMode.PARKED_TOGGLE,
                is_enabled=item.get("is_enabled", True),
                sort_order=item.get("sort_order", index),
                stream_path=(item.get("stream_path") or "").strip(),
                anpr_geometry=item.get("anpr_geometry") or {},
            )
            cam.save()
            if not cam.stream_path:
                cam.stream_path = mediamtx.build_stream_path(cam.id)
                cam.save(update_fields=["stream_path", "modified_on"])
            if cam.is_enabled:
                mediamtx.sync_camera_path(cam.stream_path, cam.rtsp_url)
            created.append(cam)

        logger.info(
            "[SITE_CAMERA] Site %s replaced with %s camera(s) by %s",
            site.id,
            len(created),
            request.user.email,
        )
        return Response(SiteCameraSerializer(created, many=True).data)


class CctvLiveCamerasView(APIView):
    """
    GET /scheduler/cctv/live-cameras/
    Cameras on sites the caller can access (for live HLS player).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        site_ids = allowed_site_ids(request.user)
        if not site_ids and not request.user.is_superuser:
            return Response([])

        qs = (
            SiteCamera.objects.filter(is_enabled=True)
            .select_related("site")
            .order_by("site__name", "sort_order", "name")
        )
        if not request.user.is_superuser:
            qs = qs.filter(site_id__in=site_ids)

        site_id = (request.query_params.get("site_id") or "").strip()
        if site_id:
            qs = qs.filter(site_id=site_id)

        results = []
        for cam in qs:
            if not (cam.stream_path or "").strip():
                cam.stream_path = mediamtx.build_stream_path(cam.id)
                cam.save(update_fields=["stream_path", "modified_on"])
            # MediaMTX forgets API paths after restart — re-register so first
            # page load works without clicking Sync.
            mediamtx.sync_camera_path(cam.stream_path, cam.rtsp_url)
            results.append(_camera_live_payload(cam))

        return Response(
            {
                "mediamtx_hls_base": mediamtx.hls_base_url(),
                "mediamtx_webrtc_base": mediamtx.webrtc_base_url(),
                "results": results,
            }
        )


class CctvMediaMtxSyncView(APIView):
    """
    POST /scheduler/cctv/sync-mediamtx/
    Push enabled cameras to MediaMTX (org admin / superadmin).
    """

    permission_classes = [IsAuthenticated]

    def post(self, request):
        user = request.user
        if not (user.is_superuser or is_org_admin(user)):
            raise PermissionDenied("Only organisation admins can sync MediaMTX paths.")

        qs = SiteCamera.objects.filter(is_enabled=True).select_related("site")
        if not user.is_superuser:
            if not user.location_id:
                return Response({"synced": 0, "failed": 0, "results": []})
            qs = qs.filter(site__location_id=user.location_id)

        synced = 0
        failed = 0
        details = []
        for cam in qs:
            if not (cam.stream_path or "").strip():
                cam.stream_path = mediamtx.build_stream_path(cam.id)
                cam.save(update_fields=["stream_path", "modified_on"])
            ok = mediamtx.sync_camera_path(cam.stream_path, cam.rtsp_url)
            if ok:
                synced += 1
            else:
                failed += 1
            details.append(
                {
                    "id": str(cam.id),
                    "name": cam.name,
                    "stream_path": cam.stream_path,
                    "ok": ok,
                }
            )

        return Response({"synced": synced, "failed": failed, "results": details})


class CctvFaceEventListView(APIView):
    """
    GET /scheduler/cctv/face-events/
    Query CCTV face events (passes detected and recognised by the face reader).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from django.conf import settings
        from .models import CctvFaceEvent

        user = request.user
        qs = CctvFaceEvent.objects.all().select_related("user", "site", "camera")
        if not user.is_superuser:
            if not user.location_id:
                return Response([])
            qs = qs.filter(site__location_id=user.location_id)

        site_id = request.query_params.get("site_id")
        if site_id:
            qs = qs.filter(site_id=site_id)

        camera_id = request.query_params.get("camera_id")
        if camera_id:
            qs = qs.filter(camera_id=camera_id)

        result_filter = request.query_params.get("result")
        if result_filter:
            qs = qs.filter(result=result_filter)

        try:
            limit = min(200, max(1, int(request.query_params.get("limit", 50))))
        except (ValueError, TypeError):
            limit = 50

        events = qs[:limit]
        media_url = getattr(settings, "MEDIA_URL", "/media/")
        data = []
        for e in events:
            snap_url = f"{media_url.rstrip('/')}/{e.snapshot.lstrip('/')}" if e.snapshot else ""
            user_name = ""
            if e.user:
                user_name = f"{getattr(e.user, 'first_name', '')} {getattr(e.user, 'last_name', '')}".strip() or getattr(e.user, "username", "")
            data.append(
                {
                    "id": str(e.id),
                    "created_on": e.created_on.isoformat() if e.created_on else None,
                    "site_id": str(e.site_id) if e.site_id else None,
                    "site_name": e.site.name if e.site else "",
                    "camera_id": str(e.camera_id) if e.camera_id else None,
                    "camera_name": e.camera_name,
                    "user_id": str(e.user_id) if e.user_id else None,
                    "user_name": user_name,
                    "result": e.result,
                    "distance": e.distance,
                    "face_px": e.face_px,
                    "detect_score": e.detect_score,
                    "track_id": e.track_id,
                    "snapshot_url": snap_url,
                }
            )
        return Response(data)
