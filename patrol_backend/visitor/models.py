import uuid

from django.conf import settings
from django.db import models


class Visitor(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="visitors",
    )
    ic_passport_number = models.CharField(max_length=64, db_index=True)
    visitor_name = models.CharField(max_length=255)
    phone_number = models.CharField(max_length=32, blank=True, default="")

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitors_deleted",
    )

    class Meta:
        ordering = ["-created_on"]
        indexes = [
            models.Index(fields=["location", "ic_passport_number"]),
            models.Index(fields=["location", "is_deleted"]),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["location", "ic_passport_number"],
                condition=models.Q(is_deleted=False),
                name="unique_visitor_ic_per_location_active",
            ),
        ]

    def __str__(self):
        return f"{self.visitor_name} ({self.ic_passport_number})"


class VisitorEntry(models.Model):
    TYPE_CONTRACTOR = "contractor"
    TYPE_CLIENT = "client"
    TYPE_DELIVERY = "delivery"
    TYPE_GUEST = "guest"
    TYPE_OTHER = "other"
    VISITOR_TYPE_CHOICES = [
        (TYPE_CONTRACTOR, "Contractor"),
        (TYPE_CLIENT, "Client"),
        (TYPE_DELIVERY, "Delivery"),
        (TYPE_GUEST, "Guest"),
        (TYPE_OTHER, "Other"),
    ]

    STATUS_OPEN = "open"
    STATUS_CHECKED_IN = "checked_in"
    STATUS_CHECKED_OUT = "checked_out"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_OPEN, "Open"),
        (STATUS_CHECKED_IN, "Checked In"),
        (STATUS_CHECKED_OUT, "Checked Out"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visitor = models.ForeignKey(
        Visitor,
        on_delete=models.CASCADE,
        related_name="entries",
    )
    host = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_hosted",
    )
    location = models.ForeignKey(
        "scheduler.Location",
        on_delete=models.CASCADE,
        related_name="visitor_entries",
    )
    visitor_type = models.CharField(
        max_length=32,
        choices=VISITOR_TYPE_CHOICES,
        default=TYPE_GUEST,
    )
    status = models.CharField(
        max_length=16,
        choices=STATUS_CHOICES,
        default=STATUS_CHECKED_IN,
        db_index=True,
    )
    purpose_of_visit = models.CharField(max_length=500, blank=True, default="")
    vehicle_number = models.CharField(max_length=64, blank=True, default="")
    remarks = models.TextField(blank=True, default="")

    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)
    visit_date_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Scheduled visit time for invitations",
    )

    qr_token = models.CharField(max_length=64, unique=True, db_index=True)
    qr_image = models.ImageField(upload_to="visitor_qr/", null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_created",
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
        related_name="visitor_entries_deleted",
    )

    class Meta:
        ordering = ["-check_in_time", "-created_on"]
        indexes = [
            models.Index(fields=["location", "status"]),
            models.Index(fields=["location", "check_in_time"]),
            models.Index(fields=["status", "check_in_time"]),
        ]

    def __str__(self):
        return f"Entry {self.id} ({self.status})"


class VisitorAsset(models.Model):
    ASSET_VISITOR_PHOTO = "visitor_photo"  # single
    ASSET_ID_PROOF = "id_proof"  # single (IC copy)
    ASSET_EXIT_PHOTO = "exit_photo"  # single
    ASSET_VEHICLE_PHOTO = "vehicle_photo"  # single (optional plate)
    ASSET_ADDITIONAL = "additional"  # multiple allowed
    ASSET_OTHER = "other"
    ASSET_TYPE_CHOICES = [
        (ASSET_VISITOR_PHOTO, "Visitor Photo"),
        (ASSET_ID_PROOF, "ID Proof"),
        (ASSET_EXIT_PHOTO, "Exit Photo"),
        (ASSET_VEHICLE_PHOTO, "Vehicle Photo"),
        (ASSET_ADDITIONAL, "Additional"),
        (ASSET_OTHER, "Other"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    visitor_entry = models.ForeignKey(
        VisitorEntry,
        on_delete=models.CASCADE,
        related_name="assets",
    )
    asset_type = models.CharField(max_length=32, choices=ASSET_TYPE_CHOICES)
    file = models.FileField(upload_to="visitor_assets/")
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_on"]

    def __str__(self):
        return f"{self.asset_type} for {self.visitor_entry_id}"
