from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from dashboard.views import AttendanceCheckinViewSet
from patrol_backend.utils.consecutive_work import ConsecutiveWorkLimitExceeded


def _request(payload, user=None):
    request = MagicMock()
    request.data = payload
    request.user = user or MagicMock(id="actor-1")
    return request


class BulkEntryConsecutiveWorkTests(SimpleTestCase):
    def test_blocked_guard_is_failed_row_and_batch_continues(self):
        view = AttendanceCheckinViewSet()
        blocked = MagicMock(id="guard-blocked")
        request = _request(
            {
                "date": "2026-01-11",
                "user_ids": ["guard-blocked", "missing"],
                "shift_id": "shift-1",
                "checkin_time": "2026-01-11 09:00:00",
                "reason": "bulk attendance test",
                "site_id": "site-1",
            }
        )
        shift = MagicMock()
        shift.location_id = "loc-1"
        shift.location.latitude = 0
        shift.location.longitude = 0
        shift.start_time = time(9, 0)
        shift.end_time = time(18, 0)

        checkin_local = datetime(2026, 1, 11, 9, 0)

        def _block_payload(guard, target_date, **kwargs):
            if str(guard.id) == "guard-blocked":
                return {
                    "code": "consecutive_work_limit",
                    "error": "Maximum 6 consecutive working days reached.",
                    "consecutive_present_days": 6,
                    "max_consecutive_work_days": 6,
                    "blocked_date": "2026-01-11",
                }
            return None

        with patch.object(view, "_resolve_scope_location_id", return_value=("loc-1", None)), patch(
            "dashboard.views.Shift.objects"
        ) as shift_objects, patch.object(
            view, "_parse_bulk_datetime_to_utc", return_value=datetime(2026, 1, 11, 3, 30)
        ), patch(
            "dashboard.views.get_user_timezone_from_request", return_value="Asia/Kolkata"
        ), patch(
            "dashboard.views.to_user_timezone", return_value=checkin_local
        ), patch(
            "dashboard.views.User.objects"
        ) as user_objects, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(True, 6, 208, True),
        ), patch(
            "patrol_backend.utils.consecutive_work.attendance_write_block_payload",
            side_effect=_block_payload,
        ), patch(
            "dashboard.views.transaction.atomic"
        ) as atomic:
            shift_objects.select_related.return_value.get.return_value = shift
            user_objects.filter.return_value.exclude.return_value = [blocked]
            atomic.side_effect = AssertionError("blocked/missing rows must not create attendance")

            response = view.bulk_entry(request)

        self.assertEqual(response.status_code, 207)
        failed_by_user = {row["user_id"]: row for row in response.data["failed"]}
        self.assertEqual(failed_by_user["guard-blocked"]["code"], "consecutive_work_limit")
        self.assertEqual(failed_by_user["guard-blocked"]["blocked_date"], "2026-01-11")
        self.assertIn("missing", failed_by_user)
        self.assertEqual(response.data["summary"]["failed_count"], 2)

    def test_disabled_setting_does_not_block_bulk_row(self):
        view = AttendanceCheckinViewSet()
        guard = MagicMock(id="guard-1")
        request = _request(
            {
                "date": "2026-01-11",
                "user_ids": ["guard-1"],
                "shift_id": "shift-1",
                "checkin_time": "2026-01-11 09:00:00",
                "reason": "bulk attendance test",
            }
        )
        shift = MagicMock()
        shift.location_id = "loc-1"
        shift.location.latitude = 0
        shift.location.longitude = 0
        shift.start_time = time(9, 0)
        shift.end_time = time(18, 0)

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
        ) as block_fn, patch(
            "dashboard.views.transaction.atomic"
        ) as atomic:
            shift_objects.select_related.return_value.get.return_value = shift
            user_objects.filter.return_value.exclude.return_value = [guard]
            atomic.side_effect = RuntimeError("stop after gate; create path is out of scope")
            response = view.bulk_entry(request)
            block_fn.assert_called_once()
            self.assertFalse(block_fn.call_args.kwargs["enabled"])
            self.assertNotEqual(response.data["failed"][0].get("code"), "consecutive_work_limit")


class MonthlyAttendanceConsecutiveWorkTests(SimpleTestCase):
    def test_mark_present_raises_before_creating_logs(self):
        view = AttendanceCheckinViewSet()
        request = SimpleNamespace(user=MagicMock(id="actor-1"))
        guard = MagicMock(id="guard-1")
        assignment = MagicMock()
        assignment.shift = MagicMock()
        assignment.location = MagicMock()
        exc = ConsecutiveWorkLimitExceeded(
            consecutive=6,
            max_days=6,
            blocked_date=date(2026, 1, 11),
        )
        with patch(
            "dashboard.views.get_user_timezone_from_request", return_value="Asia/Kolkata"
        ), patch(
            "dashboard.views.get_user_today", return_value=date(2026, 1, 11)
        ), patch.object(
            view, "_monthly_resolve_assignment_for_date", return_value=assignment
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
            side_effect=exc,
        ) as assert_fn:
            with self.assertRaises(ConsecutiveWorkLimitExceeded):
                view._monthly_cell_mark_present(
                    request,
                    guard,
                    "loc-1",
                    date(2026, 1, 11),
                    "monthly test",
                    site_id="site-1",
                    cw_enabled=True,
                    cw_max_days=6,
                )
            assert_fn.assert_called_once()
            kwargs = assert_fn.call_args.kwargs
            self.assertEqual(kwargs["site_id"], "site-1")
            self.assertTrue(kwargs["enabled"])
            self.assertEqual(kwargs["max_days"], 6)

    def test_monthly_cell_action_failed_row_has_code(self):
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
            }
        )
        blocked = ConsecutiveWorkLimitExceeded(
            consecutive=6,
            max_days=6,
            blocked_date=date(2026, 1, 11),
        )
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
            return_value=(True, 6, 208, True),
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
        self.assertEqual(failed["code"], "consecutive_work_limit")
        self.assertEqual(failed["user_id"], "guard-1")
        self.assertEqual(failed["blocked_date"], "2026-01-11")

    def test_weekoff_and_empty_skip_consecutive_work_gate(self):
        view = AttendanceCheckinViewSet()
        request = _request(
            {
                "action": "mark_weekoff",
                "reason": "weekly rest",
                "cells": [{"user_id": "guard-1", "date": "2026-01-11", "from_status": "P"}],
            }
        )
        with patch.object(view, "_resolve_scope_location_id", return_value=("loc-1", None)), patch(
            "dashboard.views.User.objects"
        ) as user_objects, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(True, 6, 208, True),
        ), patch.object(
            view, "_monthly_cell_mark_present"
        ) as mark_present, patch.object(
            view,
            "_monthly_cell_mark_weekoff",
            return_value={"user_id": "guard-1", "status": "W"},
        ):
            user_objects.filter.return_value.exclude.return_value.first.return_value = MagicMock(
                id="guard-1"
            )
            response = view.monthly_cell_action(request)

        self.assertEqual(response.status_code, 200)
        mark_present.assert_not_called()
        self.assertEqual(response.data["summary"]["success_count"], 1)
        self.assertEqual(response.data["summary"]["failed_count"], 0)
