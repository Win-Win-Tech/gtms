"""Hooks called from attendance checkout flows."""

import logging

logger = logging.getLogger(__name__)


def on_attendance_checkout(user, attendance=None) -> None:
    """Best-effort cleanup after any checkout path."""
    try:
        from livetracking.boundary_runtime import clear_tracking_state_on_checkout

        clear_tracking_state_on_checkout(user, attendance=attendance)
    except Exception:
        logger.exception(
            "[BOUNDARY] Failed to clear tracking state on checkout user=%s",
            getattr(user, "id", None),
        )
