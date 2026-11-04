"""
Phase 6 regression: every gated endpoint + prove unset caps add no COUNT work.
"""
from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import serializers, status

from dashboard.views import AttendanceCheckinViewSet
from dashboard.views_v5 import AttendanceCheckinViewSetV5, CreateAssignmentV5
from patrol_backend.utils.site_shift_headcount import (
    SiteShiftRoleCapExceeded,
    assert_headcount_allowed,
    get_caps_cached,
    invalidate_caps_cache,
)
from scheduler.serializers_v5 import AssignmentSerializerV5


def _cap_exc(*, role="guard", current=10, max_count=10, on_date=None):
    return SiteShiftRoleCapExceeded(
        role=role,
        current=current,
        max_count=max_count,
        site_id="site-1",
        shift_id="shift-1",
        on_date=on_date or date(2026, 11, 3),
    )


def _request(payload=None, *, enforce_headcount=False, user=None):
    request = MagicMock()
    request.data = payload or {}
    request.user = user or MagicMock(id="actor-1", role="guard")
    request.FILES = MagicMock()
    request.FILES.get.return_value = None
    request._gtms_enforce_headcount = enforce_headcount
    return request


# ---------------------------------------------------------------------------
# Phase 2 helper edge cases not covered elsewhere
# ---------------------------------------------------------------------------
class HelperRegressionTests(SimpleTestCase):
    def setUp(self):
        invalidate_caps_cache()

    def tearDown(self):
        invalidate_caps_cache()

    def test_missing_inputs_skip_without_db(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
        ) as caps_fn, patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
        ) as count_fn:
            info = assert_headcount_allowed(
                site_id=None,
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="guard",
            )
        self.assertEqual(info["headcount_skipped"], "missing_input")
        caps_fn.assert_not_called()
        count_fn.assert_not_called()

    def test_empty_role_skips(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
        ) as caps_fn:
            info = assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="  ",
            )
        self.assertEqual(info["headcount_skipped"], "missing_input")
        caps_fn.assert_not_called()

    def test_exclude_guard_keeps_self_under_full_cap(self):
        """Already counted guard can still punch/assign when site is at max including self."""
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={"guard": 10},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
            return_value=9,  # others only (self excluded)
        ) as count_fn:
            info = assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="Guard",
                extra=1,
                exclude_guard_id="g-self",
            )
        self.assertTrue(info["headcount_checked"])
        self.assertEqual(count_fn.call_args.kwargs["exclude_guard_id"], "g-self")

    def test_no_caps_negative_cache_second_assert_skips_count(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount._load_caps_from_db",
            return_value={},
        ) as load_caps, patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
        ) as count_fn:
            assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="guard",
            )
            assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="guard",
            )
        load_caps.assert_called_once()
        count_fn.assert_not_called()
        self.assertEqual(get_caps_cached("site-1", "shift-1"), {})

    def test_exact_capacity_blocks_extra(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={"so": 5},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
            return_value=5,
        ):
            with self.assertRaises(SiteShiftRoleCapExceeded) as ctx:
                assert_headcount_allowed(
                    site_id="site-1",
                    shift_id="shift-1",
                    on_date=date(2026, 11, 3),
                    role="so",
                    extra=1,
                )
        self.assertEqual(ctx.exception.as_dict()["role"], "so")


