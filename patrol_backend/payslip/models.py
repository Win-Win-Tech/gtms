import uuid
from django.conf import settings
from django.db import models


class EmployeePayrollProfile(models.Model):
    SALARY_TYPE_CHOICES = [
        ("monthly", "Monthly"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="payroll_profile",
    )
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="payroll_profiles",
    )
    salary_type = models.CharField(max_length=20, choices=SALARY_TYPE_CHOICES, default="monthly")
    gross_salary = models.DecimalField(max_digits=12, decimal_places=2)
    default_field_config = models.ForeignKey(
        "payslip.PayslipFieldConfig",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_payroll_profiles",
    )
    default_template = models.ForeignKey(
        "payslip.PayslipTemplate",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="default_for_payroll_profiles",
    )

    # Bank details
    bank_name = models.CharField(max_length=128, null=True, blank=True)
    account_holder_name = models.CharField(max_length=128, null=True, blank=True)
    account_number = models.CharField(max_length=64, null=True, blank=True)
    ifsc_code = models.CharField(max_length=32, null=True, blank=True)
    branch_name = models.CharField(max_length=128, null=True, blank=True)

    # Statutory
    pf_enabled = models.BooleanField(default=False)
    esi_enabled = models.BooleanField(default=False)
    pf_number = models.CharField(max_length=64, null=True, blank=True)
    esi_number = models.CharField(max_length=64, null=True, blank=True)
    uan = models.CharField(max_length=64, null=True, blank=True)
    pan = models.CharField(max_length=64, null=True, blank=True)

    is_active = models.BooleanField(default=True)
    effective_from = models.DateField()
    effective_to = models.DateField(null=True, blank=True)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_profile_created",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payroll_profile_modified",
    )

    class Meta:
        indexes = [
            models.Index(fields=["is_active"]),
        ]

    def __str__(self):
        return f"{self.user.employee_code} - {self.user_id}"


class PayslipTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.OneToOneField(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="payslip_template",
    )

    company_logo = models.ImageField(upload_to="payslip_logos/", null=True, blank=True)
    company_name = models.CharField(max_length=200, null=True, blank=True)
    company_address = models.TextField(null=True, blank=True)
    company_email = models.EmailField(null=True, blank=True)
    company_phone = models.CharField(max_length=20, null=True, blank=True)
    company_gstin = models.CharField(max_length=20, null=True, blank=True)

    header_text = models.CharField(max_length=200, null=True, blank=True)
    header_color = models.CharField(max_length=7, default="#1e40af")
    header_text_color = models.CharField(max_length=7, default="#111111")
    header_alignment = models.CharField(max_length=10, default="center")
    footer_text = models.TextField(null=True, blank=True)
    footer_color = models.CharField(max_length=7, default="#64748b")
    layout_config = models.JSONField(default=dict, blank=True)
    page_size = models.CharField(max_length=10, default="A4")
    orientation = models.CharField(max_length=10, default="portrait")
    font_size = models.IntegerField(default=10)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_template_created",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_template_modified",
    )
    is_deleted = models.BooleanField(default=False)

    def __str__(self):
        return f"Template - {self.location_id}"


class PayslipFieldConfig(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="payslip_field_configs",
    )
    config_name = models.CharField(max_length=100)
    description = models.TextField(null=True, blank=True)
    is_active = models.BooleanField(default=True)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_field_config_created",
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_field_config_modified",
    )
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [("location", "config_name")]
        ordering = ["config_name"]

    def __str__(self):
        return f"{self.config_name} - {self.location_id}"


class PayslipField(models.Model):
    FIELD_TYPES = [
        ("EARNING", "Earning"),
        ("DEDUCTION", "Deduction"),
        ("INFO", "Information"),
    ]
    VALUE_TYPES = [
        ("PERCENTAGE", "Percentage"),
        ("FIXED", "Fixed"),
        ("FORMULA", "Formula"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    field_config = models.ForeignKey(PayslipFieldConfig, on_delete=models.CASCADE, related_name="fields")
    field_name = models.CharField(max_length=100)
    field_code = models.CharField(max_length=50)
    field_type = models.CharField(max_length=20, choices=FIELD_TYPES)
    value_type = models.CharField(max_length=20, choices=VALUE_TYPES)
    value = models.CharField(max_length=500)
    display_order = models.IntegerField(default=1)
    is_visible = models.BooleanField(default=True)
    default_value = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = [("field_config", "field_code")]
        ordering = ["display_order"]

    def __str__(self):
        return f"{self.field_name} ({self.field_code})"


class PayslipRecord(models.Model):
    STATUS_CHOICES = [
        ("DRAFT", "Draft"),
        ("APPROVED", "Approved"),
        ("PAID", "Paid"),
    ]
    PAYMENT_MODE_CHOICES = [
        ("cash", "Cash"),
        ("bank", "Bank Transfer"),
        ("upi", "UPI"),
        ("other", "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="payslip_records")
    location = models.ForeignKey("scheduler.Location", on_delete=models.CASCADE, related_name="payslip_records")
    payroll_profile = models.ForeignKey(EmployeePayrollProfile, on_delete=models.PROTECT, related_name="payslips")
    field_config = models.ForeignKey(PayslipFieldConfig, on_delete=models.PROTECT, related_name="payslips")
    template = models.ForeignKey(PayslipTemplate, on_delete=models.PROTECT, related_name="payslips")
    month = models.CharField(max_length=7)  # YYYY-MM

    month_days = models.IntegerField(default=0)
    working_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    present_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    half_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    absent_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    paid_days = models.DecimalField(max_digits=6, decimal_places=2, default=0)

    gross_salary = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_earnings = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    total_deductions = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_pay = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    field_values = models.JSONField(default=dict, blank=True)
    attendance_snapshot = models.JSONField(default=dict, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="DRAFT")
    notes = models.TextField(null=True, blank=True)

    paid_amount = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    paid_on = models.DateTimeField(null=True, blank=True)
    payment_mode = models.CharField(max_length=20, choices=PAYMENT_MODE_CHOICES, null=True, blank=True)
    payment_ref_no = models.CharField(max_length=128, null=True, blank=True)
    payment_notes = models.TextField(null=True, blank=True)

    generated_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    generated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_generated",
    )
    approved_on = models.DateTimeField(null=True, blank=True)
    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_approved",
    )
    paid_marked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="payslip_paid_marked",
    )
    pdf_file = models.FileField(upload_to="payslips/", null=True, blank=True)

    class Meta:
        unique_together = [("user", "month")]
        ordering = ["-generated_on"]
        indexes = [
            models.Index(fields=["month", "status"]),
            models.Index(fields=["location", "month"]),
        ]

    def __str__(self):
        return f"{self.month} - {self.user_id} - {self.status}"

