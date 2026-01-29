from django.db import models
from django.conf import settings
from scheduler.models import Location

class UserLiveLocation(models.Model):
    """Stores only the MOST RECENT location of a user for live dashboard viewing."""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='live_location'
    )
    location = models.ForeignKey(
        Location, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='live_user_locations'
    )
    latitude = models.DecimalField(max_digits=12, decimal_places=9, null=True)
    longitude = models.DecimalField(max_digits=12, decimal_places=9, null=True)
    last_updated = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "User Live Location"
        verbose_name_plural = "User Live Locations"

    def __str__(self):
        return f"{self.user.email} - {self.location.name if self.location else 'No Location'}"

class UserLocationHistory(models.Model):
    """Stores ALL location updates for playback purposes."""
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, 
        on_delete=models.CASCADE, 
        related_name='location_history'
    )
    location = models.ForeignKey(
        Location, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='location_history_records'
    )
    latitude = models.DecimalField(max_digits=12, decimal_places=9)
    longitude = models.DecimalField(max_digits=12, decimal_places=9)
    timestamp = models.DateTimeField(db_index=True) # Index for fast time-range queries

    class Meta:
        verbose_name = "User Location History"
        verbose_name_plural = "User Location History"
        # COMPOSITE INDEX: Critical for fast playback performance
        indexes = [
            models.Index(fields=['user', 'timestamp']),
            models.Index(fields=['location', 'timestamp']),
        ]

    def __str__(self):
        return f"{self.user.email} at {self.timestamp}"

