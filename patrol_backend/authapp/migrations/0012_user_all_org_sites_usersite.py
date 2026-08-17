from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("authapp", "0011_user_face_photo_face_encoding"),
        ("scheduler", "0020_location_is_ai_extraction_enabled"),
    ]

    operations = [
        migrations.AddField(
            model_name="user",
            name="all_org_sites",
            field=models.BooleanField(
                default=False,
                help_text="If true, user can access every site in their organisation (no UserSite rows needed).",
            ),
        ),
        migrations.CreateModel(
            name="UserSite",
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
                ("created_on", models.DateTimeField(auto_now_add=True)),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_sites",
                        to="scheduler.locationsite",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="user_sites",
                        to="authapp.user",
                    ),
                ),
            ],
            options={
                "unique_together": {("user", "site")},
            },
        ),
        migrations.AddIndex(
            model_name="usersite",
            index=models.Index(fields=["user", "site"], name="authapp_use_user_id_site_idx"),
        ),
    ]
