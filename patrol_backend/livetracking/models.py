import uuid

from django.conf import settings
from django.db import models

from scheduler.models import Location, LocationSite


class TrackingAlert(models.Model):
    """Site boundary / location-missing alerts (separate from visitor NotificationLog)."""

    class AlertType(models.TextChoices):
        BOUNDARY_BREACH = "boundary_breach", "Boundary breach"
        LOCATION_MISSING = "location_missing", "Location missing"
        MANUAL_SOS = "manual_sos", "Manual SOS"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert_type = models.CharField(max_length=32, choices=AlertType.choices, db_index=True)
    site = models.ForeignKey(
        LocationSite,
        on_delete=models.CASCADE,
        related_name="tracking_alerts",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.CASCADE,
        related_name="tracking_alerts",
    )
    subject_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tracking_alerts_as_subject",
    )
    subject_role = models.CharField(max_length=100, blank=True, default="")
    attendance = models.ForeignKey(
        "dashboard.AttendanceCheckin",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tracking_alerts",
    )
    latitude = models.DecimalField(max_digits=12, decimal_places=9, null=True, blank=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=9, null=True, blank=True)
    message = models.TextField(blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["location", "is_active", "created_at"]),
            models.Index(fields=["site", "alert_type", "is_active"]),
            models.Index(fields=["subject_user", "is_active"]),
        ]

    def __str__(self):
        return f"{self.alert_type} — {self.subject_user_id} @ {self.site_id}"


class TrackingAlertRecipient(models.Model):
    """Per-user delivery and read state for a tracking alert."""

    class Channel(models.TextChoices):
        WEBSOCKET = "websocket", "WebSocket"
        IN_APP = "in_app", "In-app"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    alert = models.ForeignKey(
        TrackingAlert,
        on_delete=models.CASCADE,
        related_name="recipients",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tracking_alert_deliveries",
    )
    channel = models.CharField(
        max_length=16,
        choices=Channel.choices,
        default=Channel.IN_APP,
    )
    read_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("alert", "user", "channel")
        indexes = [
            models.Index(fields=["user", "read_at"]),
        ]

    def __str__(self):
        return f"{self.user_id} ← {self.alert_id}"


class SiteAlertRecipientConfig(models.Model):
    """
    Per-site routing: when subject_role triggers an alert, notify recipient_role.

    Example: Guard crosses boundary → SO / FO / Admin receive the alert.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    site = models.ForeignKey(
        LocationSite,
        on_delete=models.CASCADE,
        related_name="alert_recipient_configs",
    )
    subject_role = models.ForeignKey(
        "authapp.Role",
        on_delete=models.CASCADE,
        related_name="site_alert_as_subject",
        help_text="Role of the user who crossed the boundary or went missing",
    )
    recipient_role = models.ForeignKey(
        "authapp.Role",
        on_delete=models.CASCADE,
        related_name="site_alert_as_recipient",
        help_text="Role that should receive the alert for this subject role",
    )
    notify_boundary_breach = models.BooleanField(default=False)
    notify_location_missing = models.BooleanField(default=False)
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ("site", "subject_role", "recipient_role")
        indexes = [
            models.Index(fields=["site", "subject_role", "notify_boundary_breach"]),
            models.Index(fields=["site", "subject_role", "notify_location_missing"]),
        ]

    def __str__(self):
        return (
            f"{self.site.name}: {self.subject_role.name} → {self.recipient_role.name}"
        )


class UserLiveLocation(models.Model):
    """Stores only the MOST RECENT location of a user for live dashboard viewing."""

    class BoundaryState(models.TextChoices):
        INSIDE = "inside", "Inside"
        OUTSIDE = "outside", "Outside"
        UNKNOWN = "unknown", "Unknown"

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="live_location",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="live_user_locations",
    )
    assigned_site = models.ForeignKey(
        LocationSite,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="live_user_locations",
    )
    latitude = models.DecimalField(max_digits=12, decimal_places=9, null=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=9, null=True)
    is_inside_boundary = models.BooleanField(null=True, blank=True)
    boundary_state = models.CharField(
        max_length=16,
        choices=BoundaryState.choices,
        default=BoundaryState.UNKNOWN,
        blank=True,
    )
    last_location_at = models.DateTimeField(null=True, blank=True)
    active_breach_alert = models.ForeignKey(
        TrackingAlert,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="active_for_live_locations",
    )
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Live Location"
        verbose_name_plural = "User Live Locations"

    def __str__(self):
        return f"{self.user.email} - {self.location.name if self.location else 'No Location'}"


class UserLocationHistory(models.Model):
    """Stores ALL location updates for playback purposes."""

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="location_history",
    )
    location = models.ForeignKey(
        Location,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_history_records",
    )
    latitude = models.DecimalField(max_digits=12, decimal_places=9)
    longitude = models.DecimalField(max_digits=12, decimal_places=9)
    timestamp = models.DateTimeField(db_index=True)

    class Meta:
        verbose_name = "User Location History"
        verbose_name_plural = "User Location History"
        indexes = [
            models.Index(fields=["user", "timestamp"]),
            models.Index(fields=["location", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.user.email} at {self.timestamp}"
