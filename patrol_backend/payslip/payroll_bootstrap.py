"""Ensure payslip template + field configs exist for quick pay / payroll."""
from __future__ import annotations

from payslip.models import PayslipField, PayslipFieldConfig, PayslipTemplate


def _ensure_template(location_id, user):
    template, _ = PayslipTemplate.objects.get_or_create(
        location_id=location_id,
        defaults={
            "company_name": "GTMS",
            "header_text": "Payslip",
            "created_by": user,
            "modified_by": user,
            "is_deleted": False,
        },
    )
    if template.is_deleted:
        template.is_deleted = False
        template.modified_by = user
        template.save(update_fields=["is_deleted", "modified_by", "modified_on"])
    return template


def _upsert_fields(config, field_defs):
    for field_name, field_code, field_type, value_type, value, display_order in field_defs:
        field_obj, was_created = PayslipField.objects.get_or_create(
            field_config=config,
            field_code=field_code,
            defaults={
                "field_name": field_name,
                "field_type": field_type,
                "value_type": value_type,
                "value": value,
                "display_order": display_order,
                "is_visible": True,
                "is_deleted": False,
            },
        )
        if not was_created and field_obj.is_deleted:
            field_obj.is_deleted = False
            field_obj.field_name = field_name
            field_obj.field_type = field_type
            field_obj.value_type = value_type
            field_obj.value = value
            field_obj.display_order = display_order
            field_obj.is_visible = True
            field_obj.save(
                update_fields=[
                    "is_deleted",
                    "field_name",
                    "field_type",
                    "value_type",
                    "value",
                    "display_order",
                    "is_visible",
                    "modified_on",
                ]
            )


def ensure_monthly_field_config(location_id, user):
    _ensure_template(location_id, user)
    config = PayslipFieldConfig.objects.filter(
        location_id=location_id,
        is_deleted=False,
        config_name="Default Salary Config",
    ).first()
    if not config:
        config = PayslipFieldConfig.objects.create(
            location_id=location_id,
            config_name="Default Salary Config",
            description="Default salary setup",
            is_active=True,
            created_by=user,
            modified_by=user,
        )
    default_fields = [
        ("Basic Salary", "BASIC", "EARNING", "PERCENTAGE", "60", 1),
        ("House Rent Allowance", "HRA", "EARNING", "PERCENTAGE", "20", 2),
        ("Conveyance", "CONVEYANCE", "EARNING", "PERCENTAGE", "10", 3),
        ("Medical", "MEDICAL", "EARNING", "PERCENTAGE", "10", 4),
        ("Provident Fund", "PF", "DEDUCTION", "FORMULA", "BASIC * Decimal('0.12')", 5),
        ("Professional Tax", "PT", "DEDUCTION", "FIXED", "200", 6),
        ("Absent Deduction", "ABSENT_DEDUCTION", "DEDUCTION", "FORMULA", "(gross_salary / month_days) * absent_days", 7),
        ("Month Days", "MONTH_DAYS", "INFO", "FORMULA", "month_days", 8),
        ("Working Days", "WORKING_DAYS", "INFO", "FORMULA", "working_days", 9),
        ("Present Days", "PRESENT_DAYS", "INFO", "FORMULA", "present_days", 10),
        ("Half Days", "HALF_DAYS", "INFO", "FORMULA", "half_days", 11),
        ("Absent Days", "ABSENT_DAYS", "INFO", "FORMULA", "absent_days", 12),
        ("Paid Days", "PAID_DAYS", "INFO", "FORMULA", "paid_days", 13),
    ]
    _upsert_fields(config, default_fields)
    return config


def ensure_hourly_field_config(location_id, user):
    _ensure_template(location_id, user)
    config = PayslipFieldConfig.objects.filter(
        location_id=location_id,
        is_deleted=False,
        config_name="Hourly Salary Config",
    ).first()
    if not config:
        config = PayslipFieldConfig.objects.create(
            location_id=location_id,
            config_name="Hourly Salary Config",
            description="Hourly salary setup using attendance worked minutes",
            is_active=True,
            created_by=user,
            modified_by=user,
        )
    hourly_fields = [
        ("Hourly Wages", "HOURLY_WAGES", "EARNING", "FORMULA", "(hourly_rate * worked_minutes) / Decimal('60')", 1),
        ("Hourly Rate", "HOURLY_RATE", "INFO", "FORMULA", "hourly_rate", 2),
        ("Worked Minutes", "WORKED_MINUTES", "INFO", "FORMULA", "worked_minutes", 3),
        ("Paid Hours", "PAID_HOURS", "INFO", "FORMULA", "paid_hours", 4),
        ("Present Days", "PRESENT_DAYS", "INFO", "FORMULA", "present_days", 5),
        ("Paid Days", "PAID_DAYS", "INFO", "FORMULA", "paid_days", 6),
    ]
    _upsert_fields(config, hourly_fields)
    return config


def ensure_field_config_for_salary_type(location_id, salary_type, user):
    if salary_type == "hourly":
        return ensure_hourly_field_config(location_id, user)
    return ensure_monthly_field_config(location_id, user)


def resolve_field_config_by_id(location_id, field_config_id):
    if not field_config_id:
        return None
    return PayslipFieldConfig.objects.filter(
        id=field_config_id,
        location_id=location_id,
        is_deleted=False,
    ).first()


def resolve_profile_field_config(profile, user, field_config_id=None):
    """Explicit config id, else profile default, else location default by salary type."""
    explicit = resolve_field_config_by_id(profile.location_id, field_config_id)
    if explicit:
        return explicit
    config = profile.default_field_config
    if config and not config.is_deleted:
        return config
    salary_type = profile.salary_type or "monthly"
    return ensure_field_config_for_salary_type(profile.location_id, salary_type, user)
