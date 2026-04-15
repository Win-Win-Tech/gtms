from django.db import models
import uuid
from django.conf import settings
from django.utils import timezone

# Create your models here.
class AttendanceCheckin(models.Model):
    STATUS_CHOICES = [
        ("present", "Present"),
        ("late", "Late"),
        ("absent", "Absent"),
        ("checked_out", "Checked Out"),
    ]
    PA_STATUS_CHOICES = [
        ("P", "Present"),
        ("OW", "On Work"),
        ("M", "Missed Checkout"),
        ("LD", "Less Duration"),
        ("A", "Absent (Legacy)"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    guard = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_records"
    )
    assignment = models.ForeignKey(
        "scheduler.Assignment",
        on_delete=models.CASCADE,
        related_name="attendance_records"
    )
    shift = models.ForeignKey(
        "scheduler.Shift",
        on_delete=models.CASCADE,
        related_name="attendance_records"
    )
    org_location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="attendance_records"
    )
    # Logical shift-start date in business timezone (used for report bucketing).
    shift_date = models.DateField(null=True, blank=True, db_index=True)

    checkin_time = models.DateTimeField(null=True, blank=True)
    checkout_time = models.DateTimeField(null=True, blank=True)

    # V3 summary fields (latest event metadata + computed duration)
    # - checkin_time/checkout_time are kept for backward compatibility
    # - last_checkin_time/last_checkout_time represent the latest events within the shift window
    last_checkin_time = models.DateTimeField(null=True, blank=True)
    last_checkout_time = models.DateTimeField(null=True, blank=True)
    duration_minutes = models.IntegerField(null=True, blank=True)
    checkin_count = models.IntegerField(default=0)
    checkout_count = models.IntegerField(default=0)
    # Working-duration based attendance mark for web P/A/OW view.
    # - computed from CheckInLog pairs inside the shift window
    # - set after checkout (while check-in only, may remain null)
    pa_status = models.CharField(
        max_length=3,
        choices=PA_STATUS_CHOICES,
        null=True,
        blank=True,
        db_index=True,
    )

    latitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    longitude = models.DecimalField(max_digits=9, decimal_places=6, null=True, blank=True)
    radius_m = models.IntegerField(default=50)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="absent")
    remarks = models.TextField(null=True, blank=True)
    od_remarks = models.TextField(null=True, blank=True)

    # Latest check-in / check-out images for quick UI display (event-level audit remains in CheckInLog.image)
    checkin_image = models.ImageField(upload_to="attendance_checkins/", null=True, blank=True)
    checkout_image = models.ImageField(upload_to="attendance_checkouts/", null=True, blank=True)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    edited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_edits",
    )
    edited_on = models.DateTimeField(null=True, blank=True, db_index=True)
    edit_reason = models.TextField(null=True, blank=True)

    def save(self, *args, **kwargs):
        """Auto update status based on checkin/checkout"""
        if self.checkin_time:
            shift_start = self.shift.start_time
            if self.checkin_time.time() > shift_start:
                self.status = "late"
            else:
                self.status = "present"

        if self.checkout_time:
            self.status = "checked_out"

        # If no checkin at all → absent
        if not self.checkin_time and not self.checkout_time:
            self.status = "absent"

        super().save(*args, **kwargs)

    def _str_(self):
        return f"{self.guard} - {self.shift} [{self.status}]"
    
class CheckInLog(models.Model):
    guard = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    assignment = models.ForeignKey("scheduler.Assignment", on_delete=models.CASCADE)
    shift = models.ForeignKey("scheduler.Shift", on_delete=models.CASCADE)
    org_location = models.ForeignKey("scheduler.Location", on_delete=models.CASCADE)
    timestamp = models.DateTimeField(auto_now_add=True)
    type = models.CharField(max_length=10, choices=[("checkin", "Check-In"), ("checkout", "Check-Out")])
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)

    # Optional proof image for each checkin/checkout event
    image = models.ImageField(upload_to="attendance_checkinlog/", null=True, blank=True)

    def __str__(self):
        return f"{self.guard} - {self.type} @ {self.timestamp}"


class GlobalAuditLog(models.Model):
    EVENT_CHOICES = [
        ("create", "Create"),
        ("update", "Update"),
        ("delete", "Delete"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    app_label = models.CharField(max_length=100, db_index=True)
    model_name = models.CharField(max_length=120, db_index=True)
    object_pk = models.CharField(max_length=100, db_index=True)
    event_type = models.CharField(max_length=10, choices=EVENT_CHOICES, db_index=True)

    # requested for fast scoped queries
    location_id = models.UUIDField(null=True, blank=True, db_index=True)

    old_data = models.JSONField(null=True, blank=True)
    new_data = models.JSONField(null=True, blank=True)
    changed_fields = models.JSONField(null=True, blank=True)

    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="global_audit_logs",
    )
    changed_on = models.DateTimeField(auto_now_add=True, db_index=True)
    source = models.CharField(max_length=30, default="api", db_index=True)  # api/system/celery/unknown

    class Meta:
        ordering = ["-changed_on"]
        indexes = [
            models.Index(fields=["app_label", "model_name", "object_pk"]),
            models.Index(fields=["location_id", "changed_on"]),
            models.Index(fields=["event_type", "changed_on"]),
        ]

    def __str__(self):
        return f"{self.app_label}.{self.model_name}:{self.object_pk} [{self.event_type}]"


class AttendanceWeekOff(models.Model):
    """
    Stored week-off marks (W) per user (user_id), location (location_id), and calendar date.
    Used by monthly week-off Excel upload and listing (separate from check-in derived P/A).
    """

    SOURCE_CHOICES = [
        ("excel_upload", "Excel upload"),
        ("api", "API"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="attendance_weekoffs",
    )
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="attendance_weekoffs",
    )
    weekoff_date = models.DateField(db_index=True)
    mark = models.CharField(max_length=3, default="W")
    source = models.CharField(
        max_length=20,
        choices=SOURCE_CHOICES,
        default="excel_upload",
        db_index=True,
    )
    upload_batch_id = models.UUIDField(null=True, blank=True, db_index=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attendance_weekoffs_created",
    )
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["user", "location", "weekoff_date"],
                name="uniq_attendance_weekoff_user_location_date",
            ),
        ]
        indexes = [
            models.Index(fields=["location", "weekoff_date"]),
            models.Index(fields=["user", "weekoff_date"]),
        ]

    def __str__(self):
        return f"{self.user_id} @ {self.location_id} {self.weekoff_date} [{self.mark}]"
