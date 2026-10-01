# CCTV entries: per-visit visitor name / phone (blank = use linked Visitor)

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("visitor", "0014_site_vehicle_overstay_recipient"),
    ]

    operations = [
        migrations.AddField(
            model_name="visitorentry",
            name="visitor_name",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="visitorentry",
            name="phone_number",
            field=models.CharField(blank=True, default="", max_length=32),
        ),
    ]
