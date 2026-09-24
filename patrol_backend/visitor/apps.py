from django.apps import AppConfig


class VisitorConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'visitor'

    def ready(self):
        # Nested Celery module is not found by autodiscover (only visitor.tasks).
        # Import after apps are ready so -Q anpr workers register process_anpr_frame.
        # Must not import from patrol_backend.celery at module level (AppRegistryNotReady).
        from django.conf import settings

        if getattr(settings, "ANPR_ENABLED", False):
            import visitor.anpr.tasks  # noqa: F401
