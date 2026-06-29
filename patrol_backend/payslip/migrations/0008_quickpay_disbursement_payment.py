import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("payslip", "0007_payroll_advance"),
    ]

    operations = [
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="paid_amount",
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=12, null=True),
        ),
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="paid_marked_by",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="quick_pay_disbursements_paid_marked",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="paid_on",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="payment_mode",
            field=models.CharField(
                blank=True,
                choices=[
                    ("cash", "Cash"),
                    ("bank", "Bank Transfer"),
                    ("upi", "UPI"),
                    ("other", "Other"),
                ],
                max_length=20,
                null=True,
            ),
        ),
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="payment_notes",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="payment_ref_no",
            field=models.CharField(blank=True, max_length=128, null=True),
        ),
        migrations.AddField(
            model_name="quickpaydisbursement",
            name="payment_status",
            field=models.CharField(
                choices=[("PENDING", "Pending"), ("PAID", "Paid")],
                default="PENDING",
                max_length=20,
            ),
        ),
    ]
