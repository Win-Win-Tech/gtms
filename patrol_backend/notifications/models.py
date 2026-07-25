import uuid

from django.conf import settings
from django.db import models


class DeviceToken(models.Model):
    DEVICE_ANDROID = "android"
    DEVICE_IOS = "ios"
    DEVICE_WEB = "web"
    DEVICE_TYPE_CHOICES = [
        (DEVICE_ANDROID, "Android"),
        (DEVICE_IOS, "iOS"),
        (DEVICE_WEB, "Web"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="device_tokens",
    )
    token = models.CharField(max_length=255, unique=True, db_index=True)
    device_type = models.CharField(max_length=16, choices=DEVICE_TYPE_CHOICES)
    device_id = models.CharField(max_length=128, blank=True, default="")
    app_version = models.CharField(max_length=32, blank=True, default="")
    is_active = models.BooleanField(default=True, db_index=True)
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-modified_on"]
        indexes = [
            models.Index(fields=["user", "is_active"]),
            models.Index(fields=["device_type", "is_active"]),
        ]

    def __str__(self):
        return f"{self.user_id} {self.device_type} ({'active' if self.is_active else 'off'})"


class NotificationLog(models.Model):
    TYPE_VISITOR_PENDING = "visitor_pending_approval"
    TYPE_VISITOR_APPROVED = "visitor_approved"
    TYPE_VISITOR_CANCELLED = "visitor_cancelled"
    TYPE_VISITOR_REVERTED = "visitor_reverted"
    TYPE_VISITOR_RESCHEDULED = "visitor_rescheduled"
    TYPE_CHOICES = [
        (TYPE_VISITOR_PENDING, "Visitor pending approval"),
        (TYPE_VISITOR_APPROVED, "Visitor approved"),
        (TYPE_VISITOR_CANCELLED, "Visitor cancelled"),
        (TYPE_VISITOR_REVERTED, "Visitor reverted"),
        (TYPE_VISITOR_RESCHEDULED, "Visitor rescheduled"),
    ]

    CHANNEL_PUSH = "push"
    CHANNEL_IN_APP = "in_app"
    CHANNEL_CHOICES = [
        (CHANNEL_PUSH, "Push"),
        (CHANNEL_IN_APP, "In-app"),
    ]

    STATUS_PENDING = "pending"
    STATUS_SENT = "sent"
    STATUS_FAILED = "failed"
    STATUS_SKIPPED = "skipped"
    DELIVERY_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_SENT, "Sent"),
        (STATUS_FAILED, "Failed"),
        (STATUS_SKIPPED, "Skipped"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notification_logs",
    )
    type = models.CharField(max_length=64, choices=TYPE_CHOICES, db_index=True)
    title = models.CharField(max_length=255)
    body = models.TextField(blank=True, default="")
    data = models.JSONField(default=dict, blank=True)
    related_entry = models.ForeignKey(
        "visitor.VisitorEntry",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notification_logs",
    )
    channel = models.CharField(
        max_length=16,
        choices=CHANNEL_CHOICES,
        default=CHANNEL_PUSH,
    )
    delivery_status = models.CharField(
        max_length=16,
        choices=DELIVERY_CHOICES,
        default=STATUS_PENDING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")
    sent_at = models.DateTimeField(null=True, blank=True)
    read_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_on"]
        indexes = [
            models.Index(fields=["user", "read_at"]),
            models.Index(fields=["user", "type"]),
            models.Index(fields=["user", "-created_on"]),
        ]

    def __str__(self):
        return f"{self.type} → {self.user_id}"

    @property
    def is_read(self):
        return self.read_at is not None
