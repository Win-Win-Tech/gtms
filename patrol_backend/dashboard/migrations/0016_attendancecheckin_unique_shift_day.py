# Dedupe attendance rows then enforce one row per guard/shift day.

from django.db import migrations, models
from django.db.models import Count, Q


def dedupe_attendance_shift_day_rows(apps, schema_editor):
    AttendanceCheckin = apps.get_model("dashboard", "AttendanceCheckin")
    duplicate_groups = (
        AttendanceCheckin.objects.filter(shift_date__isnull=False)
        .values("guard_id", "assignment_id", "shift_id", "org_location_id", "shift_date")
        .annotate(row_count=Count("id"))
        .filter(row_count__gt=1)
    )
    for group in duplicate_groups:
        rows = list(
            AttendanceCheckin.objects.filter(
                guard_id=group["guard_id"],
                assignment_id=group["assignment_id"],
                shift_id=group["shift_id"],
                org_location_id=group["org_location_id"],
                shift_date=group["shift_date"],
            ).order_by("-modified_on", "-created_on")
        )
        for duplicate in rows[1:]:
            duplicate.delete()


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0015_rename_dashboard_a_locatio_7c8f9a_idx_dashboard_a_locatio_bd039d_idx_and_more"),
    ]

    operations = [
        migrations.RunPython(dedupe_attendance_shift_day_rows, migrations.RunPython.noop),
        migrations.AddConstraint(
            model_name="attendancecheckin",
            constraint=models.UniqueConstraint(
                condition=Q(shift_date__isnull=False),
                fields=("guard", "assignment", "shift", "org_location", "shift_date"),
                name="uniq_attendance_guard_shift_day",
            ),
        ),
    ]
