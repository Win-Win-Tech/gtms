import uuid

from django.db import models


class DummyReport(models.Model):
    """Legacy placeholder — kept so existing migration history stays valid."""

    created_at = models.DateTimeField(auto_now_add=True)


class LocationReportEmailConfig(models.Model):
    """Org-level auto report email settings (recipients + send clock)."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.OneToOneField(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="report_email_config",
    )
    is_enabled = models.BooleanField(default=False)
    recipients = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated recipient email addresses",
    )
    send_time = models.TimeField(
        help_text="Local org timezone clock time to send matching reports",
    )
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Location report email config"
        verbose_name_plural = "Location report email configs"

    def __str__(self):
        return f"ReportEmailConfig<{self.location_id}> enabled={self.is_enabled}"

    def recipient_list(self):
        parts = [p.strip() for p in (self.recipients or "").replace(";", ",").split(",")]
        return [p for p in parts if p]


class LocationReportEmailItem(models.Model):
    """Per-report schedule and format settings for an org."""

    SCHEDULE_DAILY = "daily"
    SCHEDULE_WEEKLY_SUNDAY = "weekly_sunday"
    SCHEDULE_MONTHLY_START = "monthly_start"
    SCHEDULE_CHOICES = (
        (SCHEDULE_DAILY, "Every day"),
        (SCHEDULE_WEEKLY_SUNDAY, "Every Sunday"),
        (SCHEDULE_MONTHLY_START, "1st of month"),
    )

    PERIOD_PREVIOUS_DAY = "previous_day"
    PERIOD_TODAY = "today"
    DAILY_PERIOD_CHOICES = (
        (PERIOD_PREVIOUS_DAY, "Previous day"),
        (PERIOD_TODAY, "Today"),
    )

    REPORT_CHECKIN = "checkin"
    REPORT_ATTENDANCE = "attendance"
    REPORT_ROLLCALL = "rollcall"
    REPORT_INCIDENT = "incident"
    REPORT_VISITOR_ENTRIES = "visitor_entries"
    REPORT_VEHICLE_MOVEMENT = "vehicle_movement"
    REPORT_MONTHLY_ATTENDANCE = "monthly_attendance"
    REPORT_MONTHLY_LOCATION = "monthly_location"
    REPORT_CODE_CHOICES = (
        (REPORT_CHECKIN, "Check-in Report"),
        (REPORT_ATTENDANCE, "Attendance Report"),
        (REPORT_ROLLCALL, "Roll Call Report"),
        (REPORT_INCIDENT, "Incident Report"),
        (REPORT_VISITOR_ENTRIES, "Visitor Entries"),
        (REPORT_VEHICLE_MOVEMENT, "Vehicle Movement"),
        (REPORT_MONTHLY_ATTENDANCE, "Monthly Attendance Summary"),
        (REPORT_MONTHLY_LOCATION, "Monthly Location Summary"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    config = models.ForeignKey(
        LocationReportEmailConfig,
        on_delete=models.CASCADE,
        related_name="items",
    )
    report_code = models.CharField(max_length=64, choices=REPORT_CODE_CHOICES)
    is_enabled = models.BooleanField(default=False)
    schedule_type = models.CharField(
        max_length=32,
        choices=SCHEDULE_CHOICES,
        default=SCHEDULE_DAILY,
    )
    daily_period = models.CharField(
        max_length=32,
        choices=DAILY_PERIOD_CHOICES,
        default=PERIOD_PREVIOUS_DAY,
        help_text="Used only when schedule_type=daily",
    )
    send_pdf = models.BooleanField(default=True)
    send_excel = models.BooleanField(default=False)
    site_wise = models.BooleanField(
        default=False,
        help_text="If true, generate one attachment set per active site",
    )
    last_sent_schedule_key = models.CharField(max_length=64, blank=True, default="")
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("config", "report_code")
        verbose_name = "Location report email item"
        verbose_name_plural = "Location report email items"

    def __str__(self):
        return f"{self.report_code} ({self.schedule_type}) enabled={self.is_enabled}"


class ReportEmailLog(models.Model):
    STATUS_SENT = "sent"
    STATUS_SKIPPED = "skipped"
    STATUS_ERROR = "error"
    STATUS_CHOICES = (
        (STATUS_SENT, "Sent"),
        (STATUS_SKIPPED, "Skipped"),
        (STATUS_ERROR, "Error"),
    )

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="report_email_logs",
    )
    report_code = models.CharField(max_length=64)
    site = models.ForeignKey(
        "scheduler.LocationSite",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="report_email_logs",
    )
    schedule_key = models.CharField(max_length=64)
    period_label = models.CharField(max_length=128, blank=True, default="")
    formats = models.CharField(max_length=32, blank=True, default="")
    row_count = models.PositiveIntegerField(default=0)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_SENT)
    error = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-sent_at",)
        indexes = [
            models.Index(fields=["location", "report_code", "schedule_key"]),
        ]

    def __str__(self):
        return f"{self.report_code} {self.status} {self.schedule_key}"
