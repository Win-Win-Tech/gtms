import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone


class RollCallSession(models.Model):
    STATUS_OPEN = "open"
    STATUS_CLOSED = "closed"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_CLOSED, "Closed"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="rollcall_sessions",
    )
    shift = models.ForeignKey(
        "scheduler.Shift",
        on_delete=models.CASCADE,
        related_name="rollcall_sessions",
    )
    shift_date = models.DateField(
        help_text="Logical shift start day (overnight-safe)",
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_OPEN,
        db_index=True,
    )

    start_photo = models.ImageField(upload_to="roll_call_starts/")
    end_photo = models.ImageField(upload_to="roll_call_ends/", null=True, blank=True)

    started_at = models.DateTimeField(default=timezone.now)
    ended_at = models.DateTimeField(null=True, blank=True)

    started_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rollcall_sessions_started",
    )
    ended_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rollcall_sessions_ended",
    )

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rollcall_sessions_deleted",
    )

    class Meta:
        indexes = [
            models.Index(fields=["location", "shift", "status"]),
            models.Index(fields=["location", "shift", "shift_date"]),
            models.Index(fields=["status", "shift_date"]),
        ]
        ordering = ["-started_at", "-id"]

    def __str__(self):
        return f"RollCall {self.id} ({self.status}) {self.shift_date}"
