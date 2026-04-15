# Generated manually for AttendanceWeekOff

import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("scheduler", "0016_location_is_face_attendance_enabled"),
        ("dashboard", "0013_rename_dashboard_g_app_lab_ab52b4_idx_dashboard_g_app_lab_7e7664_idx_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AttendanceWeekOff",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("weekoff_date", models.DateField(db_index=True)),
                ("mark", models.CharField(default="W", max_length=3)),
                (
                    "source",
                    models.CharField(
                        choices=[("excel_upload", "Excel upload"), ("api", "API")],
                        db_index=True,
                        default="excel_upload",
                        max_length=20,
                    ),
                ),
                ("upload_batch_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="attendance_weekoffs_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attendance_weekoffs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attendance_weekoffs",
                        to="scheduler.location",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="attendanceweekoff",
            constraint=models.UniqueConstraint(
                fields=("user", "location", "weekoff_date"),
                name="uniq_attendance_weekoff_user_location_date",
            ),
        ),
        migrations.AddIndex(
            model_name="attendanceweekoff",
            index=models.Index(fields=["location", "weekoff_date"], name="dashboard_a_locatio_7c8f9a_idx"),
        ),
        migrations.AddIndex(
            model_name="attendanceweekoff",
            index=models.Index(fields=["user", "weekoff_date"], name="dashboard_a_user_id_8d9e0b_idx"),
        ),
    ]
