import uuid
from django.db import models
from django.conf import settings
from django.utils.timezone import now
from django.utils import timezone


class ActiveManager(models.Manager):
    def get_queryset(self):
        return super().get_queryset().filter(is_deleted=False)


class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_modified"
    )

    # Soft delete fields
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="location_deleted"
        )
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_qr_scan_enable = models.BooleanField(default=True)
    is_face_attendance_enabled = models.BooleanField(
        default=False,
        help_text="If True, checkin_v4/checkout_v4 require a live face match to the user's enrolled face_photo/encoding.",
    )
    is_ai_extraction_enabled = models.BooleanField(
        default=False,
        help_text="If True, AI OCR auto-extraction (ID/vehicle) is enabled for this location/organization.",
    )

    # Managers
    objects = ActiveManager()        # default: only active
    all_objects = models.Manager()   # all records, including deleted

    def delete(self, user=None, using=None, keep_parents=False):
        """Override delete() for safe delete"""
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    class Meta:
        indexes = [
            models.Index(fields=["is_deleted"]),
        ]

    def __str__(self):
        return self.name


class LocationSite(models.Model):
    class BoundaryType(models.TextChoices):
        NONE = "none", "None"
        CIRCLE = "circle", "Circle"
        POLYGON = "polygon", "Polygon"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name="sites")
    name = models.CharField(max_length=255)
    latitude = models.FloatField()
    longitude = models.FloatField()
    is_active = models.BooleanField(default=True)

    boundary_type = models.CharField(
        max_length=16,
        choices=BoundaryType.choices,
        default=BoundaryType.NONE,
    )
    boundary_radius_m = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Meters from center; used when boundary_type=circle",
    )
    boundary_polygon = models.JSONField(
        null=True,
        blank=True,
        help_text="List of [lat, lng] points when boundary_type=polygon",
    )
    boundary_enabled = models.BooleanField(
        default=False,
        help_text="Derived: True when breach or location-missing alerts are enabled for this site",
    )
    breach_alerts_enabled = models.BooleanField(
        default=False,
        help_text="Per-site on/off for boundary breach alerts",
    )
    location_missing_alerts_enabled = models.BooleanField(
        default=False,
        help_text="Per-site on/off for location-missing alerts",
    )

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        self.boundary_enabled = bool(
            self.breach_alerts_enabled or self.location_missing_alerts_enabled
        )
        if self.boundary_type == self.BoundaryType.CIRCLE:
            self.boundary_polygon = None
        elif self.boundary_type == self.BoundaryType.POLYGON:
            self.boundary_radius_m = None
        elif self.boundary_type == self.BoundaryType.NONE:
            self.boundary_radius_m = None
            self.boundary_polygon = None
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.location.name} - {self.name}"


#class Shift(models.Model):
#    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#    location = models.ForeignKey(Location, on_delete=models.CASCADE)
#    start_time = models.TimeField()
#    end_time = models.TimeField()
#    recurrence = models.CharField(max_length=32, choices=[
#        ('daily', 'Daily'),
#        ('weekly', 'Weekly'),
#        ('custom', 'Custom'),
#    ], default='daily')


