"""Resolve visitor/vehicle type options per organisation (global + org-scoped)."""
from __future__ import annotations

from typing import Optional

from .models import VisitorEntry, VisitorLookupOption

# Seed definitions used by migrations and backfill scripts
DEFAULT_VISITOR_TYPES = [
    ("guest", "Guest", 0),
    ("contractor", "Contractor", 1),
    ("client", "Client", 2),
    ("delivery", "Delivery", 3),
    ("other", "Other", 4),
]

DEFAULT_VEHICLE_TYPES = [
    ("car", "Car", 0),
    ("truck", "Truck", 1),
    ("van", "Van", 2),
    ("motorcycle", "Motorcycle", 3),
    ("bus", "Bus", 4),
    ("other", "Other", 5),
]


def _normalize_location_id(location_id):
    if not location_id:
        return None
    return str(location_id)


def get_options_for_location(location_id, kind, active_only=True):
    """Return queryset of options for an org; fallback to global templates."""
    loc_id = _normalize_location_id(location_id)
    base = VisitorLookupOption.objects.filter(kind=kind)
    if active_only:
        base = base.filter(is_active=True)

    if loc_id:
        loc_qs = base.filter(location_id=loc_id)
        if loc_qs.exists():
            return loc_qs.order_by("sort_order", "label")
    return base.filter(location__isnull=True).order_by("sort_order", "label")


def get_valid_codes(location_id, kind, active_only=True) -> set[str]:
    return set(get_options_for_location(location_id, kind, active_only=active_only).values_list("code", flat=True))


def resolve_label(location_id, kind, code) -> str:
    text = (code or "").strip().lower()
    if not text:
        return "—"
    qs = VisitorLookupOption.objects.filter(kind=kind, code=text)
    loc_id = _normalize_location_id(location_id)
    if loc_id:
        row = qs.filter(location_id=loc_id).first()
        if row:
            return row.label
    row = qs.filter(location__isnull=True).first()
    if row:
        return row.label
    return text.replace("_", " ").title()


def normalize_visitor_type(value, location_id) -> Optional[str]:
    text = (value or "").strip().lower()
    if not text:
        return None
    valid = get_valid_codes(location_id, VisitorLookupOption.KIND_VISITOR_TYPE)
    return text if text in valid else None


def normalize_vehicle_type(value, location_id) -> Optional[str]:
    """Return valid vehicle_type code, empty string, or None if invalid."""
    text = (value or "").strip().lower()
    if not text:
        return ""
    valid = get_valid_codes(location_id, VisitorLookupOption.KIND_VEHICLE_TYPE)
    return text if text in valid else None


def get_vehicle_report_order(location_id) -> list[str]:
    """Ordered vehicle type codes for movement report rows."""
    return list(
        get_options_for_location(location_id, VisitorLookupOption.KIND_VEHICLE_TYPE).values_list("code", flat=True)
    )


def code_in_use(location_id, kind, code) -> bool:
    loc_id = _normalize_location_id(location_id)
    if not loc_id or not code:
        return False
    if kind == VisitorLookupOption.KIND_VISITOR_TYPE:
        return VisitorEntry.objects.filter(location_id=loc_id, visitor_type=code, is_deleted=False).exists()
    if kind == VisitorLookupOption.KIND_VEHICLE_TYPE:
        return VisitorEntry.objects.filter(location_id=loc_id, vehicle_type=code, is_deleted=False).exists()
    return False
