from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0006_attendancecheckin_pa_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancecheckin",
            name="checkin_count",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="attendancecheckin",
            name="checkout_count",
            field=models.IntegerField(default=0),
        ),
    ]

