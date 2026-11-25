import uuid

from django.conf import settings
from django.db import models, transaction
from django.utils import timezone


class TicketCategory(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, unique=True)
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveIntegerField(default=0)
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class SupportTicket(models.Model):
    class Priority(models.TextChoices):
        LOW = "LOW", "Low"
        MEDIUM = "MEDIUM", "Medium"
        HIGH = "HIGH", "High"
        CRITICAL = "CRITICAL", "Critical"

    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In Progress"
        RESOLVED = "RESOLVED", "Resolved"
        CLOSED = "CLOSED", "Closed"
        REFERRED_BACK = "REFERRED_BACK", "Referred Back"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket_number = models.CharField(max_length=32, unique=True, editable=False, db_index=True)
    subject = models.CharField(max_length=255)
    description = models.TextField()
    category = models.ForeignKey(
        TicketCategory,
        on_delete=models.PROTECT,
        related_name="tickets",
    )
    priority = models.CharField(
        max_length=16,
        choices=Priority.choices,
        default=Priority.MEDIUM,
        db_index=True,
    )
    status = models.CharField(
        max_length=16,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="support_tickets_created",
    )
    organisation = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    site = models.ForeignKey(
        "scheduler.LocationSite",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets",
    )
    current_assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_tickets_assigned",
    )
    resolution_summary = models.TextField(blank=True, default="")
    # Legacy column. New refer-backs write referred_back_reason and mirror here
    # so older clients that still read invalidation_reason keep working.
    invalidation_reason = models.TextField(blank=True, default="")
    referred_back_reason = models.TextField(blank=True, default="")
    referred_back_at = models.DateTimeField(null=True, blank=True)
    auto_closed = models.BooleanField(
        default=False,
        db_index=True,
        help_text="True when Celery closed the ticket after 24h in RESOLVED or REFERRED_BACK.",
    )
    status_entered_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="When the ticket entered its current status. Auto-close clock for RESOLVED and REFERRED_BACK.",
    )
    created_on = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_on = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    # Phase 7 SLA: due from priority defaults and/or explicit due_at on create.
    due_at = models.DateTimeField(
        null=True,
        blank=True,
        db_index=True,
        help_text="SLA due datetime; derived from priority unless set explicitly.",
    )
    sla_warning_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When SUPPORT was notified that due_at is approaching.",
    )
    sla_breach_notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text="When SUPPORT was notified that due_at was breached.",
    )

    class Meta:
        ordering = ["-created_on"]
        indexes = [
            models.Index(fields=["status", "created_on"], name="support_tkt_status_created"),
            models.Index(fields=["organisation", "status"], name="support_tkt_org_status"),
            models.Index(fields=["site", "status"], name="support_tkt_site_status"),
            models.Index(fields=["current_assignee", "status"], name="support_tkt_assignee_st"),
            models.Index(fields=["created_by", "created_on"], name="support_tkt_creator_created"),
            models.Index(fields=["priority", "status"], name="support_tkt_priority_status"),
            models.Index(fields=["organisation", "site", "status"], name="support_tkt_org_site_st"),
            models.Index(fields=["due_at", "status"], name="support_tkt_due_status"),
            models.Index(
                fields=["status", "status_entered_at"],
                name="support_tkt_status_entered",
            ),
        ]

    def __str__(self):
        return f"{self.ticket_number} ({self.status})"

    @staticmethod
    def generate_ticket_number():
        year = timezone.now().year
        prefix = f"SP-{year}-"
        with transaction.atomic():
            last = (
                SupportTicket.objects.select_for_update()
                .filter(ticket_number__startswith=prefix)
                .order_by("-ticket_number")
                .first()
            )
            if last:
                try:
                    seq = int(last.ticket_number.rsplit("-", 1)[-1]) + 1
                except (TypeError, ValueError):
                    seq = 1
            else:
                seq = 1
            return f"{prefix}{seq:05d}"


class TicketAssignment(models.Model):
    class Action(models.TextChoices):
        PICKUP = "PICKUP", "Pickup"
        ASSIGN = "ASSIGN", "Assign"
        REASSIGN = "REASSIGN", "Reassign"
        TRANSFER = "TRANSFER", "Transfer"
        REFER_BACK = "REFER_BACK", "Refer back"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="assignments"
    )
    from_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_assignments_from",
    )
    to_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_assignments_to",
    )
    action = models.CharField(max_length=16, choices=Action.choices)
    comment = models.TextField(blank=True, default="")
    assigned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_assignments_made",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_on"]


class TicketComment(models.Model):
    class Visibility(models.TextChoices):
        PUBLIC = "PUBLIC", "Public"
        INTERNAL = "INTERNAL", "Internal"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="comments"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="support_ticket_comments",
    )
    body = models.TextField()
    visibility = models.CharField(
        max_length=16,
        choices=Visibility.choices,
        default=Visibility.PUBLIC,
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_on"]


def ticket_attachment_upload_to(instance, filename):
    return f"support_tickets/{instance.ticket_id}/{filename}"


class TicketAttachment(models.Model):
    class Kind(models.TextChoices):
        ASSET = "ASSET", "Asset"  # image / video (Incident photo/video parity)
        AUDIO = "AUDIO", "Audio"  # Incident description_audio parity
        FILE = "FILE", "File"  # generic docs / legacy uploads

    class Purpose(models.TextChoices):
        CREATE = "CREATE", "Create"
        CLOSE = "CLOSE", "Close evidence"
        GENERAL = "GENERAL", "General"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="attachments"
    )
    comment = models.ForeignKey(
        TicketComment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attachments",
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="support_ticket_attachments",
    )
    file = models.FileField(upload_to=ticket_attachment_upload_to)
    file_name = models.CharField(max_length=255)
    mime_type = models.CharField(max_length=128, blank=True, default="")
    size_bytes = models.PositiveIntegerField(default=0)
    kind = models.CharField(
        max_length=16,
        choices=Kind.choices,
        default=Kind.FILE,
        db_index=True,
        help_text="ASSET (image/video), AUDIO, or FILE (docs).",
    )
    purpose = models.CharField(
        max_length=16,
        choices=Purpose.choices,
        default=Purpose.GENERAL,
        db_index=True,
        help_text="CREATE / CLOSE evidence / GENERAL attachment.",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_on"]


class TicketActivityLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(
        SupportTicket, on_delete=models.CASCADE, related_name="activity_logs"
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="support_ticket_activities",
    )
    action = models.CharField(max_length=64, db_index=True)
    old_value = models.TextField(blank=True, default="")
    new_value = models.TextField(blank=True, default="")
    comment = models.TextField(blank=True, default="")
    metadata_json = models.JSONField(default=dict, blank=True)
    is_public = models.BooleanField(
        default=True,
        help_text="If false, hidden from ticket creators (internal-only events).",
    )
    created_on = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["created_on"]
