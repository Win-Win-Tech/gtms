# Generated manually for visitor pass_image

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("visitor", "0004_approval_workflow_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="visitorentry",
            name="pass_image",
            field=models.ImageField(
                blank=True,
                help_text="Visitor ID-card pass (org, name, visit date, host, QR)",
                null=True,
                upload_to="visitor_pass/",
            ),
        ),
    ]
