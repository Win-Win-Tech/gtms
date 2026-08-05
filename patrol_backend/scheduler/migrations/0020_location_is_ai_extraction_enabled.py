# Generated for AI extraction enable flag

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0019_remove_locationsite_radius"),
    ]

    operations = [
        migrations.AddField(
            model_name="location",
            name="is_ai_extraction_enabled",
            field=models.BooleanField(
                default=False,
                help_text="If True, AI OCR auto-extraction (ID/vehicle) is enabled for this location/organization.",
            ),
        ),
    ]
