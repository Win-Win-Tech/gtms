"""Site access helpers for v5 user APIs (and later modules)."""

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError
from scheduler.models import LocationSite

from .models import User, UserSite


def is_org_admin(user):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return False
    return bool(user.role and user.role.lower() == "admin")


def allowed_site_qs(user):
    """LocationSite queryset this user may use."""
    qs = LocationSite.objects.filter(is_active=True)
    if not user or not getattr(user, "is_authenticated", False):
        return qs.none()
    if user.is_superuser:
        return qs
    if is_org_admin(user) or getattr(user, "all_org_sites", False):
        if not user.location_id:
            return qs.none()
        return qs.filter(location_id=user.location_id)
    return qs.filter(user_sites__user=user).distinct()


def allowed_site_ids(user):
    return set(allowed_site_qs(user).values_list("id", flat=True))


def get_site_or_error(site_id):
    site = LocationSite.objects.filter(id=site_id, is_active=True).select_related("location").first()
    if not site:
        raise ValidationError({"site_id": "Site not found or inactive."})
    return site


def caller_can_access_site(user, site):
    if not user or not getattr(user, "is_authenticated", False):
        return False
    if user.is_superuser:
        return True
    if is_org_admin(user) or getattr(user, "all_org_sites", False):
        return bool(user.location_id and str(site.location_id) == str(user.location_id))
    return UserSite.objects.filter(user=user, site=site).exists()


def assert_caller_can_access_site(user, site):
    if not caller_can_access_site(user, site):
        raise PermissionDenied("You are not allowed to use this site.")


def assigned_sites_payload(user):
    """[{id, name}, ...] for API responses."""
    if not user:
        return []
    if getattr(user, "all_org_sites", False) and user.location_id:
        sites = LocationSite.objects.filter(location_id=user.location_id, is_active=True).order_by("name")
    else:
        sites = (
            LocationSite.objects.filter(is_active=True, user_sites__user=user)
            .distinct()
            .order_by("name")
        )
    return [{"id": str(s.id), "name": s.name} for s in sites]


def header_sites_payload(user, location_id=None):
    """Sites for GET /auth/v5/my-sites/. Super Admin must pass location_id."""
    if not user or not getattr(user, "is_authenticated", False):
        return []
    if user.is_superuser:
        if not location_id:
            return []
        sites = LocationSite.objects.filter(
            location_id=location_id, is_active=True
        ).order_by("name")
        return [{"id": str(s.id), "name": s.name} for s in sites]
    return [{"id": str(s.id), "name": s.name} for s in allowed_site_qs(user).order_by("name")]


def site_ids_payload(user):
    if getattr(user, "all_org_sites", False):
        return []
    return [str(sid) for sid in UserSite.objects.filter(user=user).values_list("site_id", flat=True)]


def users_queryset_for_site(site):
    return User.objects.filter(is_deleted=False).filter(
        Q(user_sites__site_id=site.id)
        | Q(all_org_sites=True, location_id=site.location_id)
    ).distinct()


def validate_and_sync_user_sites(user, site_ids=None, all_org_sites=None):
    """
    Apply site assignment on the user.
    site_ids=None means leave UserSite rows unchanged (unless all_org_sites becomes True).
    """
    if all_org_sites is not None:
        user.all_org_sites = bool(all_org_sites)
        user.save(update_fields=["all_org_sites"])

    if user.all_org_sites:
        UserSite.objects.filter(user=user).delete()
        return

    if site_ids is None:
        return

    sites = list(
        LocationSite.objects.filter(id__in=site_ids, is_active=True).select_related("location")
    )
    found = {str(s.id) for s in sites}
    missing = [str(sid) for sid in site_ids if str(sid) not in found]
    if missing:
        raise ValidationError({"site_ids": f"Unknown or inactive site(s): {', '.join(missing)}"})

    if user.location_id:
        wrong_org = [str(s.id) for s in sites if str(s.location_id) != str(user.location_id)]
        if wrong_org:
            raise ValidationError({"site_ids": "All sites must belong to the user's organisation."})
    elif sites:
        raise ValidationError({"site_ids": "Set locationId before assigning sites."})

    UserSite.objects.filter(user=user).exclude(site_id__in=[s.id for s in sites]).delete()
    existing = set(
        UserSite.objects.filter(user=user).values_list("site_id", flat=True)
    )
    to_create = [UserSite(user=user, site=s) for s in sites if s.id not in existing]
    if to_create:
        UserSite.objects.bulk_create(to_create)
