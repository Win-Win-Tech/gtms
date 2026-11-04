"""
Phase 1: configure site + shift + role max headcount (no punch/assign enforcement yet).

GET/PUT /scheduler/v5/sites/<site_id>/shift-role-caps/?shift_id=<uuid>
"""

from django.db import transaction
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.site_access import assert_caller_can_access_site, get_site_or_error, is_org_admin

from .models import Shift, SiteShiftRoleCap


def _assert_can_manage_caps(user, site):
    if user.is_superuser:
        return
    if not is_org_admin(user):
        raise PermissionDenied("Only organisation admins can manage shift role caps.")
    if not user.location_id or str(site.location_id) != str(user.location_id):
        raise PermissionDenied("Site does not belong to your organisation.")


def _normalize_role(value) -> str:
    return str(value or "").strip().lower()


def _parse_roles_map(raw) -> dict:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValidationError({"roles": "Must be an object of role -> max_count"})
    out = {}
    for key, value in raw.items():
        role = _normalize_role(key)
        if not role:
            continue
        if role == "admin":
            continue
        try:
            max_count = int(value)
        except (TypeError, ValueError):
            raise ValidationError({"roles": f"Invalid max_count for role '{role}'"})
        if max_count < 0:
            raise ValidationError({"roles": f"max_count for '{role}' must be >= 0"})
        if max_count == 0:
            continue
        out[role] = max_count
    return out


def _caps_payload(site, shift, rows):
    roles = {row.role: int(row.max_count) for row in rows}
    total = sum(roles.values())
    return {
        "site_id": str(site.id),
        "site_name": site.name,
        "shift_id": str(shift.id),
        "shift_name": shift.name,
        "roles": roles,
        "total": total,
    }


class SiteShiftRoleCapView(APIView):
    """
    GET  — read role caps for one site+shift (empty roles = unlimited / not configured).
    PUT  — replace role matrix for that site+shift. Empty roles clears all caps.
    """

    permission_classes = [IsAuthenticated]

    def _resolve_shift(self, site, shift_id):
        if not shift_id:
            raise ValidationError({"shift_id": "shift_id is required"})
        shift = (
            Shift.objects.filter(
                id=shift_id,
                location_id=site.location_id,
                is_deleted=False,
            )
            .only("id", "name", "location_id")
            .first()
        )
        if not shift:
            raise ValidationError({"shift_id": "Shift not found for this organisation"})
        return shift

    def get(self, request, site_id):
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(request.user, site)
        shift = self._resolve_shift(site, request.query_params.get("shift_id"))
        rows = list(
            SiteShiftRoleCap.objects.filter(site=site, shift=shift)
            .order_by("role")
            .only("role", "max_count")
        )
        return Response(_caps_payload(site, shift, rows))

    def put(self, request, site_id):
        site = get_site_or_error(site_id)
        _assert_can_manage_caps(request.user, site)

        shift_id = request.data.get("shift_id") or request.query_params.get("shift_id")
        shift = self._resolve_shift(site, shift_id)
        roles = _parse_roles_map(request.data.get("roles"))

        with transaction.atomic():
            SiteShiftRoleCap.objects.filter(site=site, shift=shift).delete()
            SiteShiftRoleCap.objects.bulk_create(
                [
                    SiteShiftRoleCap(site=site, shift=shift, role=role, max_count=max_count)
                    for role, max_count in roles.items()
                ]
            )
            rows = list(
                SiteShiftRoleCap.objects.filter(site=site, shift=shift)
                .order_by("role")
                .only("role", "max_count")
            )
        from patrol_backend.utils.site_shift_headcount import invalidate_caps_cache

        invalidate_caps_cache(site_id=site.id, shift_id=shift.id)
        return Response(_caps_payload(site, shift, rows))
