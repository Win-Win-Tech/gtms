import logging

from celery import shared_task

from livetracking.boundary_runtime import run_location_missing_checks

logger = logging.getLogger(__name__)


@shared_task
def check_location_missing_alerts(location_id=None):
    """
    Celery Beat task: create location_missing alerts for on-duty users
    without GPS within the org timeout (SiteSetting: location_missing_timeout_min).
    """
    summary = run_location_missing_checks(location_id=location_id)
    logger.info("[CELERY] check_location_missing_alerts %s", summary)
    return summary
