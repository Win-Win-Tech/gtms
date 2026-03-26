from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("payslip", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="employeepayrollprofile",
            name="default_field_config",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_payroll_profiles",
                to="payslip.payslipfieldconfig",
            ),
        ),
        migrations.AddField(
            model_name="employeepayrollprofile",
            name="default_template",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="default_for_payroll_profiles",
                to="payslip.paysliptemplate",
            ),
        ),
    ]

