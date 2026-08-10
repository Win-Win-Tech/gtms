"""
One-time backfill AttendanceCheckin from CheckInLog for a location.

Default: Golden Win, from day-30 of last month through today (location TZ).

  python manage.py backfill_attendance_from_logs --dry-run
  python manage.py backfill_attendance_from_logs
  python manage.py backfill_attendance_from_logs --from-date=2026-07-30 --to-date=2026-08-10

Not wired into checkin_v4 / checkout_v4.
"""
from datetime import datetime

from django.core.management.base import BaseCommand, CommandError

from patrol_backend.utils.attendance_backfill import (
    GOLDEN_WIN_LOCATION_ID,
    backfill_attendance_from_checkin_logs,
    default_from_date_last_month_30,
)


def _parse_date(value: str):
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise CommandError(f"Invalid date '{value}' (use YYYY-MM-DD)") from exc


class Command(BaseCommand):
    help = (
        "Backfill AttendanceCheckin rows from CheckInLog "
        "(duration, counts, images, site, pa_status). "
        "Default location=Golden Win; default from=last-month-30 → today."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--location",
            default=GOLDEN_WIN_LOCATION_ID,
            help=f"Org location UUID (default Golden Win: {GOLDEN_WIN_LOCATION_ID})",
        )
        parser.add_argument(
            "--from-date",
            default=None,
            help="Inclusive start YYYY-MM-DD (default: day 30 of previous month)",
        )
        parser.add_argument(
            "--to-date",
            default=None,
            help="Inclusive end YYYY-MM-DD (default: today in location TZ)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Print what would be created/updated without saving",
        )

    def handle(self, *args, **options):
        from_date = _parse_date(options["from_date"]) if options["from_date"] else None
        to_date = _parse_date(options["to_date"]) if options["to_date"] else None

        if from_date is None:
            self.stdout.write(
                f"Using default from-date = last-month-30 "
                f"(preview: {default_from_date_last_month_30()})"
            )

        try:
            summary = backfill_attendance_from_checkin_logs(
                location_id=options["location"],
                from_date=from_date,
                to_date=to_date,
                dry_run=options["dry_run"],
                stdout=self.stdout,
            )
        except ValueError as exc:
            raise CommandError(str(exc)) from exc

        style = self.style.WARNING if options["dry_run"] else self.style.SUCCESS
        self.stdout.write(
            style(
                f"Summary: created={summary['created']} updated={summary['updated']} "
                f"errors={len(summary['errors'])} dry_run={summary['dry_run']}"
            )
        )
        if summary["errors"]:
            for err in summary["errors"][:20]:
                self.stderr.write(self.style.ERROR(err))