# ---------------------------------------------------------------------------
# Phase 3 — assign / create_assignment_v5
# ---------------------------------------------------------------------------
class AssignV5RegressionTests(SimpleTestCase):
    def test_serializer_allows_under_cap(self):
        ser = AssignmentSerializerV5()
        guard = SimpleNamespace(id="g1", role="guard")
        shift = SimpleNamespace(id="sh1", location_id="loc1")
        data = {
            "guard": guard,
            "shift": shift,
            "start_date": date(2026, 11, 3),
            "end_date": date(2026, 11, 3),
            "location": None,
            "site_id": "site-1",
        }
        with patch.object(AssignmentSerializerV5.__bases__[0], "validate", return_value=data), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_range",
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_range",
            return_value={"headcount_checked": True},
        ) as head_fn:
            out = ser.validate(dict(data))
        self.assertEqual(out["site_id"], "site-1")
        head_fn.assert_called_once()
        self.assertEqual(head_fn.call_args.kwargs["exclude_guard_id"], "g1")

    def test_create_assignment_v5_blocks_over_cap(self):
        view = CreateAssignmentV5()
        request = _request(
            {"site_id": "site-1", "shift_id": "shift-1", "guard_id": "g1"},
            user=MagicMock(id="g1", role="guard"),
        )
        site = MagicMock(id="site-1", name="Site A")
        shift = MagicMock(id="shift-1", location_id="loc-1")
        guard = MagicMock(id="g1", role="guard")

        with patch(
            "dashboard.views_v5.get_site_or_error", return_value=site
        ), patch(
            "dashboard.views_v5.assert_caller_can_access_site",
        ), patch(
            "authapp.models.User.objects",
        ) as user_objects, patch(
            "scheduler.models.Shift.objects",
        ) as shift_objects, patch(
            "dashboard.views_v5.get_user_timezone_from_request",
            return_value="Asia/Kolkata",
        ), patch(
            "dashboard.views_v5.get_user_today",
            return_value=date(2026, 11, 3),
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_range",
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
            side_effect=_cap_exc(),
        ) as hc_fn, patch(
            "dashboard.views.AttendanceCheckinViewSet.create_assignment",
        ) as create_fn:
            user_objects.filter.return_value.first.return_value = guard
            shift_objects.filter.return_value.only.return_value.first.return_value = shift
            response = view.post(request)

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("code"), "site_shift_role_cap_exceeded")
        hc_fn.assert_called_once()
        create_fn.assert_not_called()

    def test_create_assignment_v5_skips_headcount_without_site(self):
        view = CreateAssignmentV5()
        request = _request({"shift_id": "shift-1"}, user=MagicMock(id="g1", role="guard"))
        shift = MagicMock(id="shift-1", location_id="loc-1")

        with patch(
            "scheduler.models.Shift.objects",
        ) as shift_objects, patch(
            "dashboard.views_v5.get_user_timezone_from_request",
            return_value="Asia/Kolkata",
        ), patch(
            "dashboard.views_v5.get_user_today",
            return_value=date(2026, 11, 3),
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_range",
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as hc_fn, patch(
            "dashboard.views.AttendanceCheckinViewSet.create_assignment",
            return_value=MagicMock(status_code=201, data={"assignment_id": "a1"}),
        ):
            shift_objects.filter.return_value.only.return_value.first.return_value = shift
            response = view.post(request)

        hc_fn.assert_not_called()
        self.assertEqual(response.status_code, 201)


# ---------------------------------------------------------------------------
# Phase 4 — legacy vs v5 admin attendance
# ---------------------------------------------------------------------------
class AdminAttendanceRegressionTests(SimpleTestCase):
    def _bulk_shift(self):
        shift = MagicMock()
        shift.id = "shift-1"
        shift.location_id = "loc-1"
        shift.location.latitude = 0
        shift.location.longitude = 0
        shift.start_time = time(9, 0)
        shift.end_time = time(18, 0)
        return shift

    def test_legacy_bulk_entry_never_calls_fill_daily_sites(self):
        view = AttendanceCheckinViewSet()
        guard = MagicMock(id="guard-1", role="guard")
        request = _request(
            {
                "date": "2026-01-11",
                "user_ids": ["guard-1"],
                "shift_id": "shift-1",
                "checkin_time": "2026-01-11 09:00:00",
                "reason": "legacy bulk",
                "site_id": "site-1",
            },
            enforce_headcount=False,
        )
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
        ) as hc_fn, patch(
            "scheduler.daily_site.fill_daily_sites",
        ) as fill_fn, patch(
            "dashboard.views.transaction.atomic",
            side_effect=RuntimeError("stop after gate"),
        ):
            shift_objects.select_related.return_value.get.return_value = self._bulk_shift()
            user_objects.filter.return_value.exclude.return_value = [guard]
            view.bulk_entry(request)
        hc_fn.assert_not_called()
        fill_fn.assert_not_called()

    def test_v5_bulk_under_cap_calls_assert_then_fill(self):
        view = AttendanceCheckinViewSet()
        guard = MagicMock(id="guard-1", role="guard")
        request = _request(
            {
                "date": "2026-01-11",
                "user_ids": ["guard-1"],
                "shift_id": "shift-1",
                "checkin_time": "2026-01-11 09:00:00",
                "reason": "v5 bulk",
                "site_id": "site-1",
            },
            enforce_headcount=True,
        )
        assignment = MagicMock()
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
            return_value={"headcount_checked": True},
        ) as hc_fn, patch(
            "scheduler.daily_site.fill_daily_sites",
        ) as fill_fn, patch(
            "dashboard.views.Assignment.objects",
        ) as assignment_objects, patch(
            "dashboard.views.AttendanceWeekOff.objects",
        ), patch(
            "dashboard.views.CheckInLog.objects",
        ), patch(
            "dashboard.views.AttendanceCheckin.objects",
        ), patch(
            "dashboard.views.transaction.atomic",
        ):
            shift_objects.select_related.return_value.get.return_value = self._bulk_shift()
            user_objects.filter.return_value.exclude.return_value = [guard]
            assignment_objects.filter.return_value.order_by.return_value.first.return_value = assignment
            # Stop after fill by raising inside later code — fill should already be called
            fill_fn.side_effect = RuntimeError("filled")
            response = view.bulk_entry(request)

        hc_fn.assert_called_once()
        fill_fn.assert_called_once()
        self.assertEqual(response.data["summary"]["failed_count"], 1)

    def test_error_payload_shape_on_monthly_failed_row(self):
        view = AttendanceCheckinViewSet()
        request = _request(
            {
                "action": "mark_present",
                "reason": "monthly",
                "site_id": "site-1",
                "cells": [{"user_id": "guard-1", "date": "2026-01-11", "from_status": "A"}],
            },
            enforce_headcount=True,
        )
        with patch.object(view, "_resolve_scope_location_id", return_value=("loc-1", None)), patch(
            "dashboard.views.User.objects"
        ) as user_objects, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(False, 6, 208, False),
        ), patch.object(
            view, "_monthly_cell_mark_present", side_effect=_cap_exc(on_date=date(2026, 1, 11))
        ):
            user_objects.filter.return_value.exclude.return_value.first.return_value = MagicMock(
                id="guard-1"
            )
            response = view.monthly_cell_action(request)

        failed = response.data["failed"][0]
        self.assertEqual(failed["code"], "site_shift_role_cap_exceeded")
        self.assertEqual(failed["role"], "guard")
        self.assertEqual(failed["current"], 10)
        self.assertEqual(failed["max"], 10)
        self.assertIn("error", failed)


