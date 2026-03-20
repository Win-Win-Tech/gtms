from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('dashboard', '0004_alter_attendancecheckin_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='attendancecheckin',
            name='last_checkin_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancecheckin',
            name='last_checkout_time',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancecheckin',
            name='duration_minutes',
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='attendancecheckin',
            name='checkin_image',
            field=models.ImageField(blank=True, null=True, upload_to='attendance_checkins/'),
        ),
        migrations.AddField(
            model_name='attendancecheckin',
            name='checkout_image',
            field=models.ImageField(blank=True, null=True, upload_to='attendance_checkouts/'),
        ),
        migrations.AddField(
            model_name='checkinlog',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='attendance_checkinlog/'),
        ),
    ]

