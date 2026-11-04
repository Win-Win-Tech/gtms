from datetime import date
from unittest.mock import patch

from django.test import SimpleTestCase

from patrol_backend.utils.site_shift_headcount import (
    SiteShiftRoleCapExceeded,
    assert_headcount_allowed,
    assert_headcount_range,
    get_caps_cached,
    invalidate_caps_cache,
)


class SiteShiftHeadcountHelperTests(SimpleTestCase):
    def setUp(self):
        invalidate_caps_cache()

    def tearDown(self):
        invalidate_caps_cache()

    def test_no_caps_fast_path_skips_count(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount._load_caps_from_db",
            return_value={},
        ) as load_caps, patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
        ) as count_fn:
            info = assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="guard",
            )
        self.assertEqual(info["headcount_skipped"], "no_caps")
        self.assertFalse(info["headcount_checked"])
        load_caps.assert_called_once()
        count_fn.assert_not_called()

    def test_caps_cached_second_call_skips_db(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount._load_caps_from_db",
            return_value={"guard": 10},
        ) as load_caps:
            first = get_caps_cached("site-1", "shift-1")
            second = get_caps_cached("site-1", "shift-1")
        self.assertEqual(first, {"guard": 10})
        self.assertEqual(second, {"guard": 10})
        load_caps.assert_called_once()

    def test_invalidate_forces_reload(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount._load_caps_from_db",
            side_effect=[{"guard": 10}, {"guard": 5}],
        ) as load_caps:
            get_caps_cached("site-1", "shift-1")
            invalidate_caps_cache(site_id="site-1", shift_id="shift-1")
            caps = get_caps_cached("site-1", "shift-1")
        self.assertEqual(caps, {"guard": 5})
        self.assertEqual(load_caps.call_count, 2)

    def test_under_cap_allows(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={"guard": 10},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
            return_value=9,
        ):
            info = assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="guard",
                extra=1,
            )
        self.assertTrue(info["headcount_checked"])
        self.assertEqual(info["current"], 9)
        self.assertEqual(info["max"], 10)

    def test_over_cap_raises(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={"guard": 10},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
            return_value=10,
        ):
            with self.assertRaises(SiteShiftRoleCapExceeded) as ctx:
                assert_headcount_allowed(
                    site_id="site-1",
                    shift_id="shift-1",
                    on_date=date(2026, 11, 3),
                    role="guard",
                    extra=1,
                )
        payload = ctx.exception.as_dict()
        self.assertEqual(payload["code"], "site_shift_role_cap_exceeded")
        self.assertEqual(payload["role"], "guard")
        self.assertEqual(payload["current"], 10)
        self.assertEqual(payload["max"], 10)

    def test_role_without_cap_is_skipped(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={"so": 5},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.count_role_assigned",
        ) as count_fn:
            info = assert_headcount_allowed(
                site_id="site-1",
                shift_id="shift-1",
                on_date=date(2026, 11, 3),
                role="guard",
            )
        self.assertEqual(info["headcount_skipped"], "role_uncapped")
        count_fn.assert_not_called()

    def test_range_no_caps_skips_all_days(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
        ) as day_fn:
            info = assert_headcount_range(
                site_id="site-1",
                shift_id="shift-1",
                start_date=date(2026, 11, 1),
                end_date=date(2026, 11, 5),
                role="guard",
            )
        self.assertEqual(info["headcount_skipped"], "no_caps")
        day_fn.assert_not_called()

    def test_range_checks_each_day_when_capped(self):
        with patch(
            "patrol_backend.utils.site_shift_headcount.get_caps_cached",
            return_value={"guard": 10},
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_allowed",
            return_value={"headcount_checked": True},
        ) as day_fn:
            info = assert_headcount_range(
                site_id="site-1",
                shift_id="shift-1",
                start_date=date(2026, 11, 1),
                end_date=date(2026, 11, 3),
                role="guard",
                exclude_guard_id="g1",
            )
        self.assertEqual(info["days_checked"], 3)
        self.assertEqual(day_fn.call_count, 3)

    def test_serializer_error_shape(self):
        exc = SiteShiftRoleCapExceeded(
            role="guard",
            current=10,
            max_count=10,
            site_id="s1",
            shift_id="sh1",
            on_date=date(2026, 11, 3),
        )
        self.assertEqual(exc.as_serializer_error(), {"error": exc.message})
        self.assertEqual(exc.as_dict()["code"], "site_shift_role_cap_exceeded")
