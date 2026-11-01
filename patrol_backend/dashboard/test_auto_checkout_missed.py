from datetime import date, datetime, time, timedelta

import pytz
from django.test import SimpleTestCase

from dashboard.attendance_pa_status import compute_closed_pa_status
from dashboard.models import CheckInLog
from dashboard.tasks import auto_checkout_reason, shift_grace_deadline_utc


class _Shift:
    def __init__(self, start, end):
        self.start_time = start
        self.end_time = end


class ShiftGraceAutoCheckoutTests(SimpleTestCase):
    def test_reason_includes_grace_minutes(self):
        self.assertEqual(
            auto_checkout_reason(30),
            "Auto-checkout: no checkout by shift end + 30 min grace",
        )

    def test_auto_checkout_types_are_stable(self):
        self.assertEqual(CheckInLog.AUTO_CHECKOUT_TYPE_SHIFT_END, "shift_end")
        self.assertEqual(CheckInLog.AUTO_CHECKOUT_TYPE_BOUNDARY_BREACH, "boundary_breach")
        self.assertEqual(CheckInLog.AUTO_CHECKOUT_TYPE_LABELS["shift_end"], "Shift end")
        self.assertEqual(CheckInLog.AUTO_CHECKOUT_TYPE_LABELS["boundary_breach"], "Boundary breach")

    def test_day_shift_checkout_is_just_inside_deadline(self):
        tz = pytz.timezone("Asia/Kolkata")
        shift = _Shift(time(9, 0), time(18, 0))
        deadline, checkout_ts = shift_grace_deadline_utc(shift, date(2026, 10, 31), tz, 30)
        expected_end = tz.localize(datetime(2026, 10, 31, 18, 0)).astimezone(pytz.UTC)
        self.assertEqual(deadline, expected_end + timedelta(minutes=30))
        self.assertEqual(checkout_ts, deadline - timedelta(microseconds=1))
        self.assertLess(checkout_ts, deadline)

    def test_overnight_shift_deadline_is_next_calendar_day(self):
        tz = pytz.timezone("Asia/Kolkata")
        shift = _Shift(time(22, 0), time(6, 0))
        deadline, checkout_ts = shift_grace_deadline_utc(shift, date(2026, 10, 31), tz, 15)
        expected_end = tz.localize(datetime(2026, 11, 1, 6, 0)).astimezone(pytz.UTC)
        self.assertEqual(deadline, expected_end + timedelta(minutes=15))
        self.assertLess(checkout_ts, deadline)

    def test_closed_status_after_auto_checkout_matches_api_bands(self):
        # Same bands used by _attendance_v3_refresh_saved_fields after the punch.
        self.assertEqual(compute_closed_pa_status(180, 480), "A")
        self.assertEqual(compute_closed_pa_status(270, 480), "HP")
        self.assertEqual(compute_closed_pa_status(400, 480), "LD")
        self.assertEqual(compute_closed_pa_status(480, 480), "P")
