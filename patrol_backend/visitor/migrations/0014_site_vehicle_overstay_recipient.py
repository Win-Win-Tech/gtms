# Per-site recipient roles for vehicle overstay SOS

import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("authapp", "0015_rename_authapp_use_user_id_site_idx_authapp_use_user_id_52d367_idx"),
        ("scheduler", "0030_sitecamera_gate_mode"),
        ("visitor", "0013_vehicle_overstay_phase2"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteVehicleOverstayRecipient",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "recipient_role",
                    models.ForeignKey(
                        help_text="Role that should receive vehicle overstay SOS for this site",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="site_vehicle_overstay_as_recipient",
                        to="authapp.role",
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vehicle_overstay_recipients",
                        to="scheduler.locationsite",
                    ),
                ),
            ],
            options={
                "ordering": ["site__name", "recipient_role__name"],
                "unique_together": {("site", "recipient_role")},
            },
        ),
        migrations.AddIndex(
            model_name="sitevehicleoverstayrecipient",
            index=models.Index(fields=["site"], name="visitor_sit_site_id_a8f0c1_idx"),
        ),
    ]
