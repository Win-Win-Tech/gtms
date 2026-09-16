# Generated manually for SiteCamera.gate_mode

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0029_anpr_cctv_gate"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitecamera",
            name="gate_mode",
            field=models.CharField(
                choices=[
                    ("parked_toggle", "Whenever plate is seen (good for testing)"),
                    ("line_direction", "Only when vehicle crosses the line"),
                ],
                default="parked_toggle",
                help_text=(
                    "When to record: whenever plate is seen (testing), "
                    "or only when vehicle crosses the virtual line"
                ),
                max_length=32,
            ),
        ),
    ]