# ---------------------------------------------------------------------------
# Phase 5 — punch / checkout / v6 / no-cap COUNT skip
# ---------------------------------------------------------------------------
class PunchRegressionTests(SimpleTestCase):
    def test_checkin_v6_delegates_to_checkin_v5(self):
        view = AttendanceCheckinViewSetV5()
        request = _request({})
        with patch.object(
            view, "checkin_v5", return_value=MagicMock(status_code=201)
        ) as v5_fn, patch(
            "patrol_backend.utils.face_liveness.liveness_enabled",
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()),
        ):
            response = view.checkin_v6(request)
        v5_fn.assert_called_once_with(request)
        self.assertTrue(getattr(request, "_gtms_require_liveness", False))
        self.assertEqual(response.status_code, 201)

    def test_checkout_v5_never_calls_headcount(self):
        view = AttendanceCheckinViewSetV5()
        request = _request({"site_id": "site-1"})
        request.user = MagicMock(id="guard-1", role="guard")
        assignment = MagicMock()
        assignment.shift = MagicMock(id="shift-1")
        assignment.location = MagicMock(id="loc-1")
        live = MagicMock()
        live.get_today_assignment_v2.return_value = (assignment, date(2026, 11, 3))
        open_checkin = MagicMock(site_id="site-1")
        matched_site = MagicMock(id="site-1", name="Site A")

        with patch.object(view, "_live", return_value=live), patch.object(
            view, "_parse_punch_coords", return_value=(1.0, 2.0)
        ), patch(
            "dashboard.views_v5._attendance_v3_shift_window_utc",
            return_value=(None, None, None, None, None),
        ), patch(
            "patrol_backend.utils.attendance_resolve.build_log_window_filter",
            return_value={},
        ), patch(
            "dashboard.views_v5.get_open_checkin_log",
            return_value=open_checkin,
        ), patch(
            "dashboard.views_v5.resolve_v5_punch_site",
            return_value=(matched_site, 0.0, "explicit"),
        ), patch(
            "patrol_backend.utils.attendance_resolve.get_or_create_attendance_for_shift_day",
            return_value=MagicMock(),
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as hc_fn, patch(
            "dashboard.views_v5.get_user_timezone_from_request",
            return_value="Asia/Kolkata",
        ), patch(
            "dashboard.views_v5._apply_v6_liveness_and_face",
            return_value=MagicMock(status_code=418),
        ):
            response = view.checkout_v5(request)

        hc_fn.assert_not_called()
        self.assertEqual(response.status_code, 418)

    def test_checkin_v5_no_caps_does_not_count(self):
        """Unset caps: punch must not run COUNT (identical cost to pre-feature aside from cache)."""
        view = AttendanceCheckinViewSetV5()
        request = _request({"site_id": "site-1"})
        request.user = MagicMock(id="guard-1", role="guard")
        assignment = MagicMock()
        assignment.shift = MagicMock(id="shift-1")
        assignment.location = MagicMock(id="loc-1")
        live = MagicMock()
        live.get_today_assignment_v2.return_value = (assignment, date(2026, 11, 3))
        matched_site = MagicMock(id="site-1")

        with patch.object(view, "_live", return_value=live), patch.object(
            view, "_parse_punch_coords", return_value=(1.0, 2.0)
        ), patch(
            "dashboard.views_v5.resolve_v5_punch_site",
            return_value=(matched_site, 0.0, "explicit"),
        ), patch(
            "dashboard.views_v5._attendance_v3_shift_window_utc",
            return_value=(None, None, None, None, None),
        ), patch(
            "dashboard.views_v5.get_open_checkin_log",
            return_value=None,
        ), patch(
            "patrol_backend.utils.attendance_resolve.enforce_checkin_allowed_after_checkout",
        ), patch(
            "patrol_backend.utils.attendance_resolve.build_log_window_filter",
            return_value={},
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_monthly_hours_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.shift_length_minutes",
            return_value=480,
        ), patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
        ) as count_fn, patch(
            "dashboard.views_v5._apply_v6_liveness_and_face",
            return_value=MagicMock(status_code=418),
        ), patch(
            "dashboard.views_v5.get_user_timezone_from_request",
            return_value="Asia/Kolkata",
        ):
            response = view.checkin_v5(request)

        count_fn.assert_not_called()
        self.assertEqual(response.status_code, 418)

    def test_legacy_face_attendance_v1_does_not_set_enforce_flag(self):
        """face_attendance_v2 sets the flag; v1 callers start without it."""
        request = SimpleNamespace(data={}, user=MagicMock(id="admin-1"))
        self.assertFalse(getattr(request, "_gtms_enforce_headcount", False))

        view = AttendanceCheckinViewSet()
        captured = {}

        def _capture(req):
            captured["flag"] = bool(getattr(req, "_gtms_enforce_headcount", False))
            return MagicMock(status_code=200)

        with patch.object(view, "face_attendance", side_effect=_capture), patch(
            "patrol_backend.utils.face_liveness.liveness_enabled",
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()),
        ):
            view.face_attendance_v2(request)
        self.assertTrue(captured["flag"])

    def test_kiosk_no_caps_skips_count_when_enforced(self):
        from patrol_backend.utils.kiosk_attendance_fast import kiosk_apply_punch

        user = SimpleNamespace(id="g1", role="guard")
        shift = SimpleNamespace(id="sh1")
        matched_site = SimpleNamespace(id="site-1", pk="site-1")
        assignment = MagicMock()
        org_location = MagicMock(id="loc-1")
        attendance = MagicMock(site_id=None, checkin_count=0, checkout_count=0)
        created = MagicMock()
        created.timestamp = MagicMock()
        created.image.save = MagicMock()

        with patch(
            "patrol_backend.utils.kiosk_attendance_fast.lock_or_create_attendance_for_shift_day",
            return_value=attendance,
        ), patch(
            "patrol_backend.utils.kiosk_attendance_fast.get_kiosk_session_state",
            return_value=(None, None, False),
        ), patch(
            "dashboard.models.CheckInLog.objects",
        ) as log_objects, patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.assert_monthly_hours_allowed",
        ), patch(
            "patrol_backend.utils.consecutive_work.shift_length_minutes",
            return_value=480,
        ), patch(
            "patrol_backend.utils.kiosk_attendance_fast.enforce_checkin_allowed_after_checkout",
        ), patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
        ) as count_fn, patch(
            "patrol_backend.utils.kiosk_attendance_fast.apply_v4_attendance_after_log",
        ), patch(
            "patrol_backend.utils.kiosk_attendance_fast.ensure_punch_sites",
        ), patch(
            "django.db.transaction.atomic",
        ):
            log_objects.filter.return_value.order_by.return_value.first.return_value = None
            log_objects.create.return_value = created
            action_mode, *_ = kiosk_apply_punch(
                user=user,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                shift_start_date=date(2026, 11, 3),
                matched_site=matched_site,
                lat=0,
                lon=0,
                raw_bytes=b"x",
                img_name="kiosk.jpg",
                search_start_utc=MagicMock(),
                search_end_utc=MagicMock(),
                user_tz="Asia/Kolkata",
                enforce_headcount=True,
                refresh_fn=None,
            )

        self.assertEqual(action_mode, "checkin")
        count_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Cross-cutting: error contract
# ---------------------------------------------------------------------------
class ErrorContractTests(SimpleTestCase):
    def test_as_dict_has_required_fields(self):
        payload = _cap_exc(role="FO", current=5, max_count=5).as_dict()
        for key in ("code", "error", "message", "role", "current", "max", "site_id", "shift_id", "date"):
            self.assertIn(key, payload)
        self.assertEqual(payload["code"], "site_shift_role_cap_exceeded")
        self.assertEqual(payload["role"], "fo")

    def test_serializer_validation_error_uses_error_key(self):
        ser = AssignmentSerializerV5()
        guard = SimpleNamespace(id="g1", role="guard")
        shift = SimpleNamespace(id="sh1", location_id="loc1")
        data = {
            "guard": guard,
            "shift": shift,
            "start_date": date(2026, 11, 3),
            "end_date": date(2026, 11, 3),
            "location": None,
            "site_id": "site-1",
        }
        with patch.object(AssignmentSerializerV5.__bases__[0], "validate", return_value=data), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_range",
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_range",
            side_effect=_cap_exc(),
        ):
            with self.assertRaises(serializers.ValidationError) as ctx:
                ser.validate(dict(data))
        self.assertIn("error", ctx.exception.detail)
