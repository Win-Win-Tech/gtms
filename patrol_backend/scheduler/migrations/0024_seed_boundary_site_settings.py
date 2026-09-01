# Seed global SiteSetting keys for site boundary monitoring (propagate to orgs).

from django.db import migrations


BOUNDARY_KEYS = [
    ("boundary_monitoring_enabled", "false", None),
    ("boundary_exit_buffer_m", "15", "m"),
    ("boundary_still_outside_reminder_min", "0", "min"),
    ("location_missing_timeout_min", "10", "min"),
]


def seed_boundary_site_settings(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    Location = apps.get_model("scheduler", "Location")

    for key, value, unit in BOUNDARY_KEYS:
        global_row, created = SiteSetting.objects.get_or_create(
            key=key,
            location_id=None,
            defaults={
                "value": value,
                "unit": unit,
                "propagate_to_orgs": True,
                "is_deleted": False,
            },
        )
        if not created:
            continue

        org_rows = []
        for loc in Location.objects.filter(is_deleted=False):
            org_rows.append(
                SiteSetting(
                    key=key,
                    value=value,
                    unit=unit,
                    location_id=loc.id,
                    propagate_to_orgs=False,
                    is_deleted=False,
                )
            )
        if org_rows:
            SiteSetting.objects.bulk_create(org_rows, ignore_conflicts=True)


def unseed_boundary_site_settings(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    keys = [k for k, _, _ in BOUNDARY_KEYS]
    SiteSetting.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0023_locationsite_boundary_enabled_and_more"),
    ]

    operations = [
        migrations.RunPython(seed_boundary_site_settings, unseed_boundary_site_settings),
    ]
