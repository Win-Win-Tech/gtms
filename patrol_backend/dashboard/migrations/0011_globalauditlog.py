from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0010_attendancecheckin_pa_status_m"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="GlobalAuditLog",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("app_label", models.CharField(db_index=True, max_length=100)),
                ("model_name", models.CharField(db_index=True, max_length=120)),
                ("object_pk", models.CharField(db_index=True, max_length=100)),
                ("event_type", models.CharField(choices=[("create", "Create"), ("update", "Update"), ("delete", "Delete")], db_index=True, max_length=10)),
                ("location_id", models.UUIDField(blank=True, db_index=True, null=True)),
                ("old_data", models.JSONField(blank=True, null=True)),
                ("new_data", models.JSONField(blank=True, null=True)),
                ("changed_fields", models.JSONField(blank=True, null=True)),
                ("changed_on", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("source", models.CharField(db_index=True, default="api", max_length=30)),
                (
                    "changed_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="global_audit_logs",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-changed_on"],
                "indexes": [
                    models.Index(fields=["app_label", "model_name", "object_pk"], name="dashboard_g_app_lab_ab52b4_idx"),
                    models.Index(fields=["location_id", "changed_on"], name="dashboard_g_locatio_67c7a6_idx"),
                    models.Index(fields=["event_type", "changed_on"], name="dashboard_g_event_t_6d76bf_idx"),
                ],
            },
        ),
    ]



