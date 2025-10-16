from django.db import models
from django.contrib.auth import get_user_model
import uuid
from django.utils import timezone
from scheduler.models import Checkpoint, Location 

User = get_user_model()

SEVERITY_CHOICES = [
    ('High', 'High'),
    ('Medium', 'Medium'),
    ('Low', 'Low'),
]

STATUS_CHOICES = [
    ('Open', 'Open'),
    ('In-Progress', 'In-Progress'),
    ('Closed', 'Closed'),
]

def media_upload_path(instance, filename):
    return f'incidents/{instance.ticket_number}/{filename}'

class incidentreport(models.Model):
    #ticket_number = models.CharField(max_length=12, unique=True, editable=False)
    ticket_number = models.CharField(
    max_length=12,
    unique=True,
    editable=False)
    #default=lambda: str(uuid.uuid4())[:12].upper())
    # Core incident details
    #severity = models.CharField(max_length=10, choices=SEVERITY_CHOICES)
    severity = models.CharField(
    max_length=10,
    choices=SEVERITY_CHOICES,
    default='Medium')
    #incident_description = models.TextField(help_text="Details about the incident")
    incident_description = models.TextField(
    default="Incident description not provided",
    help_text="Details about the incident")
    #closure_description = models.TextField(blank=True, null=True, help_text="Details about the resolution")
    closure_description = models.TextField(
    default="Incident description not provided",
    help_text="Details about the incident")

    photo = models.ImageField(upload_to=media_upload_path, null=True, blank=True)
    video = models.FileField(upload_to=media_upload_path, null=True, blank=True)
    
    # Status tracking
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='Open')
    
    # Timestamps and user tracking
    created_by = models.ForeignKey(User, related_name='created_incidents', on_delete=models.SET_NULL, null=True)
    #created_on = models.DateTimeField(auto_now_add=True)
    created_on = models.DateTimeField(default=timezone.now)


    assigned_by = models.ForeignKey(User, related_name='assigned_incidents', on_delete=models.SET_NULL, null=True, blank=True)
    assigned_on = models.DateTimeField(null=True, blank=True)
    assigned_to = models.ForeignKey(User, related_name='incidents_assigned_to', on_delete=models.SET_NULL, null=True, blank=True)

    resolved_by = models.ForeignKey(User, related_name='resolved_incidents', on_delete=models.SET_NULL, null=True, blank=True)
    resolved_on = models.DateTimeField(null=True, blank=True)

    checkpoint = models.ForeignKey(Checkpoint, on_delete=models.SET_NULL, null=True, blank=True, related_name='incidents')
    location = models.ForeignKey(Location, on_delete=models.SET_NULL, null=True, blank=True, related_name='incidents')

    def save(self, *args, **kwargs):
        # Generate ticket number if not set
        if not self.ticket_number:
            self.ticket_number = str(uuid.uuid4())[:12].upper()

        # Status logic
        if self.resolved_by and self.resolved_on:
            self.status = 'Closed'
        elif self.assigned_to and self.assigned_on:
            self.status = 'In-Progress'
        else:
            self.status = 'Open'

        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.ticket_number} - {self.status}"
