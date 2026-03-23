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
        ("HA", "Half Day"),
        ("A", "Absent"),
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
    # Working-duration based attendance mark for web P/HA/A view.
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
