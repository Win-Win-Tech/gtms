from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("payslip", "0005_remove_employeepayrollprofile_payslip_emp_locatio_2e2a07_idx_and_more"),
    ]

    operations = [
        migrations.AlterField(
            model_name="employeepayrollprofile",
            name="gross_salary",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=12),
        ),
        migrations.AlterField(
            model_name="employeepayrollprofile",
            name="salary_type",
            field=models.CharField(
                choices=[("monthly", "Monthly"), ("hourly", "Hourly")],
                default="monthly",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="employeepayrollprofile",
            name="hourly_rate",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="paysliprecord",
            name="salary_type",
            field=models.CharField(
                choices=[("monthly", "Monthly"), ("hourly", "Hourly")],
                default="monthly",
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name="paysliprecord",
            name="hourly_rate",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="paysliprecord",
            name="worked_minutes",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="paysliprecord",
            name="paid_hours",
            field=models.DecimalField(decimal_places=2, default=0, max_digits=8),
        ),
    ]
