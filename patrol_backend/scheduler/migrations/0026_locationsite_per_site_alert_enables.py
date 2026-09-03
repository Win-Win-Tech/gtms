# Per-site enable for breach and location-missing alerts.

from django.db import migrations, models


def copy_boundary_enabled(apps, schema_editor):
    LocationSite = apps.get_model("scheduler", "LocationSite")
    LocationSite.objects.filter(boundary_enabled=True).update(
        breach_alerts_enabled=True,
        location_missing_alerts_enabled=True,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0025_seed_alert_type_enable_settings"),
    ]

    operations = [
        migrations.AddField(
            model_name="locationsite",
            name="breach_alerts_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Per-site on/off for boundary breach alerts",
            ),
        ),
        migrations.AddField(
            model_name="locationsite",
            name="location_missing_alerts_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Per-site on/off for location-missing alerts",
            ),
        ),
        migrations.AlterField(
            model_name="locationsite",
            name="boundary_enabled",
            field=models.BooleanField(
                default=False,
                help_text="Derived: True when breach or location-missing alerts are enabled for this site",
            ),
        ),
        migrations.RunPython(copy_boundary_enabled, migrations.RunPython.noop),
    ]
