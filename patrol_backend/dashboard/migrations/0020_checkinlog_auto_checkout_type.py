from django.db import migrations, models
from django.db.models import Q


def backfill_auto_checkout_type(apps, schema_editor):
    CheckInLog = apps.get_model("dashboard", "CheckInLog")
    qs = CheckInLog.objects.filter(type="checkout", is_auto_checkout=True)
    qs.filter(
        Q(auto_checkout_reason__icontains="shift end") | Q(auto_checkout_reason__icontains="grace")
    ).update(auto_checkout_type="shift_end")
    qs.filter(
        Q(auto_checkout_reason__icontains="boundary") | Q(auto_checkout_type__isnull=True)
    ).exclude(auto_checkout_type="shift_end").update(auto_checkout_type="boundary_breach")


def unfill_auto_checkout_type(apps, schema_editor):
    CheckInLog = apps.get_model("dashboard", "CheckInLog")
    CheckInLog.objects.filter(is_auto_checkout=True).update(auto_checkout_type=None)


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0019_publicholiday_attendance_ph_flag"),
    ]

    operations = [
        migrations.AddField(
            model_name="checkinlog",
            name="auto_checkout_type",
            field=models.CharField(
                blank=True,
                choices=[("shift_end", "Shift end"), ("boundary_breach", "Boundary breach")],
                db_index=True,
                max_length=32,
                null=True,
            ),
        ),
        migrations.RunPython(backfill_auto_checkout_type, unfill_auto_checkout_type),
    ]
