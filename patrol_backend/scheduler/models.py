import uuid
from django.db import models
from django.utils.timezone import now

class Location(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=255)
    address = models.TextField(blank=True)

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
    recurrence = models.CharField(
        max_length=32,
        choices=[
            ('daily', 'Daily'),
            ('weekly', 'Weekly'),
            ('custom', 'Custom'),
        ],
        default='daily'
    )
    checkpoints = models.JSONField(default=list)  # Format: [{'checkpoint_id': str, 'time': 'HH:MM'}]

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

            if self.start_time and self.end_time:
                is_overnight = self.end_time <= self.start_time
                if not is_overnight and not (self.start_time <= cp_time_obj <= self.end_time):
                    raise ValidationError(f"Checkpoint time {cp_time} is outside shift hours.")
                if is_overnight and not (cp_time_obj >= self.start_time or cp_time_obj <= self.end_time):
                    raise ValidationError(f"Checkpoint time {cp_time} is outside overnight shift hours.")

    def get_checkpoint_objects(self):
        """Returns list of Checkpoint objects with their assigned times."""
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
        return self.name

class Assignment(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    shift = models.ForeignKey(Shift, on_delete=models.CASCADE)
    assigned_date = models.DateField(default=now)

class Checkpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    location = models.ForeignKey(Location, on_delete=models.CASCADE)
    label = models.CharField(max_length=255)
    type = models.CharField(max_length=16, choices=[
        ('qr', 'QR Code'),
        ('nfc', 'NFC Tag'),
        ('gps', 'GPS Coordinate'),
    ])
    data = models.TextField()  # QR/NFC code or GPS lat/lng

    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)

    def __str__(self):
        return f"{self.label} ({self.type})"
