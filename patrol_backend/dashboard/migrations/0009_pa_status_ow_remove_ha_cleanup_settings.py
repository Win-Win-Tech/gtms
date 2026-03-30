from django.db import migrations, models


def forwards(apps, schema_editor):
    AttendanceCheckin = apps.get_model("dashboard", "AttendanceCheckin")
    AttendanceCheckin.objects.filter(pa_status="HA").update(pa_status="A")

    SiteSetting = apps.get_model("scheduler", "SiteSetting")
    SiteSetting.objects.filter(
        key__in=("absent_max_hours", "half_day_max_hours", "present_min_hours"),
        is_deleted=False,
    ).update(is_deleted=True)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0008_attendancecheckin_shift_date"),
        ("scheduler", "0015_checklist_models"),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="attendancecheckin",
            name="pa_status",
            field=models.CharField(
                blank=True,
                choices=[("P", "Present"), ("A", "Absent"), ("OW", "On Work")],
                db_index=True,
                max_length=3,
                null=True,
            ),
        ),
    ]
