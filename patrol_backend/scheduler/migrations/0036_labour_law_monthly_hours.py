from django.db import migrations


OLD_ENABLED_KEY = "consecutive_work_limit_enabled"
NEW_ENABLED_KEY = "labour_law_limit_enabled"
HOURS_KEY = "max_monthly_work_hours"
HOURS_DEFAULT = "208"
HOURS_UNIT = "h"


def rename_enable_key_and_seed_hours(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    Location = apps.get_model("scheduler", "Location")

    SiteSetting.objects.filter(key=OLD_ENABLED_KEY).update(key=NEW_ENABLED_KEY)

    global_hours = SiteSetting.objects.filter(
        key=HOURS_KEY, location_id=None, site_id=None, is_deleted=False
    ).first()
    if not global_hours:
        SiteSetting.objects.create(
            key=HOURS_KEY,
            value=HOURS_DEFAULT,
            unit=HOURS_UNIT,
            location_id=None,
            site_id=None,
            propagate_to_orgs=True,
            is_deleted=False,
        )

    existing_orgs = set(
        SiteSetting.objects.filter(key=HOURS_KEY, site_id=None)
        .exclude(location_id=None)
        .values_list("location_id", flat=True)
    )
    org_rows = [
        SiteSetting(
            key=HOURS_KEY,
            value=HOURS_DEFAULT,
            unit=HOURS_UNIT,
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


def undo_rename_and_unseed_hours(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    SiteSetting.objects.filter(key=HOURS_KEY).delete()
    SiteSetting.objects.filter(key=NEW_ENABLED_KEY).update(key=OLD_ENABLED_KEY)


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0035_alter_sitesetting_options_and_more"),
    ]

    operations = [
        migrations.RunPython(rename_enable_key_and_seed_hours, undo_rename_and_unseed_hours),
    ]
