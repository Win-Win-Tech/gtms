import time
from geopy.distance import geodesic
from scheduler.models import LocationSite, SiteSetting

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
            "id", "latitude", "longitude"
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

    for site in active_sites:
        distance = geodesic((latitude, longitude), (site.latitude, site.longitude)).meters
        if distance <= max_distance:
            return site, distance

    return None, None
