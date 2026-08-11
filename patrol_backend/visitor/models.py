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

    VEHICLE_CAR = "car"
    VEHICLE_MOTORCYCLE = "motorcycle"
    VEHICLE_VAN = "van"
    VEHICLE_TRUCK = "truck"
    VEHICLE_BUS = "bus"
    VEHICLE_OTHER = "other"
    VEHICLE_TYPE_CHOICES = [
        (VEHICLE_CAR, "Car"),
        (VEHICLE_MOTORCYCLE, "Motorcycle"),
        (VEHICLE_VAN, "Van"),
        (VEHICLE_TRUCK, "Truck"),
        (VEHICLE_BUS, "Bus"),
        (VEHICLE_OTHER, "Other"),
    ]

    # Manual entry → pending_approval → (host approve = check-in)
    # Invitation → scheduled → (QR scan = check-in)
    STATUS_PENDING_APPROVAL = "pending_approval"
    STATUS_REVERTED = "reverted"
    STATUS_SCHEDULED = "scheduled"  # invitation only
    STATUS_CHECKED_IN = "checked_in"
    STATUS_CHECKED_OUT = "checked_out"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [
        (STATUS_PENDING_APPROVAL, "Pending Approval"),
        (STATUS_REVERTED, "Reverted"),
        (STATUS_SCHEDULED, "Scheduled"),
        (STATUS_CHECKED_IN, "Checked In"),
        (STATUS_CHECKED_OUT, "Checked Out"),
        (STATUS_CANCELLED, "Cancelled"),
    ]

    ENTRY_MANUAL = "manual"
    ENTRY_INVITATION = "invitation"
    ENTRY_SOURCE_CHOICES = [
        (ENTRY_MANUAL, "Manual Entry"),
        (ENTRY_INVITATION, "Invitation"),
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
    entry_source = models.CharField(
        max_length=16,
        choices=ENTRY_SOURCE_CHOICES,
        default=ENTRY_MANUAL,
        db_index=True,
    )
    visitor_type = models.CharField(
        max_length=32,
        choices=VISITOR_TYPE_CHOICES,
        default=TYPE_GUEST,
    )
    status = models.CharField(
        max_length=32,
        choices=STATUS_CHOICES,
        default=STATUS_PENDING_APPROVAL,
        db_index=True,
    )
    purpose_of_visit = models.CharField(max_length=500, blank=True, default="")
    vehicle_number = models.CharField(max_length=64, blank=True, default="")
    vehicle_type = models.CharField(
        max_length=32,
        choices=VEHICLE_TYPE_CHOICES,
        blank=True,
        default="",
    )
    company_name = models.CharField(max_length=255, blank=True, default="")
    remarks = models.TextField(blank=True, default="")
    revert_reason = models.TextField(blank=True, default="")

    # Manual entry: expected times (same-day). Invitation: visit_date for future.
    expected_arrival_time = models.DateTimeField(null=True, blank=True)
    expected_out_time = models.DateTimeField(null=True, blank=True)
    visit_date = models.DateField(
        null=True,
        blank=True,
        help_text="Visit day — today for manual entry; scheduled date for invitations",
    )
    visit_date_time = models.DateTimeField(
        null=True,
        blank=True,
        help_text="Legacy / optional scheduled datetime for invitations",
    )

    check_in_time = models.DateTimeField(null=True, blank=True)
    check_out_time = models.DateTimeField(null=True, blank=True)

    qr_token = models.CharField(max_length=64, unique=True, db_index=True)
    qr_image = models.ImageField(upload_to="visitor_qr/", null=True, blank=True)
    pass_image = models.ImageField(
        upload_to="visitor_pass/",
        null=True,
        blank=True,
        help_text="Visitor ID-card pass (org, name, visit date, host, QR)",
    )
    qr_expired = models.BooleanField(default=False)

    approved_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_approved",
    )
    approved_on = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_created",
    )
    scanned_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_scanned",
        help_text="Guard who scanned invite/scheduled QR into pending_approval",
    )
    checked_in_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_checked_in",
        help_text="User who performed the actual check-in",
    )
    checked_out_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="visitor_entries_checked_out",
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
        ordering = ["-created_on"]
        indexes = [
            models.Index(fields=["location", "status"]),
            models.Index(fields=["location", "check_in_time"]),
            models.Index(fields=["status", "check_in_time"]),
            models.Index(fields=["host", "status"]),
        ]

    def __str__(self):
        return f"Entry {self.id} ({self.status})"

    @property
    def is_qr_usable(self):
        if self.qr_expired or self.is_deleted:
            return False
        return self.status in (
            self.STATUS_PENDING_APPROVAL,
            self.STATUS_REVERTED,
            self.STATUS_SCHEDULED,
            self.STATUS_CHECKED_IN,
        )


class VisitorAsset(models.Model):
    ASSET_VISITOR_PHOTO = "visitor_photo"
    ASSET_ID_PROOF = "id_proof"
    ASSET_EXIT_PHOTO = "exit_photo"  # checkout image (mandatory)
    ASSET_VEHICLE_PHOTO = "vehicle_photo"
    ASSET_ADDITIONAL = "additional"
    ASSET_OTHER = "other"
    ASSET_TYPE_CHOICES = [
        (ASSET_VISITOR_PHOTO, "Visitor Photo"),
        (ASSET_ID_PROOF, "ID Proof"),
        (ASSET_EXIT_PHOTO, "Checkout Image"),
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
