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
        checkpoint_ids = [item.get('checkpoint_id') for item in self.checkpoints if item.get('checkpoint_id')]
        existing_ids = set(Checkpoint.objects.filter(id__in=checkpoint_ids).values_list('id', flat=True))

        for item in self.checkpoints:
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
        checkpoint_ids = [item['checkpoint_id'] for item in self.checkpoints if 'checkpoint_id' in item]
        checkpoints = Checkpoint.objects.in_bulk(checkpoint_ids)
        return [
            {
                "checkpoint": checkpoints.get(item['checkpoint_id']),
                "time": item['time']
            }
            for item in self.checkpoints if item['checkpoint_id'] in checkpoints
        ]

    def __str__(self):
        return f"Assignment for {self.guard}"

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
    key = models.CharField(max_length=255, unique=True)
    value = models.TextField(blank=True)
    unit = models.CharField(max_length=10, null=True, blank=True)  # ✅ Optional unit

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
        indexes = [
            models.Index(fields=["is_deleted"]),
        ]
