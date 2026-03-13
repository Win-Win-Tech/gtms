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
    has_checklist = models.BooleanField(default=False)


class CheckInChecklistAnswer(models.Model):
    """
    Stores checklist answers per scan (per CheckIn).
    One row per checklist submission, with all item answers in JSON.
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey('authapp.User', on_delete=models.CASCADE, related_name='checkin_checklist_answers')
    checkin = models.ForeignKey('CheckIn', on_delete=models.CASCADE, related_name='checklist_answers')
    location = models.ForeignKey(Location, on_delete=models.CASCADE, related_name='checkin_checklist_answers')
    checkpoint = models.ForeignKey('scheduler.Checkpoint', on_delete=models.SET_NULL, null=True, blank=True, related_name='checkin_checklist_answers')
    checklist_template = models.ForeignKey('scheduler.ChecklistTemplate', on_delete=models.SET_NULL, null=True, blank=True, related_name='checkin_checklist_answers')

    # Example: [{"checklist_item_id": "uuid", "checked": true, "remarks": ""}]
    answers = models.JSONField(default=list)
    remarks = models.TextField(null=True, blank=True)

    created_on = models.DateTimeField(auto_now_add=True)
    modified_on = models.DateTimeField(auto_now=True)

    is_deleted = models.BooleanField(default=False)
    deleted_on = models.DateTimeField(null=True, blank=True)
    deleted_by = models.ForeignKey(
        'authapp.User',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='checkin_checklist_answer_deleted'
    )

    def delete(self, user=None, using=None, keep_parents=False):
        self.is_deleted = True
        self.deleted_on = now()
        if user:
            self.deleted_by = user
        self.save()

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