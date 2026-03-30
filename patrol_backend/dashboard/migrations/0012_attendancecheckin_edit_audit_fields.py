from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("dashboard", "0011_globalauditlog"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name="attendancecheckin",
            name="edit_reason",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="attendancecheckin",
            name="edited_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="attendance_edits",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="attendancecheckin",
            name="edited_on",
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
    ]

