from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0021_assignment_daily_site_guard_site_cache"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitesetting",
            name="propagate_to_orgs",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "If True (and location is NULL), this global key is copied to each organisation. "
                    "If False, the key stays global-only (e.g. app_version / force_update)."
                ),
            ),
        ),
    ]