class Shift(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100, null=True)
    location = models.ForeignKey('Location', on_delete=models.CASCADE)
    start_time = models.TimeField()
    end_time = models.TimeField()
    is_default = models.BooleanField(default=False)
    checkpoint_template = models.ForeignKey(
        'CheckpointTemplate',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='default_for_shifts'
    )

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="shift_deleted"
    )

    # Managers
    objects = ActiveManager()
    all_objects = models.Manager()

    def delete(self, user=None, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def __str__(self):
        return self.name
    # checkpoints = models.JSONField(default=list)  # Format: [{'checkpoint_id': str, 'time': 'HH:MM'}]

    # def clean(self):
    #     """Validate that all checkpoint_ids exist and times are valid."""
    #     checkpoint_ids = [item.get('checkpoint_id') for item in self.checkpoints if item.get('checkpoint_id')]
    #     existing_ids = set(Checkpoint.objects.filter(id__in=checkpoint_ids).values_list('id', flat=True))

    #     for item in self.checkpoints:
    #         cp_id = item.get('checkpoint_id')
    #         cp_time = item.get('time')

    #         if not cp_id or not cp_time:
    #             raise ValidationError("Each checkpoint entry must include 'checkpoint_id' and 'time'.")

    #         if cp_id not in existing_ids:
    #             raise ValidationError(f"Checkpoint with ID {cp_id} does not exist.")

    #         try:
    #             cp_time_obj = datetime.strptime(cp_time, "%H:%M").time()
    #         except ValueError:
    #             raise ValidationError(f"Invalid time format for checkpoint {cp_id}: {cp_time}")

    #         if self.start_time and self.end_time:
    #             is_overnight = self.end_time <= self.start_time
    #             if not is_overnight and not (self.start_time <= cp_time_obj <= self.end_time):
    #                 raise ValidationError(f"Checkpoint time {cp_time} is outside shift hours.")
    #             if is_overnight and not (cp_time_obj >= self.start_time or cp_time_obj <= self.end_time):
    #                 raise ValidationError(f"Checkpoint time {cp_time} is outside overnight shift hours.")

    # def get_checkpoint_objects(self):
    #     """Returns list of Checkpoint objects with their assigned times."""
    #     checkpoint_ids = [item['checkpoint_id'] for item in self.checkpoints if 'checkpoint_id' in item]
    #     checkpoints = Checkpoint.objects.in_bulk(checkpoint_ids)
    #     return [
    #         {
    #             "checkpoint": checkpoints.get(item['checkpoint_id']),
    #             "time": item['time']
    #         }
    #         for item in self.checkpoints if item['checkpoint_id'] in checkpoints
    #     ]

    # def __str__(self):
    #     return self.name

class Assignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    location = models.ForeignKey('Location', on_delete=models.CASCADE, null=True, blank=True)
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE)
    start_date = models.DateField(default=timezone.now)
    end_date = models.DateField(default=timezone.now)
    checkpoints = models.JSONField(default=list)  # [{'checkpoint_id': str, 'time': 'HH:MM'}]

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignment_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignment_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="assignment_deleted"
    )

    # Managers
    objects = ActiveManager()
    all_objects = models.Manager()

    def delete(self, user=None, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def clean(self):
        """Validate that all checkpoint_ids exist and times are valid."""
        checkpoints = self.checkpoints or []
        checkpoint_ids = [item.get('checkpoint_id') for item in checkpoints if item.get('checkpoint_id')]
        existing_ids = set(Checkpoint.objects.filter(id__in=checkpoint_ids).values_list('id', flat=True))

        for item in checkpoints:
            cp_id = item.get('checkpoint_id')
            cp_time = item.get('time')

            if not cp_id or not cp_time:
                raise ValidationError("Each checkpoint entry must include 'checkpoint_id' and 'time'.")

            if cp_id not in existing_ids:
                raise ValidationError(f"Checkpoint with ID {cp_id} does not exist.")

            try:
                cp_time_obj = datetime.strptime(cp_time, "%H:%M").time()
            except ValueError:
                raise ValidationError(f"Invalid time format for checkpoint {cp_id}: {cp_time}")

            if self.shift.start_time and self.shift.end_time:
                is_overnight = self.shift.end_time <= self.shift.start_time
                if not is_overnight and not (self.shift.start_time <= cp_time_obj <= self.shift.end_time):
                    raise ValidationError(f"Checkpoint time {cp_time} is outside shift hours.")
                if is_overnight and not (cp_time_obj >= self.shift.start_time or cp_time_obj <= self.shift.end_time):
                    raise ValidationError(f"Checkpoint time {cp_time} is outside overnight shift hours.")

    def get_checkpoint_objects(self):
        checkpoints_data = self.checkpoints or []
        checkpoint_ids = [item['checkpoint_id'] for item in checkpoints_data if 'checkpoint_id' in item]
        checkpoints = Checkpoint.objects.in_bulk(checkpoint_ids)
        return [
            {
                "checkpoint": checkpoints.get(item['checkpoint_id']),
                "time": item['time']
            }
            for item in checkpoints_data if item['checkpoint_id'] in checkpoints
        ]

    def __str__(self):
        return f"Assignment for {self.guard}"


class AssignmentDailySite(models.Model):
    """Posted site for one guard on one calendar date. Not stored on Assignment."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="daily_sites",
    )
    date = models.DateField(db_index=True)
    site = models.ForeignKey(
        "LocationSite",
        on_delete=models.CASCADE,
        related_name="daily_assignments",
    )
    location = models.ForeignKey(
        "Location",
        on_delete=models.CASCADE,
        related_name="daily_assignment_sites",
    )
    assignment = models.ForeignKey(
        Assignment,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_sites",
    )
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["guard", "date"], name="uniq_daily_site_guard_date"),
        ]
        indexes = [
            models.Index(fields=["guard", "date"]),
        ]

    def __str__(self):
        return f"{self.guard} {self.date} {self.site}"


class GuardSiteCache(models.Model):
    """Site switches after attendance is marked that day. Many rows per day; return latest."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="site_cache",
    )
    date = models.DateField(db_index=True)
    site = models.ForeignKey(
        "LocationSite",
        on_delete=models.CASCADE,
        related_name="guard_site_cache",
    )
    location = models.ForeignKey(
        "Location",
        on_delete=models.CASCADE,
        related_name="guard_site_cache",
    )
    created_on = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["guard", "date", "created_on"]),
        ]
        ordering = ["-created_on"]

    def __str__(self):
        return f"{self.guard} {self.date} cache {self.site}"

class Checkpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey('Location', on_delete=models.CASCADE)
    label = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=[
        ('qr', 'QR Code'),
        ('nfc', 'NFC Tag'),
        ('gps', 'GPS Coordinate'),
    ])
    data = models.TextField()  # QR/NFC code or GPS lat/lng
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkpoint_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkpoint_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkpoint_deleted"
    )

    # Managers
    objects = ActiveManager()
    all_objects = models.Manager()

    def delete(self, user=None, using=None, keep_parents=False):
        """Soft delete instead of hard delete"""
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def __str__(self):
        return f"{self.label} ({self.type})"

    class Meta:
        indexes = [
            models.Index(fields=["is_deleted"]),
        ]

class SiteSetting(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    key = models.CharField(max_length=255)
    value = models.TextField(blank=True)
    unit = models.CharField(max_length=10, null=True, blank=True)
    location = models.ForeignKey('Location', on_delete=models.CASCADE, null=True, blank=True, related_name="site_settings")
    propagate_to_orgs = models.BooleanField(
        default=True,
        help_text=(
            "If True (and location is NULL), this global key is copied to each organisation. "
            "If False, the key stays global-only (e.g. app_version / force_update)."
        ),
    )
    is_deleted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('key', 'location')
        ordering = ['key']
        indexes = [
            models.Index(fields=["is_deleted"]),
            models.Index(fields=["key", "location"]),
        ]

    @classmethod
    def get_setting(cls, key, location_id=None, default_value=None):
        """
        Get setting value for a specific location or fallback to global setting.
        Logic: Specific Location > Global (location is NULL) > default_value
        """
        setting = cls.objects.filter(key=key).filter(
            models.Q(location_id=location_id) | models.Q(location_id__isnull=True)
        ).order_by(models.F('location_id').desc(nulls_last=True)).first()
        
        return setting.value if setting else default_value

    # Audit and soft delete fields as before...

    def __str__(self):
        return f"{self.key} = {self.value}{self.unit or ''}"
  # Store values as strings or JSON if needed
    
    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_setting_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_setting_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="site_setting_deleted"
    )

    # Managers
    objects = ActiveManager()        # default: only active
    all_objects = models.Manager()   # all records, including deleted

    def delete(self, user=None, using=None, keep_parents=False):
        """Override delete() for safe delete"""
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def __str__(self):
        return self.key

    class Meta:
        unique_together = ('key', 'location')
        indexes = [
            models.Index(fields=["is_deleted"]),
        ]


import uuid
from django.db import models
from django.conf import settings
from django.utils import timezone

class CheckpointTemplate(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    shift = models.ForeignKey('Shift', on_delete=models.CASCADE, related_name='templates')
    location = models.ForeignKey('Location', on_delete=models.CASCADE, related_name='checkpoint_templates', null=True, blank=True)
    template_name = models.CharField(max_length=100)

    # JSON field to store checkpoint ID and time pairs
    checkpoints = models.JSONField(default=list)  # Example: [{"checkpoint_id": "uuid", "time": "HH:MM"}]

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkpointtemplate_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkpointtemplate_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checkpointtemplate_deleted"
    )

    def delete(self, user=None, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def __str__(self):
        return self.template_name


class ChecklistItem(models.Model):
    """
    Master checklist items (e.g. 'Close gate', 'Switch off lights').
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey('Location', on_delete=models.CASCADE, related_name='checklist_items')
    label = models.CharField(max_length=255)

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklistitem_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklistitem_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklistitem_deleted"
    )

    def delete(self, user=None, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def __str__(self):
        return self.label


class ChecklistTemplate(models.Model):
    """
    Checklist templates (groups of checklist items) per location/shift.
    Stores item list as JSON referencing ChecklistItem IDs.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey('Location', on_delete=models.CASCADE, related_name='checklist_templates')
    name = models.CharField(max_length=255)

    # Example: [{"checklist_item_id": "uuid", "sort_order": 1}]
    checklist_items = models.JSONField(default=list)

    # Audit fields
    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklisttemplate_master_created"
    )
    modified_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklisttemplate_master_modified"
    )

    # Soft delete
    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="checklisttemplate_master_deleted"
    )

    def delete(self, user=None, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_on = timezone.now()
        if user:
            self.deleted_by = user
        self.save()

    def __str__(self):
        return self.name
