# Generated manually for vehicle_type + company_name on VisitorEntry

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("visitor", "0006_device_token_notification_log_scanned_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="visitorentry",
            name="vehicle_type",
            field=models.CharField(
                blank=True,
                choices=[
                    ("car", "Car"),
                    ("motorcycle", "Motorcycle"),
                    ("van", "Van"),
                    ("truck", "Truck"),
                    ("bus", "Bus"),
                    ("other", "Other"),
                ],
                default="",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="visitorentry",
            name="company_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
    ]
