import uuid
from django.db import models
from django.utils.timezone import now

class CheckIn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    checkpoint = models.ForeignKey('scheduler.Checkpoint', on_delete=models.CASCADE)
    shift = models.ForeignKey('scheduler.Shift', on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=now)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    synced = models.BooleanField(default=True)  # False if offline, True when synced
