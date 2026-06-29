import uuid

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("scheduler", "0015_checklist_models"),
        ("payslip", "0006_hourly_payslip_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="PayrollAdvance",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("settlement_month", models.CharField(max_length=7)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("advance_date", models.DateField()),
                (
                    "payment_mode",
                    models.CharField(
                        choices=[
                            ("cash", "Cash"),
                            ("bank", "Bank Transfer"),
                            ("upi", "UPI"),
                            ("other", "Other"),
                        ],
                        default="cash",
                        max_length=20,
                    ),
                ),
                ("reference_no", models.CharField(blank=True, max_length=128, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("OPEN", "Open"),
                            ("CLOSED", "Closed"),
                            ("CANCELLED", "Cancelled"),
                        ],
                        default="OPEN",
                        max_length=20,
                    ),
                ),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                ("modified_on", models.DateTimeField(auto_now=True)),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payroll_advance_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payroll_advances",
                        to="scheduler.location",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="payroll_advances",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-advance_date", "-created_on"],
            },
        ),
        migrations.CreateModel(
            name="QuickPayDisbursement",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("settlement_month", models.CharField(max_length=7)),
                ("period_start", models.DateField()),
                ("period_end", models.DateField()),
                ("date_filter", models.CharField(default="today", max_length=20)),
                (
                    "salary_type",
                    models.CharField(
                        choices=[("monthly", "Monthly"), ("hourly", "Hourly")],
                        default="monthly",
                        max_length=20,
                    ),
                ),
                ("attendance_snapshot", models.JSONField(blank=True, default=dict)),
                ("field_values", models.JSONField(blank=True, default=dict)),
                ("gross_earnings", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("total_deductions", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("net_before_advance", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("advance_recovery", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                ("net_paid", models.DecimalField(decimal_places=2, default=0, max_digits=12)),
                (
                    "export_format",
                    models.CharField(
                        choices=[("excel", "Excel"), ("pdf", "PDF")],
                        default="pdf",
                        max_length=10,
                    ),
                ),
                ("exported_on", models.DateTimeField(auto_now_add=True)),
                (
                    "exported_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quick_pay_disbursements_exported",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
                (
                    "field_config",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="quick_pay_disbursements",
                        to="payslip.payslipfieldconfig",
                    ),
                ),
                (
                    "location",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quick_pay_disbursements",
                        to="scheduler.location",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quick_pay_disbursements",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-exported_on"],
            },
        ),
        migrations.CreateModel(
            name="PayrollAdvanceRecovery",
            fields=[
                ("id", models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ("amount", models.DecimalField(decimal_places=2, max_digits=12)),
                ("recovery_date", models.DateField()),
                (
                    "source_type",
                    models.CharField(
                        choices=[
                            ("QUICK_PAY", "Quick Pay"),
                            ("MONTHLY_PAYSLIP", "Monthly Payslip"),
                            ("MANUAL_ADJ", "Manual Adjustment"),
                        ],
                        max_length=20,
                    ),
                ),
                ("source_id", models.UUIDField(blank=True, null=True)),
                ("notes", models.TextField(blank=True, null=True)),
                ("created_on", models.DateTimeField(auto_now_add=True)),
                (
                    "advance",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="recoveries",
                        to="payslip.payrolladvance",
                    ),
                ),
                (
                    "created_by",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="payroll_advance_recovery_created",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-recovery_date", "-created_on"],
            },
        ),
        migrations.AddIndex(
            model_name="payrolladvance",
            index=models.Index(fields=["user", "settlement_month", "status"], name="payslip_pay_user_id_8a1f2c_idx"),
        ),
        migrations.AddIndex(
            model_name="payrolladvance",
            index=models.Index(fields=["location", "settlement_month"], name="payslip_pay_locatio_4b3e9d_idx"),
        ),
        migrations.AddIndex(
            model_name="quickpaydisbursement",
            index=models.Index(fields=["user", "settlement_month"], name="payslip_qui_user_id_7c2d1a_idx"),
        ),
        migrations.AddIndex(
            model_name="quickpaydisbursement",
            index=models.Index(fields=["location", "period_start", "period_end"], name="payslip_qui_locatio_9e4f6b_idx"),
        ),
        migrations.AddIndex(
            model_name="payrolladvancerecovery",
            index=models.Index(fields=["advance", "recovery_date"], name="payslip_pay_advance_2d8c5e_idx"),
        ),
        migrations.AddIndex(
            model_name="payrolladvancerecovery",
            index=models.Index(fields=["source_type", "source_id"], name="payslip_pay_source__1a7b3f_idx"),
        ),
    ]
