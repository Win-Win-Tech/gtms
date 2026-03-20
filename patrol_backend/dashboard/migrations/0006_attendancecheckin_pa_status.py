from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0005_attendancecheckin_v3_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancecheckin",
            name="pa_status",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=3,
                null=True,
                choices=[("P", "Present"), ("HA", "Half Day"), ("A", "Absent")],
            ),
        ),
    ]

