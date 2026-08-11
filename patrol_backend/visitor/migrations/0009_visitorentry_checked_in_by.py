from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("visitor", "0008_visitorentry_checked_out_by"),
    ]

    operations = [
        migrations.AddField(
            model_name="visitorentry",
            name="checked_in_by",
            field=models.ForeignKey(
                blank=True,
                help_text="User who performed the actual check-in",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="visitor_entries_checked_in",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
