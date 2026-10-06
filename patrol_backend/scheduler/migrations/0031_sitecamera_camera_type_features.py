# Generated manually for SiteCamera.camera_type / SiteCamera.features

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("scheduler", "0030_sitecamera_gate_mode"),
    ]

    operations = [
        migrations.AddField(
            model_name="sitecamera",
            name="camera_type",
            field=models.CharField(
                choices=[
                    ("vehicle", "Vehicle (number plate)"),
                    ("face", "Face"),
                ],
                default="vehicle",
                help_text="vehicle = number plate gate (ANPR); face = face features",
                max_length=16,
            ),
        ),
        migrations.AddField(
            model_name="sitecamera",
            name="features",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='Features for this camera type, e.g. ["face_attendance"]',
            ),
        ),
    ]
