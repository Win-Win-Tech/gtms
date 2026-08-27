"""Unit tests for report email schedule helpers and has_data-before-generate flow."""

from datetime import datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytz
from django.test import SimpleTestCase

from reports.models import LocationReportEmailItem as Item
from reports.services.schedule_utils import (
    build_schedule_key,
    day_of_month_matches,
    matching_schedule_types,
    resolve_period,
    schedule_matches_today,
    send_time_matches,
    snap_send_time,
)


def _item(**kwargs):
    defaults = {
        "weekly_weekday": 6,
        "weekly_include_current_day": False,
        "monthly_send_days": [1],
        "last_n_days_count": 7,
        "last_n_days_send_days": [7, 14, 21, 28],
        "last_n_days_include_current_day": False,
        "daily_period": Item.PERIOD_PREVIOUS_DAY,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


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
        item = _item(weekly_weekday=6, monthly_send_days=[1])
        matched = matching_schedule_types(
            [Item.SCHEDULE_DAILY, Item.SCHEDULE_WEEKLY, Item.SCHEDULE_MONTHLY],
            sunday,
            item,
        )
        self.assertEqual(matched, [Item.SCHEDULE_DAILY, Item.SCHEDULE_WEEKLY])

    def test_matching_legacy_aliases(self):
        sunday = datetime(2026, 8, 23, 8, 0)
        matched = matching_schedule_types(
            [Item.SCHEDULE_WEEKLY_SUNDAY],
            sunday,
            _item(weekly_weekday=6),
        )
        self.assertEqual(matched, [Item.SCHEDULE_WEEKLY])

    def test_schedule_matches_daily_always(self):
        local = datetime(2026, 8, 24, 8, 0)  # Monday
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_DAILY, local))

    def test_schedule_matches_weekly_weekday(self):
        monday = datetime(2026, 8, 24, 8, 0)  # Monday
        sunday = datetime(2026, 8, 23, 8, 0)  # Sunday
        self.assertTrue(
            schedule_matches_today(Item.SCHEDULE_WEEKLY, monday, _item(weekly_weekday=0))
        )
        self.assertFalse(
            schedule_matches_today(Item.SCHEDULE_WEEKLY, sunday, _item(weekly_weekday=0))
        )
        self.assertTrue(
            schedule_matches_today(Item.SCHEDULE_WEEKLY, sunday, _item(weekly_weekday=6))
        )

    def test_schedule_matches_monthly_send_days(self):
        day15 = datetime(2026, 9, 15, 8, 0)
        day1 = datetime(2026, 9, 1, 8, 0)
        item = _item(monthly_send_days=[1, 15])
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_MONTHLY, day15, item))
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_MONTHLY, day1, item))
        self.assertFalse(
            schedule_matches_today(
                Item.SCHEDULE_MONTHLY, datetime(2026, 9, 2, 8, 0), item
            )
        )

    def test_schedule_matches_last_n_send_days(self):
        day14 = datetime(2026, 9, 14, 8, 0)
        item = _item(last_n_days_send_days=[7, 14, 21, 28])
        self.assertTrue(schedule_matches_today(Item.SCHEDULE_LAST_N_DAYS, day14, item))
        self.assertFalse(
            schedule_matches_today(
                Item.SCHEDULE_LAST_N_DAYS, datetime(2026, 9, 15, 8, 0), item
            )
        )

    def test_feb_clamp_day_of_month(self):
        feb28 = datetime(2026, 2, 28).date()  # non-leap
        self.assertTrue(day_of_month_matches(feb28, [28]))
        self.assertTrue(day_of_month_matches(feb28, [30]))  # clamped to 28
        self.assertTrue(day_of_month_matches(feb28, [31]))  # clamped to 28
        self.assertFalse(day_of_month_matches(feb28, [15]))
        # April has 30 days — day 31 clamps to 30
        self.assertTrue(day_of_month_matches(datetime(2026, 4, 30).date(), [31]))
        self.assertFalse(day_of_month_matches(datetime(2026, 4, 29).date(), [31]))
        self.assertTrue(
            schedule_matches_today(
                Item.SCHEDULE_LAST_N_DAYS,
                datetime(2026, 2, 28, 8, 0),
                _item(last_n_days_send_days=[28]),
            )
        )

    def test_resolve_period_previous_day(self):
        local = datetime(2026, 8, 25, 8, 0)
        period = resolve_period(Item.SCHEDULE_DAILY, Item.PERIOD_PREVIOUS_DAY, local)
        self.assertEqual(period["start_date"].isoformat(), "2026-08-24")
        self.assertEqual(period["end_date"].isoformat(), "2026-08-24")

    def test_resolve_period_weekly_exclude_include(self):
        sunday = datetime(2026, 8, 23, 8, 0)
        exclude = resolve_period(
            Item.SCHEDULE_WEEKLY,
            Item.PERIOD_PREVIOUS_DAY,
            sunday,
            _item(weekly_include_current_day=False),
        )
        self.assertEqual(exclude["start_date"].isoformat(), "2026-08-16")
        self.assertEqual(exclude["end_date"].isoformat(), "2026-08-22")

        include = resolve_period(
            Item.SCHEDULE_WEEKLY,
            Item.PERIOD_PREVIOUS_DAY,
            sunday,
            _item(weekly_include_current_day=True),
        )
        self.assertEqual(include["start_date"].isoformat(), "2026-08-17")
        self.assertEqual(include["end_date"].isoformat(), "2026-08-23")

    def test_resolve_period_monthly_previous_month_mid_month(self):
        mid = datetime(2026, 9, 15, 8, 0)
        period = resolve_period(Item.SCHEDULE_MONTHLY, Item.PERIOD_PREVIOUS_DAY, mid)
        self.assertEqual(period["month_str"], "2026-08")
        self.assertEqual(period["start_date"].isoformat(), "2026-08-01")
        self.assertEqual(period["end_date"].isoformat(), "2026-08-31")

    def test_resolve_period_last_n_days(self):
        day14 = datetime(2026, 9, 14, 8, 0)
        exclude = resolve_period(
            Item.SCHEDULE_LAST_N_DAYS,
            Item.PERIOD_PREVIOUS_DAY,
            day14,
            _item(last_n_days_count=7, last_n_days_include_current_day=False),
        )
        # 7 days ending yesterday → Sep 7–13
        self.assertEqual(exclude["start_date"].isoformat(), "2026-09-07")
        self.assertEqual(exclude["end_date"].isoformat(), "2026-09-13")

        include = resolve_period(
            Item.SCHEDULE_LAST_N_DAYS,
            Item.PERIOD_PREVIOUS_DAY,
            day14,
            _item(last_n_days_count=7, last_n_days_include_current_day=True),
        )
        self.assertEqual(include["start_date"].isoformat(), "2026-09-08")
        self.assertEqual(include["end_date"].isoformat(), "2026-09-14")

    def test_build_schedule_key(self):
        self.assertEqual(
            build_schedule_key(datetime(2026, 8, 25).date(), Item.SCHEDULE_DAILY),
            "2026-08-25-daily",
        )
        self.assertEqual(
            build_schedule_key(datetime(2026, 8, 25).date(), Item.SCHEDULE_WEEKLY_SUNDAY),
            "2026-08-25-weekly",
        )


