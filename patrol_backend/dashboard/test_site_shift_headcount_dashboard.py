from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from dashboard.views import AttendanceCheckinViewSet
from dashboard.views_v5 import AttendanceCheckinViewSetV5
from patrol_backend.utils.site_shift_headcount import SiteShiftRoleCapExceeded


def _request(payload, user=None, enforce_headcount=False):
    request = MagicMock()
    request.data = payload
    request.user = user or MagicMock(id="actor-1")
    request._gtms_enforce_headcount = enforce_headcount
    return request


def _cap_exc(*, role="guard", current=10, max_count=10):
    return SiteShiftRoleCapExceeded(
        role=role,
        current=current,
        max_count=max_count,
        site_id="site-1",
        shift_id="shift-1",
        on_date=date(2026, 1, 11),
    )


class BulkEntryHeadcountTests(SimpleTestCase):
    def _common_shift(self):
        shift = MagicMock()
        shift.id = "shift-1"
        shift.location_id = "loc-1"
        shift.location.latitude = 0
        shift.location.longitude = 0
        shift.start_time = time(9, 0)
        shift.end_time = time(18, 0)
        return shift

    def test_legacy_bulk_entry_skips_headcount_gate(self):
        view = AttendanceCheckinViewSet()
        guard = MagicMock(id="guard-1", role="guard")
        request = _request(
            {
                "date": "2026-01-11",
                "user_ids": ["guard-1"],
                "shift_id": "shift-1",
                "checkin_time": "2026-01-11 09:00:00",
                "reason": "bulk attendance test",
                "site_id": "site-1",
            },
            enforce_headcount=False,
        )
        shift = self._common_shift()

        with patch.object(view, "_resolve_scope_location_id", return_value=("loc-1", None)), patch(
            "dashboard.views.Shift.objects"
        ) as shift_objects, patch.object(
            view, "_parse_bulk_datetime_to_utc", return_value=datetime(2026, 1, 11, 3, 30)
        ), patch(
            "dashboard.views.get_user_timezone_from_request", return_value="Asia/Kolkata"
        ), patch(
            "dashboard.views.to_user_timezone", return_value=datetime(2026, 1, 11, 9, 0)
        ), patch(
            "dashboard.views.User.objects"
        ) as user_objects, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(False, 6, 208, False),
        ), patch(
            "patrol_backend.utils.consecutive_work.attendance_write_block_payload",
            return_value=None,
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as assert_fn, patch(
            "dashboard.views.transaction.atomic",
            side_effect=RuntimeError("stop after gate"),
        ):
            shift_objects.select_related.return_value.get.return_value = shift
            user_objects.filter.return_value.exclude.return_value = [guard]
            response = view.bulk_entry(request)

        assert_fn.assert_not_called()
        self.assertNotEqual(
            (response.data.get("failed") or [{}])[0].get("code"),
            "site_shift_role_cap_exceeded",
        )

    def test_v5_flag_blocks_over_cap_as_failed_row(self):
        view = AttendanceCheckinViewSet()
        blocked = MagicMock(id="guard-blocked", role="guard")
        request = _request(
            {
                "date": "2026-01-11",
                "user_ids": ["guard-blocked", "missing"],
                "shift_id": "shift-1",
                "checkin_time": "2026-01-11 09:00:00",
                "reason": "bulk attendance test",
                "site_id": "site-1",
            },
            enforce_headcount=True,
        )
        shift = self._common_shift()

        with patch.object(view, "_resolve_scope_location_id", return_value=("loc-1", None)), patch(
            "dashboard.views.Shift.objects"
        ) as shift_objects, patch.object(
            view, "_parse_bulk_datetime_to_utc", return_value=datetime(2026, 1, 11, 3, 30)
        ), patch(
            "dashboard.views.get_user_timezone_from_request", return_value="Asia/Kolkata"
        ), patch(
            "dashboard.views.to_user_timezone", return_value=datetime(2026, 1, 11, 9, 0)
        ), patch(
            "dashboard.views.User.objects"
        ) as user_objects, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(False, 6, 208, False),
        ), patch(
            "patrol_backend.utils.consecutive_work.attendance_write_block_payload",
            return_value=None,
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
            side_effect=_cap_exc(),
        ), patch(
            "dashboard.views.transaction.atomic",
            side_effect=AssertionError("over-cap row must not create attendance"),
        ):
            shift_objects.select_related.return_value.get.return_value = shift
            user_objects.filter.return_value.exclude.return_value = [blocked]
            response = view.bulk_entry(request)

        self.assertEqual(response.status_code, 207)
        failed_by_user = {row["user_id"]: row for row in response.data["failed"]}
        self.assertEqual(failed_by_user["guard-blocked"]["code"], "site_shift_role_cap_exceeded")
        self.assertEqual(failed_by_user["guard-blocked"]["role"], "guard")
        self.assertEqual(failed_by_user["guard-blocked"]["current"], 10)
        self.assertEqual(failed_by_user["guard-blocked"]["max"], 10)
        self.assertIn("missing", failed_by_user)
        self.assertEqual(response.data["summary"]["failed_count"], 2)

    def test_bulk_entry_v5_sets_enforce_flag(self):
        view = AttendanceCheckinViewSetV5()
        live = MagicMock()
        live.bulk_entry.return_value = MagicMock(status_code=200)

        def _capture(request):
            self.assertTrue(getattr(request, "_gtms_enforce_headcount", False))
            return live.bulk_entry.return_value

        live.bulk_entry.side_effect = _capture
        request = _request({"site_id": "site-1"}, enforce_headcount=False)

        with patch.object(view, "_live", return_value=live), patch(
            "dashboard.views_v5.assert_site_in_scope"
        ):
            response = view.bulk_entry_v5(request)

        self.assertEqual(response.status_code, 200)
        live.bulk_entry.assert_called_once()


