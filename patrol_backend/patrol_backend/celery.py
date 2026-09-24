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
# Nested visitor.anpr.tasks is registered from visitor.apps.VisitorConfig.ready()
# when ANPR_ENABLED=true (do NOT import it here — patrol_backend/__init__ loads
# this module before django.setup() and would raise AppRegistryNotReady).
app.autodiscover_tasks()

@app.task(bind=True)
def debug_task(self):
    print(f'Request: {self.request!r}')
