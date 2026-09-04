# Remove unused legacy SiteSetting key boundary_monitoring_enabled.
# Enable is per-site (LocationSite.breach_alerts_enabled); this key is not read at runtime.

from django.db import migrations

KEY = "boundary_monitoring_enabled"


def delete_boundary_monitoring_enabled(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    SiteSetting.objects.filter(key=KEY).delete()


def noop_reverse(apps, schema_editor):
    # Do not re-seed; key is obsolete. Reverse is intentionally empty.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0026_locationsite_per_site_alert_enables"),
    ]

    operations = [
        migrations.RunPython(delete_boundary_monitoring_enabled, noop_reverse),
    ]
