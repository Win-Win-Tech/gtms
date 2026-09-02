"""Sync propagatable global SiteSetting templates to organisation (Location) rows."""

from django.db.models import Exists, OuterRef

from scheduler.models import SiteSetting


def sync_propagatable_settings_for_location(location_id, user=None):
    """
    Ensure `location_id` has org overrides for every global key with propagate_to_orgs=True.
    Called when an org admin lists settings and when a new organisation is created.
    """
    if not location_id:
        return 0

    missing_settings = SiteSetting.objects.filter(
        location__isnull=True,
        is_deleted=False,
        propagate_to_orgs=True,
    ).exclude(
        Exists(
            SiteSetting.objects.filter(
                key=OuterRef('key'),
                location_id=location_id,
                is_deleted=False,
            )
        )
    )

    to_create = []
    for global_row in missing_settings:
        to_create.append(
            SiteSetting(
                key=global_row.key,
                value=global_row.value,
                unit=global_row.unit,
                location_id=location_id,
                propagate_to_orgs=False,
                created_by=user,
            )
        )

    if to_create:
        SiteSetting.objects.bulk_create(to_create, ignore_conflicts=True)

    return len(to_create)
