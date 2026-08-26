"""Unit tests for report email schedule helpers and has_data-before-generate flow."""

from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytz
from django.test import SimpleTestCase

from reports.models import LocationReportEmailItem as Item
from reports.services.schedule_utils import (
    build_schedule_key,
    matching_schedule_types,
    resolve_period,
    schedule_matches_today,
    send_time_matches,
    snap_send_time,
)


class ScheduleUtilsTests(SimpleTestCase):
    def test_send_time_matches_same_quarter_hour(self):
        tz = pytz.timezone("Asia/Kolkata")
        local = tz.localize(datetime(2026, 8, 25, 8, 7))
        self.assertTrue(send_time_matches(local, time(8, 0)))
        self.assertFalse(send_time_matches(local, time(9, 0)))
        # 11:37 snaps into the 11:30 slot
        local_1130 = tz.localize(datetime(2026, 8, 25, 11, 30))
        self.assertTrue(send_time_matches(local_1130, time(11, 37)))
        local_1145 = tz.localize(datetime(2026, 8, 25, 11, 45))
        self.assertFalse(send_time_matches(local_1145, time(11, 37)))

    def test_snap_send_time(self):
        self.assertEqual(snap_send_time(time(11, 37)), time(11, 30))
        self.assertEqual(snap_send_time(time(11, 45)), time(11, 45))
        self.assertEqual(snap_send_time(time(8, 0)), time(8, 0))

    def test_matching_schedule_types_multi(self):
        sunday = datetime(2026, 8, 23, 8, 0)
        matched = matching_schedule_types(
            [Item.SCHEDULE_DAILY, Item.SCHEDULE_WEEKLY_SUNDAY, Item.SCHEDULE_MONTHLY_START],
            sunday,
        )
        self.assertEqual(matched, [Item.SCHEDULE_DAILY, Item.SCHEDULE_WEEKLY_SUNDAY])

    def test_schedule_matches_daily_always(self):
        local = datetime(2026, 8, 25, 8, 0)  # Monday
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_DAILY, local))

    def test_schedule_matches_sunday_only(self):
        sunday = datetime(2026, 8, 23, 8, 0)  # Sunday
        monday = datetime(2026, 8, 24, 8, 0)
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_WEEKLY_SUNDAY, sunday))
        self.assertFalse(schedule_matches_today(Item.SCHEDULE_WEEKLY_SUNDAY, monday))

    def test_schedule_matches_month_start(self):
        first = datetime(2026, 9, 1, 8, 0)
        second = datetime(2026, 9, 2, 8, 0)
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_MONTHLY_START, first))
        self.assertFalse(schedule_matches_today(Item.SCHEDULE_MONTHLY_START, second))

    def test_resolve_period_previous_day(self):
        local = datetime(2026, 8, 25, 8, 0)
        period = resolve_period(Item.SCHEDULE_DAILY, Item.PERIOD_PREVIOUS_DAY, local)
        self.assertEqual(period["start_date"].isoformat(), "2026-08-24")
        self.assertEqual(period["end_date"].isoformat(), "2026-08-24")

    def test_resolve_period_weekly(self):
        sunday = datetime(2026, 8, 23, 8, 0)
        period = resolve_period(Item.SCHEDULE_WEEKLY_SUNDAY, Item.PERIOD_PREVIOUS_DAY, sunday)
        self.assertEqual(period["start_date"].isoformat(), "2026-08-16")
        self.assertEqual(period["end_date"].isoformat(), "2026-08-22")

    def test_resolve_period_monthly_previous_month(self):
        first = datetime(2026, 9, 1, 8, 0)
        period = resolve_period(Item.SCHEDULE_MONTHLY_START, Item.PERIOD_PREVIOUS_DAY, first)
        self.assertEqual(period["month_str"], "2026-08")
        self.assertEqual(period["start_date"].isoformat(), "2026-08-01")
        self.assertEqual(period["end_date"].isoformat(), "2026-08-31")

    def test_build_schedule_key(self):
        self.assertEqual(
            build_schedule_key(datetime(2026, 8, 25).date(), Item.SCHEDULE_DAILY),
            "2026-08-25-daily",
        )


class ReportGenerateHasDataGateTests(SimpleTestCase):
    """Ensure generate is not called when has_data is false."""

    def test_skip_generate_when_no_data(self):
        from reports.services import report_generator as rg

        has_fn = MagicMock(return_value=False)
        gen_fn = MagicMock(return_value=[{"filename": "x.xlsx"}])

        location = SimpleNamespace(id="loc-1", name="Org")
        period = {
            "start_date": datetime(2026, 8, 24).date(),
            "end_date": datetime(2026, 8, 24).date(),
            "label": "test",
            "month_str": None,
            "year": 2026,
            "month": 8,
        }
        with patch.dict(
            rg._HANDLERS,
            {Item.REPORT_CHECKIN: (has_fn, gen_fn)},
            clear=False,
        ):
            result = rg.report_generate(
                Item.REPORT_CHECKIN,
                location,
                period,
                user=SimpleNamespace(is_superuser=True, location=location),
                send_pdf=True,
                send_excel=False,
            )
        self.assertEqual(result, [])
        gen_fn.assert_not_called()
        has_fn.assert_called()

    def test_generate_when_data_exists(self):
        from reports.services import report_generator as rg

        has_fn = MagicMock(return_value=True)
        gen_fn = MagicMock(
            return_value=[
                {
                    "filename": "a.pdf",
                    "content": b"%PDF",
                    "mime": "application/pdf",
                    "format": "pdf",
                    "row_count": 3,
                    "display_name": "Check-In",
                }
            ]
        )
        location = SimpleNamespace(id="loc-1", name="Org")
        period = {
            "start_date": datetime(2026, 8, 24).date(),
            "end_date": datetime(2026, 8, 24).date(),
            "label": "test",
            "month_str": None,
            "year": 2026,
            "month": 8,
        }
        with patch.dict(
            rg._HANDLERS,
            {Item.REPORT_CHECKIN: (has_fn, gen_fn)},
            clear=False,
        ):
            result = rg.report_generate(
                Item.REPORT_CHECKIN,
                location,
                period,
                user=SimpleNamespace(is_superuser=True, location=location),
                send_pdf=True,
                send_excel=False,
            )
        self.assertEqual(len(result), 1)
        gen_fn.assert_called_once()
