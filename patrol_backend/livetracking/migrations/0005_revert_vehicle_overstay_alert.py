# Revert Phase-3 vehicle_overstay TrackingAlert changes (overstay uses NotificationLog).

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def delete_vehicle_overstay_alerts(apps, schema_editor):
    TrackingAlert = apps.get_model("livetracking", "TrackingAlert")
    TrackingAlert.objects.filter(alert_type="vehicle_overstay").delete()


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("livetracking", "0004_vehicle_overstay_alert"),
    ]

    operations = [
        migrations.RunPython(delete_vehicle_overstay_alerts, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="trackingalert",
            name="alert_type",
            field=models.CharField(
                choices=[
                    ("boundary_breach", "Boundary breach"),
                    ("location_missing", "Location missing"),
                    ("manual_sos", "Manual SOS"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="trackingalert",
            name="subject_user",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="tracking_alerts_as_subject",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
