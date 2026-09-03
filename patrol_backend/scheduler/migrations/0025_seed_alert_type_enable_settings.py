# Seed per-alert-type enable toggles (propagate to orgs).

from django.db import migrations


ALERT_TYPE_KEYS = [
    ("boundary_breach_alerts_enabled", "false", None),
    ("location_missing_alerts_enabled", "false", None),
]


def seed_alert_type_settings(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    Location = apps.get_model("scheduler", "Location")

    for key, value, unit in ALERT_TYPE_KEYS:
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
            if not global_row.propagate_to_orgs:
                global_row.propagate_to_orgs = True
                global_row.save(update_fields=["propagate_to_orgs"])

        existing_org_ids = set(
            SiteSetting.objects.filter(key=key, location_id__isnull=False).values_list(
                "location_id", flat=True
            )
        )
        # Prefer copying existing monitoring_enabled into both new keys for smoother upgrade
        for loc in Location.objects.filter(is_deleted=False):
            if loc.id in existing_org_ids:
                continue
            monitoring = (
                SiteSetting.objects.filter(
                    key="boundary_monitoring_enabled",
                    location_id=loc.id,
                    is_deleted=False,
                )
                .values_list("value", flat=True)
                .first()
            )
            seed_value = monitoring if monitoring is not None else value
            SiteSetting.objects.create(
                key=key,
                value=seed_value,
                unit=unit,
                location_id=loc.id,
                propagate_to_orgs=False,
                is_deleted=False,
            )


def unseed_alert_type_settings(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    keys = [k for k, _, _ in ALERT_TYPE_KEYS]
    SiteSetting.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0024_seed_boundary_site_settings"),
    ]

    operations = [
        migrations.RunPython(seed_alert_type_settings, unseed_alert_type_settings),
    ]
