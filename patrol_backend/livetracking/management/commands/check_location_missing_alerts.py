from django.core.management.base import BaseCommand

from livetracking.boundary_runtime import run_location_missing_checks


class Command(BaseCommand):
    help = (
        "Create location_missing tracking alerts for on-duty users "
        "without GPS within the org timeout. "
        "Normally run by Celery Beat (livetracking.tasks.check_location_missing_alerts every 2 min)."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--location-id",
            dest="location_id",
            default=None,
            help="Limit scan to one organisation (Location UUID).",
        )

    def handle(self, *args, **options):
        summary = run_location_missing_checks(location_id=options.get("location_id"))
        self.stdout.write(self.style.SUCCESS(str(summary)))
