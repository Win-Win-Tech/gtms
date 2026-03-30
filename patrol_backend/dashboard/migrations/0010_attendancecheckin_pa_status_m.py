from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("dashboard", "0009_pa_status_ow_remove_ha_cleanup_settings"),
    ]

    operations = [
        migrations.AlterField(
            model_name="attendancecheckin",
            name="pa_status",
            field=models.CharField(
                blank=True,
                choices=[("P", "Present"), ("A", "Absent"), ("OW", "On Work"), ("M", "Missed Checkout")],
                db_index=True,
                max_length=3,
                null=True,
            ),
        ),
    ]

