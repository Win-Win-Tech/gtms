from datetime import date, datetime
from types import SimpleNamespace

from django.test import SimpleTestCase

from dashboard.public_holiday import attendance_counts_as_worked, parse_holiday_date


class AttendanceWorkedDetectionTests(SimpleTestCase):
    def test_checkin_counts_as_worked(self):
        att = SimpleNamespace(last_checkin_time=datetime(2026, 1, 26, 9, 0), checkin_time=None, duration_minutes=0)
        self.assertTrue(attendance_counts_as_worked(att))

    def test_duration_without_checkin_counts(self):
        att = SimpleNamespace(last_checkin_time=None, checkin_time=None, duration_minutes=30)
        self.assertTrue(attendance_counts_as_worked(att))

    def test_empty_day_does_not_count(self):
        att = SimpleNamespace(last_checkin_time=None, checkin_time=None, duration_minutes=0)
        self.assertFalse(attendance_counts_as_worked(att))
        self.assertFalse(attendance_counts_as_worked(None))


class ParseHolidayDateTests(SimpleTestCase):
    def test_iso_and_indian_formats(self):
        self.assertEqual(parse_holiday_date("2026-01-26"), date(2026, 1, 26))
        self.assertEqual(parse_holiday_date("26-01-2026"), date(2026, 1, 26))
        self.assertEqual(parse_holiday_date("26/01/2026"), date(2026, 1, 26))

    def test_date_and_datetime_passthrough(self):
        self.assertEqual(parse_holiday_date(date(2026, 8, 15)), date(2026, 8, 15))
        self.assertEqual(parse_holiday_date(datetime(2026, 8, 15, 10, 0)), date(2026, 8, 15))

    def test_empty_is_none(self):
        self.assertIsNone(parse_holiday_date(None))
        self.assertIsNone(parse_holiday_date(""))
        self.assertIsNone(parse_holiday_date("not-a-date"))
