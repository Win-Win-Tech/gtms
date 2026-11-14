# Shift ↔ sites: applies_to_all_sites + M2M sites (existing rows stay all-sites).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0038_seed_boundary_outside_buffer_min"),
    ]

    operations = [
        migrations.AddField(
            model_name="shift",
            name="applies_to_all_sites",
            field=models.BooleanField(
                default=True,
                help_text="When True, shift is available on every site in the organisation.",
            ),
        ),
        migrations.AddField(
            model_name="shift",
            name="sites",
            field=models.ManyToManyField(
                blank=True,
                help_text="Sites this shift applies to when applies_to_all_sites is False.",
                related_name="shifts",
                to="scheduler.locationsite",
            ),
        ),
    ]
