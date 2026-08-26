from django.db import migrations, models


def forwards_copy_schedule(apps, schema_editor):
    Item = apps.get_model("reports", "LocationReportEmailItem")
    for item in Item.objects.all().iterator():
        st = item.schedule_type or "daily"
        item.schedule_types = [st]
        keys = {}
        if item.last_sent_schedule_key:
            keys[st] = item.last_sent_schedule_key
        item.last_sent_keys = keys
        item.save(update_fields=["schedule_types", "last_sent_keys"])


def backwards_noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0002_report_email_config"),
    ]

    operations = [
        migrations.AddField(
            model_name="locationreportemailitem",
            name="schedule_types",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text='List of schedule types, e.g. ["daily","weekly_sunday"]',
            ),
        ),
        migrations.AddField(
            model_name="locationreportemailitem",
            name="last_sent_keys",
            field=models.JSONField(
                blank=True,
                default=dict,
                help_text='Per-schedule last sent keys, e.g. {"daily":"2026-08-25-daily"}',
            ),
        ),
        migrations.AlterField(
            model_name="locationreportemailitem",
            name="schedule_type",
            field=models.CharField(
                choices=[
                    ("daily", "Every day"),
                    ("weekly_sunday", "Every Sunday"),
                    ("monthly_start", "1st of month"),
                ],
                default="daily",
                help_text="Legacy primary schedule; prefer schedule_types",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="locationreportemailitem",
            name="daily_period",
            field=models.CharField(
                choices=[("previous_day", "Previous day"), ("today", "Today")],
                default="previous_day",
                help_text="Used only when daily is among schedule_types",
                max_length=32,
            ),
        ),
        migrations.RunPython(forwards_copy_schedule, backwards_noop),
    ]
