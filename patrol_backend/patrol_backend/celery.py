# patrol_backend/celery.py
import os
from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'patrol_backend.settings')

# Create Celery app
app = Celery('patrol_backend')

# Load task modules from all registered Django app configs.
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all installed apps (loads visitor.tasks, etc.)
app.autodiscover_tasks()

# Nested package visitor.anpr.tasks is NOT picked up by autodiscover (only
# <app>.tasks). Import when ANPR is enabled so the -Q anpr worker registers
# process_anpr_frame. Default workers should leave ANPR_ENABLED unset/false
# and listen only to the celery queue (-Q celery) so they do not steal ANPR jobs.
from django.conf import settings  # noqa: E402

if getattr(settings, "ANPR_ENABLED", False):
    import visitor.anpr.tasks  # noqa: F401

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
