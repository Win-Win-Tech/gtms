# Add vehicle_overstay report code choice

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0004_schedule_period_options"),
    ]

    operations = [
        migrations.AlterField(
            model_name="locationreportemailitem",
            name="report_code",
            field=models.CharField(
                choices=[
                    ("checkin", "QR Scan Patrol Report"),
                    ("attendance", "Attendance Report"),
                    ("rollcall", "Roll Call Report"),
                    ("incident", "Incident Report"),
                    ("visitor_entries", "Visitor Entries"),
                    ("vehicle_movement", "Vehicle Movement"),
                    ("vehicle_overstay", "Vehicle Overstay"),
                    ("monthly_attendance", "Monthly Attendance Summary"),
                    ("monthly_location", "Monthly Location Summary"),
                ],
                max_length=64,
            ),
        ),
    ]
