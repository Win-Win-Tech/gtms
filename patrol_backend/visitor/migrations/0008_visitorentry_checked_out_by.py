from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("visitor", "0007_visitorentry_vehicle_type_company_name"),
    ]

    operations = [
        migrations.AddField(
            model_name="visitorentry",
            name="checked_out_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="visitor_entries_checked_out",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
