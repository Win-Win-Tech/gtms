from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0017_checkinlog_auto_checkout_reason_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="attendancecheckin",
            index=models.Index(
                fields=["guard", "shift_date"],
                name="att_guard_shiftdate_idx",
            ),
        ),
    ]
