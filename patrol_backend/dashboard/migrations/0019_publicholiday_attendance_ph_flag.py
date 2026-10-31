import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("scheduler", "0036_labour_law_monthly_hours"),
        ("dashboard", "0018_attendancecheckin_guard_shift_date_idx"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancecheckin",
            name="worked_on_public_holiday",
            field=models.BooleanField(db_index=True, default=False),
        ),
        migrations.AddField(
            model_name="attendancecheckin",
            name="public_holiday_name",
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.CreateModel(
            name="PublicHoliday",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("holiday_date", models.DateField(db_index=True)),
                ("name", models.CharField(max_length=255)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="public_holidays_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "modified_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="public_holidays_modified",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "org_location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_holidays",
                        to="scheduler.location",
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_holidays",
                        to="scheduler.locationsite",
                    ),
                ),
                (
                    "vendor",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="public_holidays",
                        to="scheduler.vendor",
                    ),
                ),
            ],
        ),
        migrations.AddIndex(
            model_name="publicholiday",
            index=models.Index(fields=["site", "holiday_date", "vendor"], name="ph_site_date_vendor_idx"),
        ),
        migrations.AddIndex(
            model_name="publicholiday",
            index=models.Index(fields=["org_location", "holiday_date"], name="ph_org_date_idx"),
        ),
    ]
