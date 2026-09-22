# Generated manually for vehicle overstay Phase 2

import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


SETTING_KEY = "vehicle_overstay_hours"
SETTING_DEFAULT = "4"
SETTING_UNIT = "h"


def seed_overstay_hours(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    Location = apps.get_model("scheduler", "Location")

    global_row, created = SiteSetting.objects.get_or_create(
        key=SETTING_KEY,
        location_id=None,
        defaults={
            "value": SETTING_DEFAULT,
            "unit": SETTING_UNIT,
            "propagate_to_orgs": True,
            "is_deleted": False,
        },
    )
    if not created:
        updated = False
        if not global_row.propagate_to_orgs:
            global_row.propagate_to_orgs = True
            updated = True
        if not (global_row.unit or "").strip():
            global_row.unit = SETTING_UNIT
            updated = True
        if updated:
            global_row.save()

    existing_org_ids = set(
        SiteSetting.objects.filter(key=SETTING_KEY, location_id__isnull=False).values_list(
            "location_id", flat=True
        )
    )
    for loc in Location.objects.filter(is_deleted=False):
        if loc.id in existing_org_ids:
            continue
        SiteSetting.objects.create(
            key=SETTING_KEY,
            value=SETTING_DEFAULT,
            unit=SETTING_UNIT,
            location_id=loc.id,
            propagate_to_orgs=False,
            is_deleted=False,
        )


def unseed_overstay_hours(apps, schema_editor):
    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    SiteSetting.objects.filter(key=SETTING_KEY).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("visitor", "0012_anpr_cctv_gate"),
        ("scheduler", "0030_sitecamera_gate_mode"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="visitorentry",
            name="overstay_alert_sent_at",
            field=models.DateTimeField(
                blank=True,
                help_text="When vehicle overstay SOS was sent (once per checked-in visit).",
                null=True,
            ),
        ),
        migrations.AddIndex(
            model_name="visitorentry",
            index=models.Index(
                fields=["status", "overstay_alert_sent_at", "check_in_time"],
                name="visitor_overstay_chk_idx",
            ),
        ),
        migrations.CreateModel(
            name="VehicleOverstayWhitelist",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=False,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("vehicle_number", models.CharField(db_index=True, max_length=32)),
                ("notes", models.CharField(blank=True, default="", max_length=255)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                ("is_deleted", models.BooleanField(default=False)),
                ("deleted_on", models.DateTimeField(blank=True, null=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="vehicle_overstay_whitelist_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "deleted_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="vehicle_overstay_whitelist_deleted",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="vehicle_overstay_whitelist",
                        to="scheduler.location",
                    ),
                ),
            ],
            options={
                "ordering": ["-created_on"],
            },
        ),
        migrations.AddIndex(
            model_name="vehicleoverstaywhitelist",
            index=models.Index(
                fields=["location", "is_deleted"],
                name="visitor_wl_loc_del_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="vehicleoverstaywhitelist",
            index=models.Index(
                fields=["location", "vehicle_number"],
                name="visitor_wl_loc_plate_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="vehicleoverstaywhitelist",
            constraint=models.UniqueConstraint(
                condition=models.Q(("is_deleted", False)),
                fields=("location", "vehicle_number"),
                name="unique_overstay_whitelist_plate_per_location_active",
            ),
        ),
        migrations.RunPython(seed_overstay_hours, unseed_overstay_hours),
    ]
