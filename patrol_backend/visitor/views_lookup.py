from django.db import transaction
from rest_framework import permissions, status, viewsets
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from .lookup_options import code_in_use
from .models import VisitorLookupOption
from .serializers_lookup import VisitorLookupOptionSerializer, _slugify_code


def _is_superadmin_user(user):
    if getattr(user, "is_superuser", False):
        return True
    role = (getattr(user, "role", None) or "").lower()
    return role in ("superadmin", "super_admin")


def _can_manage_lookup_options(user):
    if _is_superadmin_user(user):
        return True
    role = (getattr(user, "role", None) or "").lower()
    return role == "admin"


def _scope_qs(kind, location):
    return VisitorLookupOption.objects.filter(kind=kind, location=location)


def _next_free_sort(kind, location, exclude_id=None):
    qs = _scope_qs(kind, location)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    used = set(qs.values_list("sort_order", flat=True))
    return (max(used) + 1) if used else 0


def _parse_sort(value, fallback=0):
    try:
        sort_order = int(value)
    except (TypeError, ValueError):
        return fallback
    return max(0, sort_order)


def _swap_occupant_to_end(kind, location, taken_sort, exclude_id=None):
    """If taken_sort is occupied, move that row to the next free sort (end)."""
    qs = _scope_qs(kind, location).filter(sort_order=taken_sort)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    occupant = qs.first()
    if not occupant:
        return
    occupant.sort_order = _next_free_sort(kind, location, exclude_id=occupant.id)
    occupant.save(update_fields=["sort_order"])


def _swap_sort(instance, new_sort):
    """Swap sort_order with the row that currently holds new_sort."""
    if new_sort is None or new_sort == instance.sort_order:
        return
    other = (
        _scope_qs(instance.kind, instance.location)
        .filter(sort_order=new_sort)
        .exclude(id=instance.id)
        .first()
    )
    if not other:
        return
    other.sort_order = instance.sort_order
    other.save(update_fields=["sort_order"])


class VisitorLookupOptionViewSet(viewsets.ModelViewSet):
    serializer_class = VisitorLookupOptionSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        location_id = self.request.query_params.get("location_id")
        kind = (self.request.query_params.get("kind") or "").strip()

        if location_id:
            if user.location and str(user.location.id) != location_id and not _is_superadmin_user(user):
                return VisitorLookupOption.objects.none()
            loc_qs = VisitorLookupOption.objects.filter(location_id=location_id)
            queryset = loc_qs if loc_qs.exists() else VisitorLookupOption.objects.filter(location__isnull=True)
        elif _is_superadmin_user(user) or not user.location:
            queryset = VisitorLookupOption.objects.filter(location__isnull=True)
        else:
            loc_qs = VisitorLookupOption.objects.filter(location=user.location)
            queryset = loc_qs if loc_qs.exists() else VisitorLookupOption.objects.filter(location__isnull=True)

        if kind:
            queryset = queryset.filter(kind=kind)
        return queryset.order_by("sort_order", "label")

    def _require_manage_permission(self):
        if not _can_manage_lookup_options(self.request.user):
            raise PermissionDenied("Only administrators can manage visitor lookup options.")

    def create(self, request, *args, **kwargs):
        self._require_manage_permission()
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)
        if not (data.get("code") or "").strip():
            data["code"] = _slugify_code(data.get("label") or "")

        user = request.user
        location = None if (_is_superadmin_user(user) or not user.location) else user.location
        kind = data.get("kind")
        preferred = _parse_sort(data.get("sort_order"), fallback=_next_free_sort(kind, location))

        with transaction.atomic():
            _swap_occupant_to_end(kind, location, preferred)
            data["sort_order"] = preferred
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

    def update(self, request, *args, **kwargs):
        self._require_manage_permission()
        partial = kwargs.pop("partial", False)
        instance = self.get_object()
        data = request.data.copy() if hasattr(request.data, "copy") else dict(request.data)

        with transaction.atomic():
            if "sort_order" in data:
                new_sort = _parse_sort(data.get("sort_order"), fallback=instance.sort_order)
                _swap_sort(instance, new_sort)
                data["sort_order"] = new_sort
            serializer = self.get_serializer(instance, data=data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)

        return Response(serializer.data)

    def destroy(self, request, *args, **kwargs):
        self._require_manage_permission()
        return super().destroy(request, *args, **kwargs)

    def perform_create(self, serializer):
        user = self.request.user
        if _is_superadmin_user(user) or not user.location:
            serializer.save(location=None, is_default=True)
        else:
            serializer.save(location=user.location, is_default=False)

    def perform_update(self, serializer):
        instance = self.get_object()
        user = self.request.user
        if instance.location is None and not _is_superadmin_user(user):
            raise PermissionDenied("Only super administrators can edit global templates.")

        old_code = instance.code
        old_kind = instance.kind
        updated = serializer.save()

        if updated.location is None:
            VisitorLookupOption.objects.filter(
                kind=old_kind,
                code__iexact=old_code,
            ).exclude(id=updated.id).update(
                label=updated.label,
                sort_order=updated.sort_order,
                is_active=updated.is_active,
            )

    def perform_destroy(self, instance):
        user = self.request.user
        if instance.is_default:
            raise ValidationError({"detail": "Default system types cannot be deleted."})
        if instance.location is None and not _is_superadmin_user(user):
            raise PermissionDenied("Only super administrators can delete global templates.")

        loc_id = instance.location_id
        if loc_id and code_in_use(loc_id, instance.kind, instance.code):
            raise ValidationError({"detail": "This type is in use by visitor entries and cannot be deleted."})

        instance.delete()
