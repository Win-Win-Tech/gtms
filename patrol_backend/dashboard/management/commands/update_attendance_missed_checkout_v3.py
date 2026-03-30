from datetime import timedelta

import pytz
from django.core.management.base import BaseCommand
from django.db.models import F, Q
from django.utils import timezone

from scheduler.models import SiteSetting
from patrol_backend.utils.timezone_utils import (
    combine_date_time_in_user_tz,
    get_user_timezone_from_request,
    to_user_timezone,
)

from dashboard.models import AttendanceCheckin


def _get_site_setting_int(key, location_id=None, default_value=None):
    try:
        raw = SiteSetting.get_setting(key=key, location_id=location_id, default_value=default_value)
        if raw is None:
            return default_value
        return int(raw)
    except Exception:
        return default_value


class Command(BaseCommand):
    help = "Backfill/refresh v3 pa_status for open sessions: OW for still-on-duty, M for missed checkout after shift_end + grace."

    def add_arguments(self, parser):
        parser.add_argument("--batch-size", type=int, default=1000)
        parser.add_argument("--dry-run", action="store_true", help="Do not update DB, only print counts.")

    def handle(self, *args, **options):
        batch_size = options["batch_size"]
        dry_run = options["dry_run"]

        now_utc = timezone.now()

        # Candidates:
        # - last_checkin exists
        # - there is no checkout OR last checkin is newer than last checkout (open session)
        candidates = (
            AttendanceCheckin.objects.select_related("shift", "org_location")
            .filter(last_checkin_time__isnull=False)
            .filter(Q(last_checkout_time__isnull=True) | Q(last_checkin_time__gt=F("last_checkout_time")))
            .order_by("shift_date")
        )

        tz_cache = {}
        grace_cache = {}
        ow_ids = []
        m_ids = []

        total = candidates.count()
        self.stdout.write(f"[update_attendance_missed_checkout_v3] candidates={total} now_utc={now_utc.isoformat()}")

        for row in candidates.iterator(chunk_size=batch_size):
            org_loc_id = row.org_location_id
            if org_loc_id not in tz_cache:
                tz_cache[org_loc_id] = get_user_timezone_from_request(None, location_id=org_loc_id)
                grace_cache[org_loc_id] = _get_site_setting_int(
                    key="shift_grace_time",
                    location_id=org_loc_id,
                    default_value=30,
                )

            tz = tz_cache[org_loc_id] or pytz.timezone("Asia/Kolkata")
            grace_minutes = grace_cache[org_loc_id] if grace_cache[org_loc_id] is not None else 30

            shift = row.shift
            if not shift:
                continue

            # shift_date is the logical shift-start day (business timezone).
            if row.shift_date:
                shift_start_date = row.shift_date
            else:
                # Fallback for older rows: derive shift_date from checkin_time
                base_dt_utc = row.checkin_time or row.last_checkin_time or row.created_on
                local_dt = to_user_timezone(base_dt_utc, tz)
                shift_start_date = local_dt.date()
                if shift.end_time <= shift.start_time and local_dt.time() < shift.end_time:
                    shift_start_date = shift_start_date - timedelta(days=1)

            is_overnight = shift.end_time <= shift.start_time
            shift_end_date = shift_start_date + timedelta(days=1) if is_overnight else shift_start_date

            shift_end_utc = combine_date_time_in_user_tz(shift_end_date, shift.end_time, tz)
            deadline_utc = shift_end_utc + timedelta(minutes=grace_minutes)

            if now_utc >= deadline_utc:
                m_ids.append(row.id)
            else:
                ow_ids.append(row.id)

        self.stdout.write(f"[update_attendance_missed_checkout_v3] to_set_OW={len(ow_ids)} to_set_M={len(m_ids)}")

        if dry_run:
            self.stdout.write("[dry-run] no database updates performed.")
            return

        # Only update if statuses exist in your model choices.
        if m_ids:
            AttendanceCheckin.objects.filter(id__in=m_ids).update(pa_status="M", modified_on=now_utc)
        if ow_ids:
            AttendanceCheckin.objects.filter(id__in=ow_ids).update(pa_status="OW", modified_on=now_utc)

        self.stdout.write("[update_attendance_missed_checkout_v3] done.")

