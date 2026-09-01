"""
Site boundary geometry and org-level monitoring settings.

Separate from attendance punch proximity (proximity_utils.attendance_distance).
"""
from __future__ import annotations

import time
from typing import Any, Iterable, List, Optional, Sequence, Tuple, Union

from geopy.distance import geodesic
from scheduler.models import LocationSite, SiteSetting

# SiteSetting keys (global template + per-org override via propagate_to_orgs)
BOUNDARY_SETTING_MONITORING_ENABLED = "boundary_monitoring_enabled"
BOUNDARY_SETTING_EXIT_BUFFER_M = "boundary_exit_buffer_m"
BOUNDARY_SETTING_STILL_OUTSIDE_REMINDER_MIN = "boundary_still_outside_reminder_min"
BOUNDARY_SETTING_LOCATION_MISSING_TIMEOUT_MIN = "location_missing_timeout_min"

BOUNDARY_SETTING_DEFAULTS = {
    BOUNDARY_SETTING_MONITORING_ENABLED: ("false", None),
    BOUNDARY_SETTING_EXIT_BUFFER_M: ("15", "m"),
    BOUNDARY_SETTING_STILL_OUTSIDE_REMINDER_MIN: ("0", "min"),
    BOUNDARY_SETTING_LOCATION_MISSING_TIMEOUT_MIN: ("10", "min"),
}

Coord = Union[Sequence[float], Tuple[float, float]]
PolygonCoords = List[List[float]]

_MONITORING_CACHE: dict[str, tuple[float, bool]] = {}
_MONITORING_CACHE_TTL_SEC = 60


