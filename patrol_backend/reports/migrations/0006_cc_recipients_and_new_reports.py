# Add org-level CC recipients + Public Holiday Worked / Auto Checkout / Employee Duration report codes

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0005_vehicle_overstay_report"),
    ]

    operations = [
        migrations.AddField(
            model_name="locationreportemailconfig",
            name="cc_recipients",
            field=models.TextField(
                blank=True,
                default="",
                help_text="Comma-separated CC email addresses",
            ),
        ),
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
                    ("public_holiday_worked", "Public Holiday Worked"),
                    ("auto_checkout", "Auto Checkout"),
                    ("employee_duration", "Employee Duration Report"),
                ],
                max_length=64,
            ),
        ),
    ]
