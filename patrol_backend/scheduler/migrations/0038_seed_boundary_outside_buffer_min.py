# Seed boundary_outside_buffer_min (site → org → global); default 5 minutes.

from django.db import migrations

KEY = "boundary_outside_buffer_min"
DEFAULT_VALUE = "5"
UNIT = "min"


def seed_outside_buffer(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    Location = apps.get_model("scheduler", "Location")

    global_row, created = SiteSetting.objects.get_or_create(
        key=KEY,
        location_id=None,
        site_id=None,
        defaults={
            "value": DEFAULT_VALUE,
            "unit": UNIT,
            "propagate_to_orgs": True,
            "is_deleted": False,
        },
    )
    if not created:
        if not global_row.propagate_to_orgs:
            global_row.propagate_to_orgs = True
            global_row.save(update_fields=["propagate_to_orgs"])
        if global_row.unit != UNIT:
            global_row.unit = UNIT
            global_row.save(update_fields=["unit"])

    existing_orgs = set(
        SiteSetting.objects.filter(key=KEY, site_id=None)
        .exclude(location_id=None)
        .values_list("location_id", flat=True)
    )
    org_rows = [
        SiteSetting(
            key=KEY,
            value=DEFAULT_VALUE,
            unit=UNIT,
            location_id=loc.id,
            site_id=None,
            propagate_to_orgs=False,
            is_deleted=False,
        )
        for loc in Location.objects.filter(is_deleted=False)
        if loc.id not in existing_orgs
    ]
    if org_rows:
        SiteSetting.objects.bulk_create(org_rows, ignore_conflicts=True)


def unseed_outside_buffer(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    SiteSetting.objects.filter(key=KEY).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0037_siteshiftrolecap"),
    ]

    operations = [
        migrations.RunPython(seed_outside_buffer, unseed_outside_buffer),
    ]
