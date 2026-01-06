# Generated migration for timezone field

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('authapp', '0007_alter_user_aadhar_no'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='timezone',
            field=models.CharField(
                default='Asia/Kolkata',
                help_text="User's timezone (e.g., 'Asia/Kolkata', 'America/New_York')",
                max_length=50
            ),
        ),
    ]

