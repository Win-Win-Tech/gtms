# Generated manually for CctvFaceEvent (Phase A2)

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("scheduler", "0031_sitecamera_camera_type_features"),
    ]

    operations = [
        migrations.CreateModel(
            name="CctvFaceEvent",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("camera_name", models.CharField(blank=True, default="", max_length=255)),
                (
                    "distance",
                    models.FloatField(
                        blank=True,
                        help_text="FAISS Euclidean match distance (lower is closer, <= 0.38 is strict match)",
                        null=True,
                    ),
                ),
                (
                    "result",
                    models.CharField(
                        choices=[
                            ("recognised", "Recognised"),
                            ("unknown", "Unknown"),
                            ("too_small", "Face too small"),
                            ("no_shift", "No active shift today"),
                            ("cooldown", "Within cooldown period"),
                            ("checkin", "Check-in marked"),
                            ("checkout", "Check-out marked"),
                            ("already_in", "Already checked in"),
                            ("no_open_session", "No open session to checkout"),
                        ],
                        default="unknown",
                        max_length=50,
                    ),
                ),
                (
                    "snapshot",
                    models.CharField(
                        blank=True,
                        default="",
                        help_text="Relative path to debug / snapshot crop",
                        max_length=512,
                    ),
                ),
                ("track_id", models.CharField(blank=True, default="", max_length=64)),
                ("face_px", models.IntegerField(blank=True, null=True)),
                ("detect_score", models.FloatField(blank=True, null=True)),
                ("created_on", models.DateTimeField(auto_now_add=True, db_index=True)),
                (
                    "camera",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="face_events",
                        to="scheduler.sitecamera",
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cctv_face_events",
                        to="scheduler.locationsite",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="cctv_face_events",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-created_on"],
                "indexes": [
                    models.Index(fields=["site", "-created_on"], name="scheduler_c_site_id_a87c12_idx"),
                    models.Index(fields=["user", "-created_on"], name="scheduler_c_user_id_44f198_idx"),
                    models.Index(fields=["camera", "-created_on"], name="scheduler_c_camera__12b390_idx"),
                ],
            },
        ),
    ]
