# Additive: SiteCamera for per-site CCTV config. Does not alter LocationSite.

import django.db.models.deletion
from django.db import migrations, models
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0027_remove_boundary_monitoring_enabled_setting"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteCamera",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("name", models.CharField(max_length=255)),
                ("rtsp_url", models.CharField(max_length=1024)),
                (
                    "direction",
                    models.CharField(
                        choices=[
                            ("toggle", "Toggle in/out"),
                            ("in", "Entry only"),
                            ("out", "Exit only"),
                        ],
                        default="toggle",
                        help_text="toggle = flip check-in/out; in/out = dedicated lane (future)",
                        max_length=16,
                    ),
                ),
                (
                    "is_enabled",
                    models.BooleanField(
                        default=True,
                        help_text="When False, skip sampling / hide from active monitoring",
                    ),
                ),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("stream_path", models.CharField(blank=True, default="", max_length=255)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="cameras",
                        to="scheduler.locationsite",
                    ),
                ),
            ],
            options={
                "ordering": ["sort_order", "name"],
            },
        ),
        migrations.AddIndex(
            model_name="sitecamera",
            index=models.Index(fields=["site", "is_enabled"], name="scheduler_s_site_id_cam_en_idx"),
        ),
    ]
