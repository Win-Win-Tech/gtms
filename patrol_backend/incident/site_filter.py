"""Site scope for incident v5 list/export. Live incident views do not use this."""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.site_access import (
    allowed_site_ids,
    assert_caller_can_access_site,
    get_site_or_error,
    is_org_admin,
)


def apply_incident_site_filter(queryset, request):
    caller = request.user
    raw_site = request.query_params.get("site_id") or None
    site_id = None if not raw_site or str(raw_site).lower() in ("all", "null", "undefined") else raw_site
    location_id = request.query_params.get("location_id") or None
    org_id = location_id or getattr(caller, "location_id", None)

    if site_id:
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(caller, site)
        if location_id and str(site.location_id) != str(location_id):
            raise ValidationError({"site_id": "Site does not belong to that organisation."})
        if not caller.is_superuser and caller.location_id:
            if str(site.location_id) != str(caller.location_id):
                raise PermissionDenied("Site does not belong to your organisation.")
        return queryset.filter(site_id=site.id)

    # Header All: same org as live list. Include tickets that have no site.
    if caller.is_superuser:
        if org_id:
            return queryset.filter(location_id=org_id)
        return queryset

    if is_org_admin(caller) or getattr(caller, "all_org_sites", False):
        if not org_id:
            return queryset.none()
        return queryset.filter(location_id=org_id)

    if not org_id:
        return queryset.none()

    allowed = allowed_site_ids(caller)
    return queryset.filter(
        Q(location_id=org_id)
        & (Q(site_id__in=allowed) | Q(site__isnull=True))
    )
