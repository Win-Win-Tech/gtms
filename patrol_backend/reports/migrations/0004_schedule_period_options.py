from django.db import migrations, models


def default_monthly_send_days():
    return [1]


def default_last_n_send_days():
    return [7, 14, 21, 28]


def normalize_schedule_types(apps, schema_editor):
    Item = apps.get_model("reports", "LocationReportEmailItem")
    for item in Item.objects.all():
        raw = item.schedule_types if isinstance(item.schedule_types, list) else []
        if not raw and item.schedule_type:
            raw = [item.schedule_type]
        cleaned = []
        for t in raw:
            if t == "weekly_sunday":
                t = "weekly"
            elif t == "monthly_start":
                t = "monthly"
            if t in {"daily", "weekly", "monthly", "last_n_days"} and t not in cleaned:
                cleaned.append(t)
        if not cleaned:
            cleaned = ["daily"]
        item.schedule_types = cleaned
        item.schedule_type = cleaned[0]
        item.weekly_weekday = getattr(item, "weekly_weekday", None) or 6
        days = getattr(item, "monthly_send_days", None)
        if not isinstance(days, list) or not days:
            item.monthly_send_days = [1]
        n_days = getattr(item, "last_n_days_send_days", None)
        if not isinstance(n_days, list) or not n_days:
            item.last_n_days_send_days = [7, 14, 21, 28]
        if not getattr(item, "last_n_days_count", None):
            item.last_n_days_count = 7
        item.save(
            update_fields=[
                "schedule_types",
                "schedule_type",
                "weekly_weekday",
                "monthly_send_days",
                "last_n_days_send_days",
                "last_n_days_count",
            ]
        )


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0003_schedule_types_and_last_sent_keys"),
    ]

    operations = [
        migrations.AddField(
            model_name="locationreportemailitem",
            name="weekly_weekday",
            field=models.PositiveSmallIntegerField(
                default=6,
                help_text="0=Monday … 6=Sunday; used when weekly is selected",
            ),
        ),
        migrations.AddField(
            model_name="locationreportemailitem",
            name="weekly_include_current_day",
            field=models.BooleanField(
                default=False,
                help_text="If false, weekly window ends yesterday; if true, ends today",
            ),
        ),
        migrations.AddField(
            model_name="locationreportemailitem",
            name="monthly_send_days",
            field=models.JSONField(
                blank=True,
                default=default_monthly_send_days,
                help_text="Days of month (1–28) when monthly schedule sends",
            ),
        ),
        migrations.AddField(
            model_name="locationreportemailitem",
            name="last_n_days_count",
            field=models.PositiveSmallIntegerField(
                default=7,
                help_text="Rolling lookback length for last_n_days schedule (1–90)",
            ),
        ),
        migrations.AddField(
            model_name="locationreportemailitem",
            name="last_n_days_send_days",
            field=models.JSONField(
                blank=True,
                default=default_last_n_send_days,
                help_text="Days of month (1–28) when last_n_days schedule sends",
            ),
        ),
        migrations.AddField(
            model_name="locationreportemailitem",
            name="last_n_days_include_current_day",
            field=models.BooleanField(
                default=False,
                help_text="If false, last-N window ends yesterday; if true, ends today",
            ),
        ),
        migrations.AlterField(
            model_name="locationreportemailitem",
            name="schedule_type",
            field=models.CharField(
                choices=[
                    ("daily", "Every day"),
                    ("weekly", "Weekly"),
                    ("monthly", "Monthly"),
                    ("last_n_days", "Last N days"),
                    ("weekly_sunday", "Every Sunday (legacy)"),
                    ("monthly_start", "1st of month (legacy)"),
                ],
                default="daily",
                help_text="Legacy primary schedule; prefer schedule_types",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="locationreportemailitem",
            name="schedule_types",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='List of schedule types, e.g. ["daily","weekly","last_n_days"]',
            ),
        ),
        migrations.RunPython(normalize_schedule_types, noop_reverse),
    ]
