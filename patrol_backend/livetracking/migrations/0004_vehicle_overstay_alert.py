# Vehicle overstay alert type + nullable subject_user

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("livetracking", "0003_sitealertrecipientconfig_subject_recipient_roles"),
    ]

    operations = [
        migrations.AlterField(
            model_name="trackingalert",
            name="alert_type",
            field=models.CharField(
                choices=[
                    ("boundary_breach", "Boundary breach"),
                    ("location_missing", "Location missing"),
                    ("manual_sos", "Manual SOS"),
                    ("vehicle_overstay", "Vehicle overstay"),
                ],
                db_index=True,
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="trackingalert",
            name="subject_user",
            field=models.ForeignKey(
                blank=True,
                help_text="Null for vehicle overstay (no subject person).",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tracking_alerts_as_subject",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
