from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("livetracking", "0005_revert_vehicle_overstay_alert"),
    ]

    operations = [
        migrations.AddField(
            model_name="userlivelocation",
            name="boundary_outside_since",
            field=models.DateTimeField(
                blank=True,
                help_text="When the user first became continuously outside; cleared on re-entry.",
                null=True,
            ),
        ),
    ]
