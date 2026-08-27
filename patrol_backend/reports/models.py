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
        from reports.services.email_sender import parse_recipients

        return parse_recipients(self.recipients or "")


def default_monthly_send_days():
    return [1]


def default_last_n_send_days():
    return [7, 14, 21, 28]


class LocationReportEmailItem(models.Model):
    """Per-report schedule and format settings for an org."""

    SCHEDULE_DAILY = "daily"
    SCHEDULE_WEEKLY = "weekly"
    SCHEDULE_MONTHLY = "monthly"
    SCHEDULE_LAST_N_DAYS = "last_n_days"
    # Legacy aliases (still accepted on input / stored history)
    SCHEDULE_WEEKLY_SUNDAY = "weekly_sunday"
    SCHEDULE_MONTHLY_START = "monthly_start"

    SCHEDULE_CHOICES = (
        (SCHEDULE_DAILY, "Every day"),
        (SCHEDULE_WEEKLY, "Weekly"),
        (SCHEDULE_MONTHLY, "Monthly"),
        (SCHEDULE_LAST_N_DAYS, "Last N days"),
        (SCHEDULE_WEEKLY_SUNDAY, "Every Sunday (legacy)"),
        (SCHEDULE_MONTHLY_START, "1st of month (legacy)"),
    )

    VALID_SCHEDULE_TYPES = frozenset(
        {
            SCHEDULE_DAILY,
            SCHEDULE_WEEKLY,
            SCHEDULE_MONTHLY,
            SCHEDULE_LAST_N_DAYS,
            SCHEDULE_WEEKLY_SUNDAY,
            SCHEDULE_MONTHLY_START,
        }
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
        (REPORT_CHECKIN, "QR Scan Patrol Report"),
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
        help_text="Legacy primary schedule; prefer schedule_types",
    )
    schedule_types = models.JSONField(
        default=list,
        blank=True,
        help_text='List of schedule types, e.g. ["daily","weekly","last_n_days"]',
    )
    daily_period = models.CharField(
        max_length=32,
        choices=DAILY_PERIOD_CHOICES,
        default=PERIOD_PREVIOUS_DAY,
        help_text="Used only when daily is among schedule_types",
    )
    weekly_weekday = models.PositiveSmallIntegerField(
        default=6,
        help_text="0=Monday … 6=Sunday; used when weekly is selected",
    )
    weekly_include_current_day = models.BooleanField(
        default=False,
        help_text="If false, weekly window ends yesterday; if true, ends today",
    )
    monthly_send_days = models.JSONField(
        default=default_monthly_send_days,
        blank=True,
        help_text="Days of month (1–31) when monthly schedule sends",
    )
    last_n_days_count = models.PositiveSmallIntegerField(
        default=7,
        help_text="Rolling lookback length for last_n_days schedule (1–90)",
    )
    last_n_days_send_days = models.JSONField(
        default=default_last_n_send_days,
        blank=True,
        help_text="Days of month (1–31) when last_n_days schedule sends",
    )
    last_n_days_include_current_day = models.BooleanField(
        default=False,
        help_text="If false, last-N window ends yesterday; if true, ends today",
    )
    send_pdf = models.BooleanField(default=True)
    send_excel = models.BooleanField(default=False)
    site_wise = models.BooleanField(
        default=False,
        help_text="If true, generate one attachment set per active site",
    )
    last_sent_schedule_key = models.CharField(max_length=64, blank=True, default="")
    last_sent_keys = models.JSONField(
        default=dict,
        blank=True,
        help_text='Per-schedule last sent keys, e.g. {"daily":"2026-08-25-daily"}',
    )
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("config", "report_code")
        verbose_name = "Location report email item"
        verbose_name_plural = "Location report email items"

    def __str__(self):
        types = self.get_schedule_types()
        return f"{self.report_code} ({','.join(types)}) enabled={self.is_enabled}"

    @staticmethod
    def normalize_schedule_type(value: str) -> str:
        if value == LocationReportEmailItem.SCHEDULE_WEEKLY_SUNDAY:
            return LocationReportEmailItem.SCHEDULE_WEEKLY
        if value == LocationReportEmailItem.SCHEDULE_MONTHLY_START:
            return LocationReportEmailItem.SCHEDULE_MONTHLY
        return value

    def get_schedule_types(self):
        """Normalized list of schedule types (supports legacy schedule_type)."""
        raw = self.schedule_types
        if isinstance(raw, list) and raw:
            cleaned = []
            for t in raw:
                nt = self.normalize_schedule_type(t)
                if nt in {
                    self.SCHEDULE_DAILY,
                    self.SCHEDULE_WEEKLY,
                    self.SCHEDULE_MONTHLY,
                    self.SCHEDULE_LAST_N_DAYS,
                } and nt not in cleaned:
                    cleaned.append(nt)
            if cleaned:
                return cleaned
        if self.schedule_type:
            return [self.normalize_schedule_type(self.schedule_type)]
        return [self.SCHEDULE_DAILY]

    def set_schedule_types(self, types):
        cleaned = []
        for t in types or []:
            nt = self.normalize_schedule_type(t)
            if nt in {
                self.SCHEDULE_DAILY,
                self.SCHEDULE_WEEKLY,
                self.SCHEDULE_MONTHLY,
                self.SCHEDULE_LAST_N_DAYS,
            } and nt not in cleaned:
                cleaned.append(nt)
        if not cleaned:
            cleaned = [self.SCHEDULE_DAILY]
        self.schedule_types = cleaned
        self.schedule_type = cleaned[0]


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
