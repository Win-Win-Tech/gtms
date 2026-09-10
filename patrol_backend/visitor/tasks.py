"""Celery task imports for the visitor app (autodiscover)."""

# Ensure ANPR tasks are registered when Celery loads `visitor.tasks`
from visitor.anpr.tasks import process_anpr_frame  # noqa: F401

__all__ = ["process_anpr_frame"]
