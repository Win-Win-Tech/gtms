from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from rest_framework import status

from dashboard.views import AttendanceCheckinViewSet
from dashboard.views_v5 import AttendanceCheckinViewSetV5
from patrol_backend.utils.site_shift_headcount import SiteShiftRoleCapExceeded


def _cap_exc():
    return SiteShiftRoleCapExceeded(
        role="guard",
        current=10,
        max_count=10,
        site_id="site-1",
        shift_id="shift-1",
        on_date=date(2026, 1, 11),
    )


class CheckinV5HeadcountTests(SimpleTestCase):
    def _run_checkin(self, *, matched_site, hc_side_effect=None, hc_return=None):
        view = AttendanceCheckinViewSetV5()
        request = MagicMock()
        request.user = MagicMock(id="guard-1", role="guard")
        request.data = {"site_id": "site-1"} if matched_site else {}
        request.FILES = MagicMock()
        request.FILES.get.return_value = None

        assignment = MagicMock()
        shift = MagicMock(id="shift-1")
        assignment.shift = shift
        assignment.location = MagicMock(id="loc-1")
        live = MagicMock()
        live.get_today_assignment_v2.return_value = (assignment, date(2026, 1, 11))

        hc_kwargs = {}
        if hc_side_effect is not None:
            hc_kwargs["side_effect"] = hc_side_effect
        else:
            hc_kwargs["return_value"] = hc_return or {
                "headcount_checked": False,
                "headcount_skipped": "no_caps",
            }

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
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
            **hc_kwargs,
        ) as hc_fn, patch(
            "dashboard.views_v5._apply_v6_liveness_and_face",
            return_value=MagicMock(status_code=418),
        ) as face_fn, patch(
            "dashboard.views_v5.get_user_timezone_from_request",
            return_value="Asia/Kolkata",
        ):
            response = view.checkin_v5(request)
        return response, hc_fn, face_fn

    def test_blocks_when_cap_exceeded_before_face(self):
        matched_site = MagicMock(id="site-1")
        response, hc_fn, face_fn = self._run_checkin(
            matched_site=matched_site,
            hc_side_effect=_cap_exc(),
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data.get("code"), "site_shift_role_cap_exceeded")
        hc_fn.assert_called_once()
        face_fn.assert_not_called()

    def test_no_site_skips_headcount_and_continues(self):
        response, hc_fn, face_fn = self._run_checkin(matched_site=None)
        hc_fn.assert_not_called()
        face_fn.assert_called_once()
        self.assertEqual(response.status_code, 418)

    def test_under_cap_continues_to_face(self):
        matched_site = MagicMock(id="site-1")
        response, hc_fn, face_fn = self._run_checkin(
            matched_site=matched_site,
            hc_return={"headcount_checked": True, "headcount_skipped": None},
        )
        hc_fn.assert_called_once()
        face_fn.assert_called_once()
        self.assertEqual(response.status_code, 418)


