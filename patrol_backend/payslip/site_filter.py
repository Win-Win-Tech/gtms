"""Site scope for payslip v5 — filter employees via UserSite. Live payslip views do not use this."""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.models import User
from authapp.site_access import (
    allowed_site_ids,
    assert_caller_can_access_site,
    get_site_or_error,
    is_org_admin,
    users_queryset_for_site,
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


def resolve_payslip_location_id(request):
    """Effective location_id for payslip queries (super admin may pass location_id)."""
    requested = request.query_params.get("location_id")
    if not requested and hasattr(request, "data"):
        try:
            requested = request.data.get("location_id")
        except Exception:
            requested = None
    caller = request.user
    if caller.is_superuser:
        return requested
    return str(getattr(caller, "location_id", "") or "") or None


def apply_payslip_user_filter(users_qs, request):
    caller = request.user
    site_id = parse_site_id(request)
    location_id = resolve_payslip_location_id(request)

    if site_id:
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(caller, site)
        if location_id and str(site.location_id) != str(location_id):
            raise ValidationError({"site_id": "Site does not belong to that organisation."})
        if not caller.is_superuser and caller.location_id:
            if str(site.location_id) != str(caller.location_id):
                raise PermissionDenied("Site does not belong to your organisation.")
        return users_qs.filter(id__in=users_queryset_for_site(site).values("id"))

    if caller.is_superuser:
        if location_id:
            return users_qs.filter(location_id=location_id)
        return users_qs

    if is_org_admin(caller) or getattr(caller, "all_org_sites", False):
        if not location_id and caller.location_id:
            location_id = str(caller.location_id)
        if not location_id:
            return users_qs.none()
        return users_qs.filter(location_id=location_id)

    if not location_id and caller.location_id:
        location_id = str(caller.location_id)
    if not location_id:
        return users_qs.none()

    allowed = allowed_site_ids(caller)
    return users_qs.filter(
        Q(user_sites__site_id__in=allowed)
        | Q(all_org_sites=True, location_id=location_id)
    ).distinct()


def user_ids_in_payslip_scope(request):
    qs = apply_payslip_user_filter(
        User.objects.filter(is_active=True, is_deleted=False),
        request,
    )
    return list(qs.values_list("id", flat=True))


def get_scoped_user_ids(request):
    """
    User id strings visible for the current payslip scope.
    Returns None when no per-user filter is needed (superuser, no location).
    """
    caller = request.user
    site_id = parse_site_id(request)

    if site_id:
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(caller, site)
        if not caller.is_superuser and caller.location_id:
            if str(site.location_id) != str(caller.location_id):
                raise PermissionDenied("Site does not belong to your organisation.")
        return [str(uid) for uid in users_queryset_for_site(site).values_list("id", flat=True)]

    location_id = resolve_payslip_location_id(request)
    if not location_id and caller.location_id:
        location_id = str(caller.location_id)

    if caller.is_superuser and not location_id:
        return None

    if is_org_admin(caller) or getattr(caller, "all_org_sites", False) or caller.is_superuser:
        if not location_id:
            raise ValidationError({"location_id": "location_id is required"})
        qs = User.objects.filter(is_active=True, is_deleted=False, location_id=location_id)
        if not caller.is_superuser:
            qs = qs.exclude(role__iexact="admin")
        return [str(uid) for uid in qs.values_list("id", flat=True)]

    if not location_id:
        raise ValidationError({"location_id": "location_id is required"})

    return [str(uid) for uid in user_ids_in_payslip_scope(request)]


def assert_employee_in_payslip_scope(request, employee):
    scoped = get_scoped_user_ids(request)
    if scoped is not None and str(employee.id) not in scoped:
        raise PermissionDenied("Employee is not in your site scope.")


def resolve_site_name(request):
    site_id = parse_site_id(request)
    if not site_id:
        return ""
    site = get_site_or_error(site_id)
    assert_caller_can_access_site(request.user, site)
    return site.name or ""


def inject_scoped_user_ids_param(request):
    """
    Intersect query user_ids with payslip site scope for report endpoints.
    Mutates the underlying Django GET QueryDict on the request.
    """
    scoped = get_scoped_user_ids(request)
    if scoped is None:
        return
    scoped_set = set(scoped)
    q = request.query_params.copy()
    existing = q.get("user_ids")
    if existing:
        existing_ids = {part.strip() for part in str(existing).split(",") if part.strip()}
        merged = existing_ids & scoped_set
    else:
        merged = scoped_set
    if not merged:
        raise ValidationError({"site_id": "No employees found for this site scope."})
    q["user_ids"] = ",".join(sorted(merged))
    request._request.GET = q


def filter_queryset_by_user_scope(qs, request, user_field="user_id"):
    scoped = get_scoped_user_ids(request)
    if scoped is not None:
        qs = qs.filter(**{f"{user_field}__in": scoped})
    location_id = resolve_payslip_location_id(request)
    if location_id:
        qs = qs.filter(location_id=location_id)
    return qs
