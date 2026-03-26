from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payslip", "0002_employeepayrollprofile_default_links"),
    ]

    operations = [
        migrations.AddField(
            model_name="paysliptemplate",
            name="header_text_color",
            field=models.CharField(default="#111111", max_length=7),
        ),
    ]

