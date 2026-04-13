from django.apps import AppConfig
import logging
import threading

logger = logging.getLogger(__name__)

class DashboardConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'dashboard'

    def ready(self):
        from . import signals  # noqa: F401
        # First-phase kiosk requirement: rebuild face index on every app restart.
        def _warmup():
            try:
                from patrol_backend.utils.face_index import rebuild_all_indexes

                rebuild_all_indexes()
            except Exception as exc:
                logger.warning("Face index warmup skipped during startup: %s", exc)

        threading.Thread(target=_warmup, daemon=True).start()
