import uuid

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0036_labour_law_monthly_hours"),
    ]

    operations = [
        migrations.CreateModel(
            name="SiteShiftRoleCap",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                (
                    "role",
                    models.CharField(
                        help_text="Lowercase User.role, e.g. so / fo / guard",
                        max_length=32,
                    ),
                ),
                ("max_count", models.PositiveIntegerField()),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "shift",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="site_role_caps",
                        to="scheduler.shift",
                    ),
                ),
                (
                    "site",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="shift_role_caps",
                        to="scheduler.locationsite",
                    ),
                ),
            ],
        ),
        migrations.AddConstraint(
            model_name="siteshiftrolecap",
            constraint=models.UniqueConstraint(
                fields=("site", "shift", "role"),
                name="uniq_site_shift_role_cap",
            ),
        ),
        migrations.AddIndex(
            model_name="siteshiftrolecap",
            index=models.Index(fields=["site", "shift"], name="idx_site_shift_role_cap"),
        ),
    ]
