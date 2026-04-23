from geopy.distance import geodesic
from scheduler.models import LocationSite, SiteSetting

def get_site_within_proximity(latitude, longitude, location_id):
    """
    Checks if given coordinates are within any active site of a specific location.
    Returns the LocationSite object if within range, else None.
    Uses the 'attendance_distance' key from SiteSetting for geofence radius.
    """
    # Fetch unified radius from SiteSetting
    radius_str = SiteSetting.get_setting("attendance_distance", location_id=location_id, default_value="150")
    try:
        max_distance = float(radius_str)
    except (ValueError, TypeError):
        max_distance = 150.0

    active_sites = LocationSite.objects.filter(location_id=location_id, is_active=True)
    
    for site in active_sites:
        distance = geodesic((latitude, longitude), (site.latitude, site.longitude)).meters
        if distance <= max_distance:
            return site, distance
            
    return None, None
