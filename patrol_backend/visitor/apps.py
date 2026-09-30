import sys

from django.apps import AppConfig


def _is_celery_worker() -> bool:
    return "celery" in (sys.argv[0] or "").lower() and "worker" in sys.argv[1:]


class VisitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'visitor'

    def ready(self):
        # Nested Celery module is not found by autodiscover (only visitor.tasks).
        # Import after apps are ready so -Q anpr workers register process_anpr_frame.
        # Must not import from patrol_backend.celery at module level (AppRegistryNotReady).
        # Every Celery worker registers it even without ANPR_ENABLED: the reader may
        # have the flag while the worker does not, and an unregistered task is
        # discarded ("Received unregistered task"). Models load lazily, so this is cheap.
        from django.conf import settings

        if getattr(settings, "ANPR_ENABLED", False) or _is_celery_worker():
            import visitor.anpr.tasks  # noqa: F401
