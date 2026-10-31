"""Site / org / global SiteSetting writes for the settings page.

Runtime lookup for most keys is still org → global. Labour-law already
resolves site → org → global. Other keys will follow that same chain later.
"""

from django.db.models import F, Q

from scheduler.models import SiteSetting


def overlay_site_setting_rows(base_rows, site_rows):
    """Pair each org/global row with a site override when one exists.

    Returns list of (row, inherited) where inherited is True when the org/global
    value is used because this site has no override yet.
    """
    overrides = {str(row.key): row for row in (site_rows or [])}
    result = []
    for row in base_rows or []:
        site_row = overrides.get(str(row.key))
        if site_row is not None:
            result.append((site_row, False))
        else:
            result.append((row, True))
    return result


def upsert_setting_value(
    key,
    value,
    unit=None,
    *,
    location_id=None,
    site_id=None,
    user=None,
):
    """Write one key at site scope, or org/global when site_id is omitted.

    Site rows keep location_id=None so unique_together (key, location) still
    allows one override per physical site.
    """
    key = str(key or "").strip()
    if not key:
        raise ValueError("key is required")

    qs = SiteSetting.objects.filter(key=key, is_deleted=False)
    if site_id:
        qs = qs.filter(site_id=site_id)
    else:
        qs = qs.filter(site_id__isnull=True, location_id=location_id)
    row = qs.first()
    if row:
        row.value = "" if value is None else str(value)
        if unit is not None:
            row.unit = unit
        row.modified_by = user
        row.save(update_fields=["value", "unit", "modified_by", "modified_on"])
        return row

    catalog = SiteSetting.objects.filter(key=key, is_deleted=False, site_id__isnull=True)
    if location_id:
        catalog = catalog.filter(Q(location_id=location_id) | Q(location_id__isnull=True))
    else:
        catalog = catalog.filter(location_id__isnull=True)
    template = catalog.order_by(F("location_id").desc(nulls_last=True)).first()
    if template is None and not site_id:
        raise ValueError(f"Unknown setting key '{key}'")
    if site_id and template is not None and template.location_id is None and template.propagate_to_orgs is False:
        raise ValueError(f"'{key}' is global-only and cannot be overridden per site")

    return SiteSetting.objects.create(
        key=key,
        value="" if value is None else str(value),
        unit=unit if unit is not None else (template.unit if template else None),
        location_id=None if site_id else location_id,
        site_id=site_id,
        propagate_to_orgs=False,
        created_by=user,
    )
