"""Site scope for dashboard v5 list/export. Live dashboard views do not use this."""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.site_access import (
    allowed_site_ids,
    assert_caller_can_access_site,
    get_site_or_error,
    is_org_admin,
)


def parse_site_id(request):
    raw = request.query_params.get("site_id")
    if not raw and hasattr(request, "data"):
        try:
            raw = request.data.get("site_id")
        except Exception:
            raw = None
    if not raw or str(raw).lower() in ("all", "null", "undefined"):
        return None
    return raw


def resolve_dashboard_location_id(request):
    """Effective org/location id from query or body."""
    requested = (
        request.query_params.get("location_id")
        or request.query_params.get("location")
        or request.query_params.get("org_location_id")
    )
    if not requested and hasattr(request, "data"):
        try:
            requested = request.data.get("location_id") or request.data.get("location")
        except Exception:
            requested = None
    if requested and str(requested).lower() == "all":
        requested = None
    caller = request.user
    if caller.is_superuser:
        return requested
    return str(getattr(caller, "location_id", "") or "") or requested


def resolve_dashboard_site_scope(request):
    """
    Returns (effective_site_id, extra_q):
    - effective_site_id: str to pass to live helpers that accept site_id, or None
    - extra_q: optional Q() for queryset when header All + site-scoped user
    """
    caller = request.user
    site_id = parse_site_id(request)
    location_id = resolve_dashboard_location_id(request)

    if site_id:
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(caller, site)
        if location_id and str(site.location_id) != str(location_id):
            raise ValidationError({"site_id": "Site does not belong to that organisation."})
        if not caller.is_superuser and caller.location_id:
            if str(site.location_id) != str(caller.location_id):
                raise PermissionDenied("Site does not belong to your organisation.")
        return str(site.id), None

    if caller.is_superuser:
        return None, None

    if is_org_admin(caller) or getattr(caller, "all_org_sites", False):
        return None, None

    if not location_id and caller.location_id:
        location_id = str(caller.location_id)
    if not location_id:
        raise ValidationError({"location_id": "location_id is required"})

    allowed = allowed_site_ids(caller)
    extra_q = Q(site_id__in=allowed) | Q(site__isnull=True)
    return None, extra_q


def inject_validated_site_query_params(request):
    """
    Mutate underlying GET so live handlers receive a validated site_id (or none).
    Returns extra_q for AttendanceCheckin-style querysets when header All + site user.
    """
    effective_site_id, extra_q = resolve_dashboard_site_scope(request)
    q = request.query_params.copy()
    if effective_site_id:
        q["site_id"] = effective_site_id
    else:
        q.pop("site_id", None)
    request._request.GET = q
    return extra_q


def assert_site_in_scope(request, site_id):
    """Validate a concrete site_id on write payloads (bulk entry, cell action, etc.)."""
    if not site_id:
        return
    site = get_site_or_error(site_id)
    assert_caller_can_access_site(request.user, site)
    location_id = resolve_dashboard_location_id(request)
    if location_id and str(site.location_id) != str(location_id):
        raise ValidationError({"site_id": "Site does not belong to that organisation."})


def apply_dashboard_attendance_site_filter(queryset, request):
    """Filter AttendanceCheckin queryset by validated header site scope."""
    effective_site_id, extra_q = resolve_dashboard_site_scope(request)
    if effective_site_id:
        return queryset.filter(site_id=effective_site_id)
    if extra_q:
        return queryset.filter(extra_q)
    return queryset


def site_scope_internal_kwargs(request):
    """
    Validated site scope for internal report helpers.
    Returns dict with site_id and/or site_scope_extra_q (never both).
    """
    effective_site_id, extra_q = resolve_dashboard_site_scope(request)
    if effective_site_id:
        return {"site_id": effective_site_id}
    if extra_q:
        return {"site_scope_extra_q": extra_q}
    return {}


def assignment_attendance_site_q(request):
    """
    Assignment queryset filter for check-in report site scope.
    Returns a Q() or None when no site restriction applies.
    """
    effective_site_id, extra_q = resolve_dashboard_site_scope(request)
    if effective_site_id:
        return Q(attendance_records__site_id=effective_site_id)
    if extra_q:
        allowed = allowed_site_ids(request.user)
        return Q(attendance_records__site_id__in=allowed) | Q(
            attendance_records__site__isnull=True
        )
    return None


def assert_attendance_checkin_in_scope(request, attendance):
    """Validate caller may act on this AttendanceCheckin row."""
    if attendance is None:
        return
    caller = request.user
    site_id = getattr(attendance, "site_id", None)
    if site_id:
        assert_site_in_scope(request, str(site_id))
        return
    if caller.is_superuser or is_org_admin(caller) or getattr(caller, "all_org_sites", False):
        return
    location_id = resolve_dashboard_location_id(request)
    org_id = getattr(attendance, "org_location_id", None)
    if location_id and org_id and str(org_id) != str(location_id):
        raise PermissionDenied("Attendance record is outside your organisation scope.")
    # Legacy rows without site remain visible to site-scoped users at their org.


def filter_users_queryset_for_site_scope(users_qs, request):
    """Restrict user list to header site scope (bulk entry candidates)."""
    from authapp.models import UserSite

    effective_site_id, extra_q = resolve_dashboard_site_scope(request)
    if effective_site_id:
        assigned = UserSite.objects.filter(site_id=effective_site_id).values_list("user_id", flat=True)
        return users_qs.filter(Q(id__in=assigned) | Q(all_org_sites=True)).distinct()
    if extra_q:
        allowed = allowed_site_ids(request.user)
        assigned = UserSite.objects.filter(site_id__in=allowed).values_list("user_id", flat=True)
        return users_qs.filter(Q(id__in=assigned) | Q(all_org_sites=True)).distinct()
    return users_qs
