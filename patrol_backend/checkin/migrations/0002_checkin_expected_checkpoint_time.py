# Generated manually for adding expected_checkpoint_time field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('checkin', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='checkin',
            name='expected_checkpoint_time',
            field=models.TimeField(blank=True, null=True),
        ),
    ]