class DomListValidationTests(SimpleTestCase):
    def test_empty_send_days_rejected_when_monthly_selected(self):
        from reports.serializers import LocationReportEmailItemSerializer

        ser = LocationReportEmailItemSerializer(
            data={
                "report_code": Item.REPORT_CHECKIN,
                "is_enabled": False,
                "schedule_types": [Item.SCHEDULE_MONTHLY],
                "monthly_send_days": [],
                "send_pdf": True,
                "send_excel": False,
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("monthly_send_days", ser.errors)

    def test_empty_last_n_send_days_rejected(self):
        from reports.serializers import LocationReportEmailItemSerializer

        ser = LocationReportEmailItemSerializer(
            data={
                "report_code": Item.REPORT_CHECKIN,
                "is_enabled": False,
                "schedule_types": [Item.SCHEDULE_LAST_N_DAYS],
                "last_n_days_send_days": [],
                "last_n_days_count": 7,
                "send_pdf": True,
                "send_excel": False,
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("last_n_days_send_days", ser.errors)

    def test_send_days_above_31_rejected(self):
        from reports.serializers import LocationReportEmailItemSerializer

        ser = LocationReportEmailItemSerializer(
            data={
                "report_code": Item.REPORT_CHECKIN,
                "is_enabled": False,
                "schedule_types": [Item.SCHEDULE_MONTHLY],
                "monthly_send_days": [1, 32],
                "send_pdf": True,
                "send_excel": False,
            }
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("monthly_send_days", ser.errors)

    def test_send_days_29_30_31_accepted(self):
        from reports.serializers import LocationReportEmailItemSerializer

        ser = LocationReportEmailItemSerializer(
            data={
                "report_code": Item.REPORT_CHECKIN,
                "is_enabled": False,
                "schedule_types": [Item.SCHEDULE_MONTHLY],
                "monthly_send_days": [1, 29, 30, 31],
                "send_pdf": True,
                "send_excel": False,
            }
        )
        self.assertTrue(ser.is_valid(), ser.errors)


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
                    "display_name": "QR Scan Patrol",
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
