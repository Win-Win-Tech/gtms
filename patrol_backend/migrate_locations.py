import os
import sys
import django

# Add current directory to path
sys.path.append(os.getcwd())

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'patrol_backend.settings')
django.setup()

from scheduler.models import Location, LocationSite

locations = Location.objects.filter(latitude__isnull=False, longitude__isnull=False)
count = 0
for loc in locations:
    if not LocationSite.objects.filter(location=loc).exists():
        LocationSite.objects.create(
            location=loc,
            name="Main Boundary",
            latitude=loc.latitude,
            longitude=loc.longitude,
            radius=150,
            is_active=True
        )
        count += 1

print(f"Successfully migrated {count} locations to LocationSite.")
