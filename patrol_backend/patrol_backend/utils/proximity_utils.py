import logging
import time
from geopy.distance import geodesic
from scheduler.models import LocationSite, SiteSetting

logger = logging.getLogger(__name__)

# location_id -> (expires_at, max_distance, [(site, lat, lon), ...])
_SITE_PROXIMITY_CACHE = {}
_SITE_CACHE_TTL_SEC = 300


def _get_location_sites_cached(location_id):
    now = time.time()
    key = str(location_id)
    cached = _SITE_PROXIMITY_CACHE.get(key)
    if cached and cached[0] > now:
        return cached[1], cached[2]

    radius_str = SiteSetting.get_setting(
        "attendance_distance", location_id=location_id, default_value="150"
    )
    try:
        max_distance = float(radius_str)
    except (ValueError, TypeError):
        max_distance = 150.0

    sites = list(
        LocationSite.objects.filter(location_id=location_id, is_active=True).only(
            "id", "name", "latitude", "longitude"
        )
    )
    _SITE_PROXIMITY_CACHE[key] = (now + _SITE_CACHE_TTL_SEC, max_distance, sites)
    return max_distance, sites


def get_site_within_proximity(latitude, longitude, location_id):
    """
    Checks if given coordinates are within any active site of a specific location.
    Returns the LocationSite object if within range, else None.
    Uses the 'attendance_distance' key from SiteSetting for geofence radius.
    """
    max_distance, active_sites = _get_location_sites_cached(location_id)

    if not active_sites:
        logger.warning(
            "[GEOFENCE_FAIL] location=%s input_coords=(%.6f, %.6f) error='No active sites configured for this location'",
            location_id,
            latitude,
            longitude,
        )
        return None, None

    closest_site = None
    min_distance = None

    for site in active_sites:
        distance = geodesic((latitude, longitude), (site.latitude, site.longitude)).meters
        if min_distance is None or distance < min_distance:
            min_distance = distance
            closest_site = site

        if distance <= max_distance:
            logger.info(
                "[GEOFENCE_MATCH] location=%s input_coords=(%.6f, %.6f) site='%s'(%s) site_coords=(%.6f, %.6f) distance=%.2fm allowed_radius=%.2fm",
                location_id,
                latitude,
                longitude,
                getattr(site, "name", "Unknown"),
                site.id,
                site.latitude,
                site.longitude,
                distance,
                max_distance,
            )
            return site, distance

    logger.warning(
        "[GEOFENCE_FAIL] location=%s input_coords=(%.6f, %.6f) allowed_radius=%.2fm active_sites_count=%d closest_site='%s'(%s) closest_coords=(%.6f, %.6f) closest_dist=%.2fm excess=%.2fm",
        location_id,
        latitude,
        longitude,
        max_distance,
        len(active_sites),
        getattr(closest_site, "name", "Unknown") if closest_site else "None",
        getattr(closest_site, "id", "None") if closest_site else "None",
        getattr(closest_site, "latitude", 0.0) if closest_site else 0.0,
        getattr(closest_site, "longitude", 0.0) if closest_site else 0.0,
        min_distance if min_distance is not None else 0.0,
        (min_distance - max_distance) if min_distance is not None else 0.0,
    )
    return None, None


def get_nearest_site_within_proximity(latitude, longitude, location_id, allowed_site_ids=None):
    """
    Among active sites within attendance_distance, return the closest one.
    allowed_site_ids: optional set of UUIDs the caller may use (v5 site access).
    """
    max_distance, active_sites = _get_location_sites_cached(location_id)
    allowed = None if allowed_site_ids is None else {str(sid) for sid in allowed_site_ids}

    if not active_sites:
        logger.warning(
            "[GEOFENCE_FAIL] nearest_site location=%s input_coords=(%.6f, %.6f) error='No active sites configured'",
            location_id,
            latitude,
            longitude,
        )
        return None, None

    best_site = None
    best_dist = None
    overall_closest_site = None
    overall_min_dist = None

    for site in active_sites:
        if allowed is not None and str(site.id) not in allowed:
            continue
        distance = geodesic((latitude, longitude), (site.latitude, site.longitude)).meters
        if overall_min_dist is None or distance < overall_min_dist:
            overall_min_dist = distance
            overall_closest_site = site

        if distance <= max_distance and (best_dist is None or distance < best_dist):
            best_site = site
            best_dist = distance

    if best_site is None:
        logger.warning(
            "[GEOFENCE_FAIL] nearest_site location=%s input_coords=(%.6f, %.6f) allowed_radius=%.2fm active_sites_checked=%d closest_site='%s'(%s) closest_coords=(%.6f, %.6f) closest_dist=%.2fm excess=%.2fm",
            location_id,
            latitude,
            longitude,
            max_distance,
            len(active_sites),
            getattr(overall_closest_site, "name", "Unknown") if overall_closest_site else "None",
            getattr(overall_closest_site, "id", "None") if overall_closest_site else "None",
            getattr(overall_closest_site, "latitude", 0.0) if overall_closest_site else 0.0,
            getattr(overall_closest_site, "longitude", 0.0) if overall_closest_site else 0.0,
            overall_min_dist if overall_min_dist is not None else 0.0,
            (overall_min_dist - max_distance) if overall_min_dist is not None else 0.0,
        )
        return None, None

    logger.info(
        "[GEOFENCE_MATCH] nearest_site location=%s input_coords=(%.6f, %.6f) matched_site='%s'(%s) site_coords=(%.6f, %.6f) distance=%.2fm allowed_radius=%.2fm",
        location_id,
        latitude,
        longitude,
        getattr(best_site, "name", "Unknown"),
        best_site.id,
        best_site.latitude,
        best_site.longitude,
        best_dist,
        max_distance,
    )
    return best_site, best_dist


def distance_to_site_meters(latitude, longitude, site):
    """Geodesic distance from coordinates to a site's center."""
    return geodesic((latitude, longitude), (site.latitude, site.longitude)).meters


def is_within_site_attendance_radius(latitude, longitude, location_id, site):
    """True if coords are within org attendance_distance of the given site."""
    max_distance, _ = _get_location_sites_cached(location_id)
    dist = distance_to_site_meters(latitude, longitude, site)
    is_inside = dist <= max_distance
    if is_inside:
        logger.info(
            "[GEOFENCE_MATCH] specific_site site='%s'(%s) location=%s input_coords=(%.6f, %.6f) site_coords=(%.6f, %.6f) distance=%.2fm allowed_radius=%.2fm",
            getattr(site, "name", "Unknown"),
            getattr(site, "id", "?"),
            location_id,
            latitude,
            longitude,
            getattr(site, "latitude", 0.0),
            getattr(site, "longitude", 0.0),
            dist,
            max_distance,
        )
    else:
        logger.warning(
            "[GEOFENCE_FAIL] specific_site site='%s'(%s) location=%s input_coords=(%.6f, %.6f) site_coords=(%.6f, %.6f) distance=%.2fm allowed_radius=%.2fm excess=%.2fm",
            getattr(site, "name", "Unknown"),
            getattr(site, "id", "?"),
            location_id,
            latitude,
            longitude,
            getattr(site, "latitude", 0.0),
            getattr(site, "longitude", 0.0),
            dist,
            max_distance,
            dist - max_distance,
        )
    return is_inside