class MonthlyCellHeadcountTests(SimpleTestCase):
    def test_mark_present_raises_when_cap_exceeded(self):
        view = AttendanceCheckinViewSet()
        request = SimpleNamespace(user=MagicMock(id="actor-1"), _gtms_enforce_headcount=True)
        guard = MagicMock(id="guard-1", role="guard")
        assignment = MagicMock()
        assignment.shift = MagicMock(id="shift-1")
        assignment.location = MagicMock()
        exc = _cap_exc()

        with patch(
            "dashboard.views.get_user_timezone_from_request", return_value="Asia/Kolkata"
        ), patch(
            "dashboard.views.get_user_today", return_value=date(2026, 1, 11)
        ), patch.object(
            view, "_monthly_resolve_assignment_for_date", return_value=assignment
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_monthly_hours_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.shift_length_minutes",
            return_value=480,
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
            side_effect=exc,
        ) as assert_fn, patch(
            "scheduler.daily_site.fill_daily_sites",
        ) as fill_fn:
            with self.assertRaises(SiteShiftRoleCapExceeded):
                view._monthly_cell_mark_present(
                    request,
                    guard,
                    "loc-1",
                    date(2026, 1, 11),
                    "monthly test",
                    site_id="site-1",
                    cw_enabled=False,
                )
            assert_fn.assert_called_once()
            fill_fn.assert_not_called()

    def test_legacy_mark_present_skips_headcount(self):
        view = AttendanceCheckinViewSet()
        request = SimpleNamespace(user=MagicMock(id="actor-1"), _gtms_enforce_headcount=False)
        guard = MagicMock(id="guard-1", role="guard")
        assignment = MagicMock()
        assignment.shift = MagicMock(id="shift-1", location_id="loc-1")
        assignment.location = MagicMock(id="loc-1", latitude=0, longitude=0)

        with patch(
            "dashboard.views.get_user_timezone_from_request", return_value="Asia/Kolkata"
        ), patch(
            "dashboard.views.get_user_today", return_value=date(2026, 1, 11)
        ), patch.object(
            view, "_monthly_resolve_assignment_for_date", return_value=assignment
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_monthly_hours_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.shift_length_minutes",
            return_value=480,
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as assert_fn, patch(
            "dashboard.views._attendance_v3_shift_window_utc",
            side_effect=RuntimeError("stop after headcount gate"),
        ):
            with self.assertRaises(RuntimeError):
                view._monthly_cell_mark_present(
                    request,
                    guard,
                    "loc-1",
                    date(2026, 1, 11),
                    "monthly test",
                    site_id="site-1",
                    cw_enabled=False,
                )
            assert_fn.assert_not_called()

    def test_monthly_cell_action_failed_row_has_cap_code(self):
        view = AttendanceCheckinViewSet()
        request = _request(
            {
                "action": "mark_present",
                "reason": "monthly test",
                "site_id": "site-1",
                "cells": [
                    {"user_id": "guard-1", "date": "2026-01-11", "from_status": "A"},
                    {"user_id": "guard-2", "date": "2026-01-11", "from_status": "A"},
                ],
            },
            enforce_headcount=True,
        )
        blocked = _cap_exc()
        guard1 = MagicMock(id="guard-1")
        guard2 = MagicMock(id="guard-2")

        def _mark_present(_request, guard, *_args, **_kwargs):
            if str(guard.id) == "guard-1":
                raise blocked
            return {"user_id": str(guard.id), "date": "2026-01-11", "status": "P"}

        with patch.object(view, "_resolve_scope_location_id", return_value=("loc-1", None)), patch(
            "dashboard.views.User.objects"
        ) as user_objects, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(False, 6, 208, False),
        ), patch.object(
            view, "_monthly_cell_mark_present", side_effect=_mark_present
        ):
            user_objects.filter.return_value.exclude.return_value.first.side_effect = [
                guard1,
                guard2,
            ]
            response = view.monthly_cell_action(request)

        self.assertEqual(response.status_code, 207)
        self.assertEqual(response.data["summary"]["success_count"], 1)
        self.assertEqual(response.data["summary"]["failed_count"], 1)
        failed = response.data["failed"][0]
        self.assertEqual(failed["code"], "site_shift_role_cap_exceeded")
        self.assertEqual(failed["user_id"], "guard-1")
        self.assertEqual(failed["role"], "guard")
        self.assertEqual(failed["current"], 10)
        self.assertEqual(failed["max"], 10)

    def test_monthly_cell_action_v5_sets_enforce_flag(self):
        view = AttendanceCheckinViewSetV5()
        live = MagicMock()
        live.monthly_cell_action.return_value = MagicMock(status_code=200)

        def _capture(request):
            self.assertTrue(getattr(request, "_gtms_enforce_headcount", False))
            return live.monthly_cell_action.return_value

        live.monthly_cell_action.side_effect = _capture
        request = _request({"site_id": "site-1"}, enforce_headcount=False)

        with patch.object(view, "_live", return_value=live), patch(
            "dashboard.views_v5.assert_site_in_scope"
        ):
            response = view.monthly_cell_action_v5(request)

        self.assertEqual(response.status_code, 200)
        live.monthly_cell_action.assert_called_once()
