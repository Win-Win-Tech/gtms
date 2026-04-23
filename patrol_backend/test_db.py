import os
import django
import sys

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
django.setup()

from dashboard.models import CheckIn
import pytz

checkin_id = "74f6a410-edf5-4546-ae65-2ff3843bcb33"
try:
    c = CheckIn.objects.get(id=checkin_id)
    print("CheckIn found!")
    print(f"Timestamp: {c.timestamp}")
    print(f"Guard ID: {c.guard_id} (type {type(c.guard_id)})")
    print(f"Shift ID: {c.shift_id} (type {type(c.shift_id)})")
    print(f"Checkpoint ID: {c.checkpoint_id} (type {type(c.checkpoint_id)})")
except Exception as e:
    print("Error:", e)
