"""Celery tasks for visitor module (overstay, etc.)."""

import logging

from celery import shared_task

from visitor.overstay import process_vehicle_overstay

logger = logging.getLogger(__name__)


@shared_task(name="visitor.tasks.check_vehicle_overstay")
def check_vehicle_overstay(location_id=None):
    """
    Celery Beat: raise vehicle overstay SOS for checked-in vehicles
    past SiteSetting vehicle_overstay_hours (org-wide whitelist skipped).
    """
    summary = process_vehicle_overstay(location_id=location_id)
    logger.info("[CELERY] check_vehicle_overstay %s", summary)
    return summary
