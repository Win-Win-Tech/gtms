"""Backfill org copies of global visitor/vehicle lookup options (like populate_roles.py)."""
import os
import sys
import uuid

import django

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

from scheduler.models import Location  # noqa: E402
from visitor.lookup_options import DEFAULT_VEHICLE_TYPES, DEFAULT_VISITOR_TYPES  # noqa: E402
from visitor.models import VisitorLookupOption  # noqa: E402


def seed_globals():
    for kind, items in (
        (VisitorLookupOption.KIND_VISITOR_TYPE, DEFAULT_VISITOR_TYPES),
        (VisitorLookupOption.KIND_VEHICLE_TYPE, DEFAULT_VEHICLE_TYPES),
    ):
        for code, label, sort_order in items:
            VisitorLookupOption.objects.get_or_create(
                kind=kind,
                code=code,
                location=None,
                defaults={
                    "label": label,
                    "is_default": True,
                    "sort_order": sort_order,
                    "is_active": True,
                },
            )


def propagate_to_orgs():
    globals_qs = VisitorLookupOption.objects.filter(location__isnull=True)
    for loc in Location.objects.filter(is_deleted=False):
        for global_row in globals_qs:
            VisitorLookupOption.objects.get_or_create(
                kind=global_row.kind,
                code=global_row.code,
                location=loc,
                defaults={
                    "label": global_row.label,
                    "is_default": global_row.is_default,
                    "sort_order": global_row.sort_order,
                    "is_active": global_row.is_active,
                },
            )


if __name__ == "__main__":
    seed_globals()
    propagate_to_orgs()
    print("Visitor lookup options populated.")