class FaceAttendanceV2HeadcountTests(SimpleTestCase):
    def test_v2_sets_enforce_flag(self):
        view = AttendanceCheckinViewSet()
        request = MagicMock()

        def _capture(req):
            self.assertTrue(getattr(req, "_gtms_enforce_headcount", False))
            return MagicMock(status_code=200)

        with patch.object(view, "face_attendance", side_effect=_capture), patch(
            "patrol_backend.utils.face_liveness.liveness_enabled",
            return_value=MagicMock(__enter__=MagicMock(), __exit__=MagicMock()),
        ):
            response = view.face_attendance_v2(request)

        self.assertEqual(response.status_code, 200)

    def test_kiosk_checkin_blocks_when_cap_exceeded(self):
        from patrol_backend.utils.kiosk_attendance_fast import kiosk_apply_punch

        user = SimpleNamespace(id="g1", role="guard")
        shift = SimpleNamespace(id="sh1")
        matched_site = SimpleNamespace(id="site-1")
        assignment = MagicMock()
        org_location = MagicMock(id="loc-1")

        with patch(
            "patrol_backend.utils.kiosk_attendance_fast.lock_or_create_attendance_for_shift_day",
            return_value=MagicMock(),
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
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
            side_effect=_cap_exc(),
        ) as hc_fn, patch(
            "django.db.transaction.atomic",
        ):
            log_objects.filter.return_value.order_by.return_value.first.return_value = None
            with self.assertRaises(SiteShiftRoleCapExceeded):
                kiosk_apply_punch(
                    user=user,
                    assignment=assignment,
                    shift=shift,
                    org_location=org_location,
                    shift_start_date=date(2026, 1, 11),
                    matched_site=matched_site,
                    lat=0,
                    lon=0,
                    raw_bytes=b"x",
                    img_name="kiosk.jpg",
                    search_start_utc=MagicMock(),
                    search_end_utc=MagicMock(),
                    user_tz="Asia/Kolkata",
                    enforce_headcount=True,
                )
            hc_fn.assert_called_once()

    def test_kiosk_checkin_skips_when_flag_false(self):
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
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as hc_fn, patch(
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
                shift_start_date=date(2026, 1, 11),
                matched_site=matched_site,
                lat=0,
                lon=0,
                raw_bytes=b"x",
                img_name="kiosk.jpg",
                search_start_utc=MagicMock(),
                search_end_utc=MagicMock(),
                user_tz="Asia/Kolkata",
                enforce_headcount=False,
                refresh_fn=None,
            )

        self.assertEqual(action_mode, "checkin")
        hc_fn.assert_not_called()

    def test_kiosk_checkout_skips_headcount_even_when_flag_true(self):
        from patrol_backend.utils.kiosk_attendance_fast import kiosk_apply_punch

        user = SimpleNamespace(id="g1", role="guard")
        shift = SimpleNamespace(id="sh1")
        matched_site = SimpleNamespace(id="site-1", pk="site-1")
        assignment = MagicMock()
        org_location = MagicMock(id="loc-1")
        attendance = MagicMock(site_id=None, checkin_count=1, checkout_count=0)
        created = MagicMock()
        created.timestamp = MagicMock()
        created.image.save = MagicMock()
        latest_checkin = MagicMock()

        with patch(
            "patrol_backend.utils.kiosk_attendance_fast.lock_or_create_attendance_for_shift_day",
            return_value=attendance,
        ), patch(
            "patrol_backend.utils.kiosk_attendance_fast.get_kiosk_session_state",
            return_value=(latest_checkin, None, True),
        ), patch(
            "dashboard.models.CheckInLog.objects",
        ) as log_objects, patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as hc_fn, patch(
            "patrol_backend.utils.kiosk_attendance_fast.apply_v4_attendance_after_log",
        ), patch(
            "patrol_backend.utils.kiosk_attendance_fast.ensure_punch_sites",
        ), patch(
            "django.db.transaction.atomic",
        ), patch(
            "django.utils.timezone.now",
        ) as now_fn:
            now = MagicMock()
            now_fn.return_value = now
            now.__sub__ = MagicMock(
                return_value=MagicMock(total_seconds=MagicMock(return_value=9999))
            )
            log_objects.filter.return_value.order_by.return_value.first.return_value = None
            log_objects.create.return_value = created
            action_mode, *_ = kiosk_apply_punch(
                user=user,
                assignment=assignment,
                shift=shift,
                org_location=org_location,
                shift_start_date=date(2026, 1, 11),
                matched_site=matched_site,
                lat=0,
                lon=0,
                raw_bytes=b"x",
                img_name="kiosk.jpg",
                search_start_utc=MagicMock(),
                search_end_utc=MagicMock(),
                user_tz="Asia/Kolkata",
                enforce_headcount=True,
                min_checkout_minutes=0,
                refresh_fn=None,
            )

        self.assertEqual(action_mode, "checkout")
        hc_fn.assert_not_called()
