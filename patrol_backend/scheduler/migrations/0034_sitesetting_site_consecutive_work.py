import django.db.models.deletion
from django.db import migrations, models


CONSECUTIVE_WORK_KEYS = [
    ("consecutive_work_limit_enabled", "false", None),
    ("max_consecutive_work_days", "6", "days"),
]


def seed_consecutive_work_settings(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    Location = apps.get_model("scheduler", "Location")

    for key, value, unit in CONSECUTIVE_WORK_KEYS:
        global_row = SiteSetting.objects.filter(
            key=key, location_id=None, site_id=None, is_deleted=False
        ).first()
        if not global_row:
            SiteSetting.objects.create(
                key=key,
                value=value,
                unit=unit,
                location_id=None,
                site_id=None,
                propagate_to_orgs=True,
                is_deleted=False,
            )

        org_rows = [
            SiteSetting(
                key=key,
                value=value,
                unit=unit,
                location_id=loc.id,
                site_id=None,
                propagate_to_orgs=False,
                is_deleted=False,
            )
            for loc in Location.objects.filter(is_deleted=False)
        ]
        if org_rows:
            SiteSetting.objects.bulk_create(org_rows, ignore_conflicts=True)


def unseed_consecutive_work_settings(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    keys = [k for k, _, _ in CONSECUTIVE_WORK_KEYS]
    SiteSetting.objects.filter(key__in=keys).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0033_vendor_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="site",
            field=models.ForeignKey(
                blank=True,
                help_text="When set, this row is a per-physical-site override. Lookup: site → org → global.",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="site_settings",
                to="scheduler.locationsite",
            ),
        ),
        migrations.AddIndex(
            model_name="sitesetting",
            index=models.Index(fields=["key", "site"], name="scheduler_s_key_site_idx"),
        ),
        migrations.RunPython(seed_consecutive_work_settings, unseed_consecutive_work_settings),
    ]
