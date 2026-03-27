from datetime import timedelta

import pytz
from django.conf import settings
from django.db import migrations, models


def _compute_shift_date(checkin_dt, shift, tz):
    if not checkin_dt or not shift:
        return None
    local_dt = checkin_dt.astimezone(tz)
    local_date = local_dt.date()
    local_time = local_dt.time()
    is_overnight = shift.end_time <= shift.start_time
    if is_overnight and local_time < shift.end_time:
        return local_date - timedelta(days=1)
    return local_date


def backfill_shift_date(apps, schema_editor):
    AttendanceCheckin = apps.get_model("dashboard", "AttendanceCheckin")
    default_tz = pytz.timezone(getattr(settings, "TIME_ZONE", "Asia/Kolkata"))

    qs = AttendanceCheckin.objects.select_related("shift").all().only(
        "id", "checkin_time", "created_on", "shift_id", "shift_date"
    )
    updates = []
    for row in qs.iterator(chunk_size=1000):
        base_dt = row.checkin_time or row.created_on
        shift_date = _compute_shift_date(base_dt, row.shift, default_tz)
        if shift_date and row.shift_date != shift_date:
            row.shift_date = shift_date
            updates.append(row)
        if len(updates) >= 1000:
            AttendanceCheckin.objects.bulk_update(updates, ["shift_date"])
            updates = []
    if updates:
        AttendanceCheckin.objects.bulk_update(updates, ["shift_date"])


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0007_attendancecheckin_log_counts"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancecheckin",
            name="shift_date",
            field=models.DateField(blank=True, db_index=True, null=True),
        ),
        migrations.RunPython(backfill_shift_date, migrations.RunPython.noop),
    ]

