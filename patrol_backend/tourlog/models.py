import uuid
from django.db import models
from django.utils.timezone import now

class TourLog(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    shift = models.ForeignKey('scheduler.Shift', on_delete=models.CASCADE)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    completed = models.BooleanField(default=False)

class MissedCheckpoint(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    checkpoint = models.ForeignKey('scheduler.Checkpoint', on_delete=models.CASCADE)
    shift = models.ForeignKey('scheduler.Shift', on_delete=models.CASCADE)
    expected_time = models.DateTimeField()
    detected_at = models.DateTimeField(default=now)
    resolved = models.BooleanField(default=False)

class IncidentReport(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    shift = models.ForeignKey('scheduler.Shift', on_delete=models.CASCADE)
    checkpoint = models.ForeignKey('scheduler.Checkpoint', on_delete=models.SET_NULL, null=True, blank=True)
    timestamp = models.DateTimeField(default=now)
    notes = models.TextField()
    photo = models.ImageField(upload_to='incidents/', null=True, blank=True)
