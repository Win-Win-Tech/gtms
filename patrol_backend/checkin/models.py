import uuid
from django.db import models
from django.utils.timezone import now
#from .models import Location
from scheduler.models import Location  # adjust app name as needed

class CheckIn(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    guard = models.ForeignKey('authapp.User', on_delete=models.CASCADE)
    checkpoint = models.ForeignKey('scheduler.Checkpoint', on_delete=models.CASCADE)
    shift = models.ForeignKey('scheduler.Shift', on_delete=models.CASCADE)
    timestamp = models.DateTimeField(default=now)
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    synced = models.BooleanField(default=True)  # False if offline, True when synced

#class Checkpoint(models.Model):
#    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
#    location = models.ForeignKey(Location, on_delete=models.CASCADE)
#    label = models.CharField(max_length=255)
#    type = models.CharField(max_length=16, choices=[
#        ('qr', 'QR Code'),
#        ('nfc', 'NFC Tag'),
#        ('gps', 'GPS Coordinate'),
#    ])
#    data = models.TextField()  # QR/NFC code or GPS lat/lng

    # Add these fields to store geolocation
#    latitude = models.FloatField(null=True, blank=True)
#    longitude = models.FloatField(null=True, blank=True)

#    def __str__(self):
#        return f"{self.label} ({self.type})"