def _parse_bool(raw, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    return str(raw).strip().lower() in ("1", "true", "yes", "on")


def get_boundary_setting_raw(
    key: str,
    location_id=None,
    default_value=None,
):
    return SiteSetting.get_setting(
        key=key,
        location_id=location_id,
        default_value=default_value,
    )


def get_boundary_setting_int(key: str, location_id=None, default_value: int = 0) -> int:
    default = default_value
    if default is None:
        default = 0
    try:
        raw = get_boundary_setting_raw(key, location_id=location_id, default_value=default)
        if raw is None:
            return int(default)
        return int(raw)
    except (TypeError, ValueError):
        return int(default)


def get_boundary_setting_bool(key: str, location_id=None, default_value: bool = False) -> bool:
    raw = get_boundary_setting_raw(
        key,
        location_id=location_id,
        default_value="true" if default_value else "false",
    )
    return _parse_bool(raw, default=default_value)


def is_org_boundary_monitoring_enabled(location_id) -> bool:
    if not location_id:
        return False
    cache_key = str(location_id)
    now = time.time()
    cached = _MONITORING_CACHE.get(cache_key)
    if cached and cached[0] > now:
        return cached[1]

    enabled = get_boundary_setting_bool(
        BOUNDARY_SETTING_MONITORING_ENABLED,
        location_id=location_id,
        default_value=False,
    )
    _MONITORING_CACHE[cache_key] = (now + _MONITORING_CACHE_TTL_SEC, enabled)
    return enabled


def get_boundary_exit_buffer_m(location_id) -> int:
    return max(
        0,
        get_boundary_setting_int(
            BOUNDARY_SETTING_EXIT_BUFFER_M,
            location_id=location_id,
            default_value=15,
        ),
    )


def get_boundary_still_outside_reminder_min(location_id) -> int:
    return max(
        0,
        get_boundary_setting_int(
            BOUNDARY_SETTING_STILL_OUTSIDE_REMINDER_MIN,
            location_id=location_id,
            default_value=0,
        ),
    )


def get_location_missing_timeout_min(location_id) -> int:
    return max(
        1,
        get_boundary_setting_int(
            BOUNDARY_SETTING_LOCATION_MISSING_TIMEOUT_MIN,
            location_id=location_id,
            default_value=10,
        ),
    )


def site_has_configured_boundary(site: LocationSite) -> bool:
    if not site:
        return False
    boundary_type = getattr(site, "boundary_type", None) or LocationSite.BoundaryType.NONE
    if boundary_type == LocationSite.BoundaryType.CIRCLE:
        return bool(site.boundary_radius_m and site.boundary_radius_m > 0)
    if boundary_type == LocationSite.BoundaryType.POLYGON:
        return bool(_normalize_polygon_coords(site.boundary_polygon))
    return False


def is_boundary_monitoring_active(location_id, site: Optional[LocationSite]) -> bool:
    """
    Global org toggle AND per-site enabled AND a drawable boundary configured.
    """
    if not location_id or not site:
        return False
    if not is_org_boundary_monitoring_enabled(location_id):
        return False
    if not getattr(site, "boundary_enabled", False):
        return False
    return site_has_configured_boundary(site)


def is_point_in_circle(
    lat: float,
    lng: float,
    center_lat: float,
    center_lng: float,
    radius_m: float,
) -> bool:
    if radius_m <= 0:
        return False
    distance = geodesic((lat, lng), (center_lat, center_lng)).meters
    return distance <= float(radius_m)


def _normalize_polygon_coords(polygon: Any) -> PolygonCoords:
    """
    Accept:
    - [[lat, lng], ...]
    - {"coordinates": [[lat, lng], ...]}
    """
    if not polygon:
        return []
    if isinstance(polygon, dict):
        polygon = polygon.get("coordinates") or []
    if not isinstance(polygon, (list, tuple)):
        return []

    points: PolygonCoords = []
    for item in polygon:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            lat = float(item[0])
            lng = float(item[1])
        except (TypeError, ValueError):
            continue
        points.append([lat, lng])

    if len(points) >= 3 and points[0] != points[-1]:
        points.append(points[0][:])
    return points


def is_point_in_polygon(lat: float, lng: float, polygon_coords: Iterable[Coord]) -> bool:
    """
    Ray-casting point-in-polygon. polygon_coords: [[lat, lng], ...] closed or open ring.
    """
    ring = _normalize_polygon_coords(polygon_coords)
    if len(ring) < 4:
        return False

    x = lng
    y = lat
    inside = False
    n = len(ring) - 1
    for i in range(n):
        y1, x1 = ring[i][0], ring[i][1]
        y2, x2 = ring[i + 1][0], ring[i + 1][1]
        intersects = ((y1 > y) != (y2 > y)) and (
            x < (x2 - x1) * (y - y1) / ((y2 - y1) or 1e-12) + x1
        )
        if intersects:
            inside = not inside
    return inside


def evaluate_site_boundary(
    site: LocationSite,
    lat: float,
    lng: float,
    *,
    location_id=None,
    previous_boundary_state: Optional[str] = None,
) -> Optional[bool]:
    """
    Return True (inside), False (outside), or None (not configured / monitoring off).

    Hysteresis for circle boundaries when previous_boundary_state is provided:
    - from inside: stay inside until distance > radius + exit_buffer
    - from outside: re-enter when distance <= radius
  """
    loc_id = location_id or getattr(site, "location_id", None)
    if not is_boundary_monitoring_active(loc_id, site):
        return None

    boundary_type = site.boundary_type
    exit_buffer_m = get_boundary_exit_buffer_m(loc_id)

    if boundary_type == LocationSite.BoundaryType.CIRCLE:
        radius_m = site.boundary_radius_m
        if not radius_m or radius_m <= 0:
            return None
        distance = geodesic((lat, lng), (site.latitude, site.longitude)).meters
        effective_radius = float(radius_m)
        if previous_boundary_state == "inside":
            effective_radius += float(exit_buffer_m)
        return distance <= effective_radius

    if boundary_type == LocationSite.BoundaryType.POLYGON:
        ring = _normalize_polygon_coords(site.boundary_polygon)
        if len(ring) < 4:
            return None
        inside = is_point_in_polygon(lat, lng, ring)
        if (
            previous_boundary_state == "inside"
            and not inside
            and exit_buffer_m > 0
        ):
            # Approximate buffer: still inside if within exit_buffer_m of any vertex
            for point in ring[:-1]:
                if geodesic((lat, lng), (point[0], point[1])).meters <= exit_buffer_m:
                    return True
        return inside

    return None


def boundary_status_label(inside: Optional[bool]) -> str:
    if inside is True:
        return "inside"
    if inside is False:
        return "outside"
    return "not_configured"
