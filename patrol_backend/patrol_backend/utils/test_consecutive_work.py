from datetime import date, time, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from patrol_backend.utils.consecutive_work import (
    ConsecutiveWorkLimitExceeded,
    MonthlyHourLimitExceeded,
    assert_consecutive_work_allowed,
    assert_consecutive_work_range,
    assert_monthly_hours_allowed,
    consecutive_present_count,
    consecutive_work_block_payload,
    is_worked_pa_status,
    monthly_hours_block_payload,
    resolve_labour_law_settings,
    shift_length_minutes,
    upsert_org_labour_law_settings,
    upsert_site_consecutive_work_settings,
)


class ConsecutiveWorkLogicTests(SimpleTestCase):
    def test_ld_and_present_count_as_worked(self):
        self.assertTrue(is_worked_pa_status("P"))
        self.assertTrue(is_worked_pa_status("LD"))
        self.assertTrue(is_worked_pa_status("OW"))
        self.assertTrue(is_worked_pa_status("M"))
        self.assertTrue(is_worked_pa_status("HD"))
        self.assertTrue(is_worked_pa_status(None))
        self.assertFalse(is_worked_pa_status("A"))

    def test_absent_in_middle_resets_streak(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(5)}  # Mon-Fri present, Sat absent
        sunday = monday + timedelta(days=6)
        self.assertEqual(consecutive_present_count(worked, sunday, 6), 0)

    def test_six_presents_blocks_seventh(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}  # Mon-Sat
        sunday = monday + timedelta(days=6)
        self.assertEqual(consecutive_present_count(worked, sunday, 6), 6)

    def test_ld_days_still_count_toward_streak(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        self.assertGreaterEqual(consecutive_present_count(worked, sunday, 6), 6)

    def test_disabled_limit_allows_seventh_day(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        assert_consecutive_work_allowed(
            user=MagicMock(id="u1"),
            target_date=sunday,
            worked_dates=worked,
            enabled=False,
            max_days=6,
        )

    def test_assert_blocks_seventh_present_day(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        with self.assertRaises(ConsecutiveWorkLimitExceeded) as ctx:
            assert_consecutive_work_allowed(
                user=MagicMock(id="u1"),
                target_date=sunday,
                worked_dates=worked,
                enabled=True,
                max_days=6,
            )
        self.assertEqual(ctx.exception.consecutive, 6)
        self.assertEqual(ctx.exception.max_days, 6)
        payload = ctx.exception.as_dict()
        self.assertEqual(payload["code"], "consecutive_work_limit")
        self.assertEqual(payload["blocked_date"], "2026-01-11")

    def test_assigned_but_absent_allows_next_day(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(5)}  # Sat missing
        sunday = monday + timedelta(days=6)
        assert_consecutive_work_allowed(
            user=MagicMock(id="u1"),
            target_date=sunday,
            worked_dates=worked,
            enabled=True,
            max_days=6,
        )

    def test_block_payload_for_bulk_and_monthly_rows(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_worked_dates",
            return_value=worked,
        ):
            blocked = consecutive_work_block_payload(
                MagicMock(id="u1"),
                sunday,
                enabled=True,
                max_days=6,
            )
        self.assertIsNotNone(blocked)
        self.assertEqual(blocked["code"], "consecutive_work_limit")

        with patch(
            "patrol_backend.utils.consecutive_work.fetch_worked_dates",
            return_value=set(),
        ):
            allowed = consecutive_work_block_payload(
                MagicMock(id="u1"),
                sunday,
                enabled=True,
                max_days=6,
            )
        self.assertIsNone(allowed)

    def test_assignment_range_blocks_span_longer_than_max_days(self):
        start = date(2026, 10, 31)
        end = date(2026, 11, 13)
        with self.assertRaises(ConsecutiveWorkLimitExceeded) as ctx:
            assert_consecutive_work_range(
                MagicMock(id="u1"),
                start,
                end,
                assigned_dates=set(),
                weekoff_dates=set(),
                worked_dates=set(),
                enabled=True,
                max_days=6,
            )
        self.assertEqual(ctx.exception.reason, ConsecutiveWorkLimitExceeded.REASON_ASSIGNMENT)
        self.assertEqual(ctx.exception.max_days, 6)
        self.assertGreater(ctx.exception.consecutive, 6)
        self.assertEqual(ctx.exception.blocked_date, date(2026, 11, 6))
        self.assertIn("Cannot assign", ctx.exception.message)
        self.assertNotIn("consecutive_present_days", ctx.exception.as_dict())
        self.assertEqual(ctx.exception.as_serializer_error(), {"error": ctx.exception.message})

    def test_assignment_range_allows_exactly_max_days(self):
        start = date(2026, 10, 31)
        end = start + timedelta(days=5)  # 6 days
        assert_consecutive_work_range(
            MagicMock(id="u1"),
            start,
            end,
            assigned_dates=set(),
            weekoff_dates=set(),
            worked_dates=set(),
            enabled=True,
            max_days=6,
        )

    def test_assignment_range_weekoff_splits_streak(self):
        start = date(2026, 10, 31)
        end = date(2026, 11, 13)
        weekoff = {date(2026, 11, 6), date(2026, 11, 13)}
        assert_consecutive_work_range(
            MagicMock(id="u1"),
            start,
            end,
            assigned_dates=set(),
            weekoff_dates=weekoff,
            worked_dates=set(),
            enabled=True,
            max_days=6,
        )

    def test_assignment_range_disabled_allows_long_span(self):
        assert_consecutive_work_range(
            MagicMock(id="u1"),
            date(2026, 10, 31),
            date(2026, 11, 13),
            assigned_dates=set(),
            weekoff_dates=set(),
            enabled=False,
            max_days=6,
        )

    def test_assignment_range_blocks_adjacent_existing_assignment(self):
        existing = {date(2026, 10, 26) + timedelta(days=i) for i in range(6)}  # 26-31
        with self.assertRaises(ConsecutiveWorkLimitExceeded) as ctx:
            assert_consecutive_work_range(
                MagicMock(id="u1"),
                date(2026, 11, 1),
                date(2026, 11, 1),
                assigned_dates=existing,
                weekoff_dates=set(),
                worked_dates=set(),
                enabled=True,
                max_days=6,
            )
        self.assertEqual(ctx.exception.reason, ConsecutiveWorkLimitExceeded.REASON_ASSIGNMENT)

    def test_assignment_range_blocks_next_shift_after_six_worked_days(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        with self.assertRaises(ConsecutiveWorkLimitExceeded) as ctx:
            assert_consecutive_work_range(
                MagicMock(id="u1"),
                sunday,
                sunday,
                assigned_dates=set(),
                weekoff_dates=set(),
                worked_dates=worked,
                enabled=True,
                max_days=6,
            )
        self.assertEqual(ctx.exception.reason, ConsecutiveWorkLimitExceeded.REASON_WORKED)
        self.assertIn("already worked", ctx.exception.message)
        self.assertIn("rest day", ctx.exception.message)

    def test_create_assignment_v5_today_blocked_after_six_assigned_days(self):
        today = date(2026, 10, 31)
        existing = {today - timedelta(days=i) for i in range(1, 7)}  # 25-30
        with self.assertRaises(ConsecutiveWorkLimitExceeded):
            assert_consecutive_work_range(
                MagicMock(id="u1"),
                today,
                today,
                assigned_dates=existing,
                weekoff_dates=set(),
                worked_dates=set(),
                enabled=True,
                max_days=6,
            )


class ConsecutiveWorkDashboardGateTests(SimpleTestCase):
    """Dashboard bulk-entry and monthly mark-present must fail the row, not the whole batch."""

    def test_bulk_entry_failed_row_shape(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_worked_dates",
            return_value=worked,
        ):
            block = consecutive_work_block_payload(
                MagicMock(id="guard-1"),
                sunday,
                location_id="loc-1",
                site_id="site-1",
                enabled=True,
                max_days=6,
            )
        failed_row = {
            "user_id": "guard-1",
            "error": block["error"],
            "code": block["code"],
            "consecutive_present_days": block["consecutive_present_days"],
            "max_consecutive_work_days": block["max_consecutive_work_days"],
            "blocked_date": block["blocked_date"],
        }
        self.assertEqual(failed_row["code"], "consecutive_work_limit")
        self.assertEqual(failed_row["blocked_date"], sunday.isoformat())

    def test_monthly_mark_present_uses_same_block(self):
        monday = date(2026, 1, 5)
        worked = {monday + timedelta(days=i) for i in range(6)}
        sunday = monday + timedelta(days=6)
        with self.assertRaises(ConsecutiveWorkLimitExceeded):
            assert_consecutive_work_allowed(
                MagicMock(id="guard-1"),
                sunday,
                worked_dates=worked,
                enabled=True,
                max_days=6,
            )

    def test_monthly_hours_block_when_already_at_cap(self):
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_month_worked_minutes",
            return_value=208 * 60,
        ):
            with self.assertRaises(MonthlyHourLimitExceeded) as ctx:
                assert_monthly_hours_allowed(
                    MagicMock(id="u1"),
                    date(2026, 10, 31),
                    extra_minutes=12 * 60,
                    enabled=True,
                    max_hours=208,
                )
            self.assertEqual(ctx.exception.max_hours, 208)
            self.assertEqual(ctx.exception.as_dict()["code"], "monthly_hour_limit")
            self.assertIn("208", ctx.exception.message)

    def test_monthly_hours_block_when_shift_would_exceed(self):
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_month_worked_minutes",
            return_value=200 * 60,
        ):
            with self.assertRaises(MonthlyHourLimitExceeded):
                assert_monthly_hours_allowed(
                    MagicMock(id="u1"),
                    date(2026, 10, 31),
                    extra_minutes=12 * 60,
                    enabled=True,
                    max_hours=208,
                )

    def test_monthly_hours_allow_when_under_cap(self):
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_month_worked_minutes",
            return_value=100 * 60,
        ):
            assert_monthly_hours_allowed(
                MagicMock(id="u1"),
                date(2026, 10, 31),
                extra_minutes=12 * 60,
                enabled=True,
                max_hours=208,
            )

    def test_monthly_hours_disabled_with_master_switch_off(self):
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_month_worked_minutes",
            return_value=300 * 60,
        ) as fetch_fn:
            assert_monthly_hours_allowed(
                MagicMock(id="u1"),
                date(2026, 10, 31),
                extra_minutes=12 * 60,
                enabled=False,
                max_hours=208,
            )
            fetch_fn.assert_not_called()

    def test_monthly_hours_payload_for_bulk(self):
        with patch(
            "patrol_backend.utils.consecutive_work.fetch_month_worked_minutes",
            return_value=208 * 60,
        ):
            blocked = monthly_hours_block_payload(
                MagicMock(id="u1"),
                date(2026, 10, 31),
                extra_minutes=60,
                enabled=True,
                max_hours=208,
            )
        self.assertEqual(blocked["code"], "monthly_hour_limit")

    def test_shift_length_minutes_overnight(self):
        shift = MagicMock()
        shift.start_time = time(19, 0)
        shift.end_time = time(7, 0)
        self.assertEqual(shift_length_minutes(shift), 12 * 60)


class LabourLawScopeTests(SimpleTestCase):
    def test_resolve_prefers_site_hours_over_org(self):
        rows = [
            SimpleNamespace(
                key="labour_law_limit_enabled",
                value="true",
                location_id="loc-1",
                site_id=None,
            ),
            SimpleNamespace(
                key="max_consecutive_work_days",
                value="6",
                location_id="loc-1",
                site_id=None,
            ),
            SimpleNamespace(
                key="max_monthly_work_hours",
                value="208",
                location_id="loc-1",
                site_id=None,
            ),
            SimpleNamespace(
                key="max_monthly_work_hours",
                value="210",
                location_id=None,
                site_id="site-1",
            ),
        ]
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.only.return_value = rows
        with patch("scheduler.models.SiteSetting") as setting_model:
            setting_model.objects.filter.return_value = qs
            enabled, max_days, max_hours, inherited = resolve_labour_law_settings(
                "loc-1", "site-1"
            )
        self.assertTrue(enabled)
        self.assertEqual(max_days, 6)
        self.assertEqual(max_hours, 210)
        self.assertFalse(inherited)

    def test_upsert_site_does_not_write_org_row(self):
        created = []
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.first.return_value = None
        setting_model = MagicMock()
        setting_model.objects.filter.return_value = qs
        setting_model.objects.create.side_effect = lambda **kwargs: created.append(kwargs) or MagicMock()
        site = MagicMock(id="site-1")
        with patch("scheduler.models.SiteSetting", setting_model):
            upsert_site_consecutive_work_settings(
                site, enabled=True, max_days=6, max_hours=210
            )
        self.assertTrue(created)
        for row in created:
            self.assertEqual(row["site_id"], "site-1")
            self.assertIsNone(row["location_id"])
        hours = next(row for row in created if row["key"] == "max_monthly_work_hours")
        self.assertEqual(hours["value"], "210")
        self.assertTrue(any(call.kwargs.get("site_id") == "site-1" for call in qs.filter.call_args_list))
        self.assertFalse(any(call.kwargs.get("location_id") == "loc-1" for call in qs.filter.call_args_list))

    def test_upsert_org_does_not_write_site_row(self):
        created = []
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.first.return_value = None
        setting_model = MagicMock()
        setting_model.objects.filter.return_value = qs
        setting_model.objects.create.side_effect = lambda **kwargs: created.append(kwargs) or MagicMock()
        with patch("scheduler.models.SiteSetting", setting_model):
            upsert_org_labour_law_settings(
                "loc-1", enabled=True, max_days=6, max_hours=208
            )
        self.assertTrue(created)
        for row in created:
            self.assertEqual(row["location_id"], "loc-1")
            self.assertIsNone(row["site_id"])
        self.assertTrue(any(call.kwargs.get("location_id") == "loc-1" for call in qs.filter.call_args_list))
        self.assertTrue(all(call.kwargs.get("site_id__isnull") is True for call in qs.filter.call_args_list))


class LabourLawSettingsEndpointTests(SimpleTestCase):
    def test_put_with_site_updates_site_only(self):
        from scheduler.views import SiteSettingViewSet

        view = SiteSettingViewSet()
        request = MagicMock()
        request.method = "PUT"
        request.user = MagicMock(is_superuser=True, location_id="loc-1")
        request.data = {
            "site_id": "site-1",
            "location_id": "loc-1",
            "labour_law_limit_enabled": True,
            "max_consecutive_work_days": 6,
            "max_monthly_work_hours": 210,
        }
        request.query_params = {}
        site = MagicMock(id="site-1", location_id="loc-1")
        with patch("scheduler.views.LocationSite.objects") as site_objects, patch(
            "patrol_backend.utils.consecutive_work.upsert_site_consecutive_work_settings"
        ) as site_upsert, patch(
            "patrol_backend.utils.consecutive_work.upsert_org_labour_law_settings"
        ) as org_upsert, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(True, 6, 210, False),
        ):
            site_objects.filter.return_value.first.return_value = site
            response = view.labour_law(request)

        site_upsert.assert_called_once()
        org_upsert.assert_not_called()
        self.assertEqual(response.data["scope"], "site")
        self.assertEqual(response.data["max_monthly_work_hours"], 210)
        self.assertEqual(str(site_upsert.call_args.args[0].id), "site-1")

    def test_put_without_site_updates_org_default(self):
        from scheduler.views import SiteSettingViewSet

        view = SiteSettingViewSet()
        request = MagicMock()
        request.method = "PUT"
        request.user = MagicMock(is_superuser=False, location_id="loc-1")
        request.data = {
            "location_id": "loc-1",
            "site_id": None,
            "labour_law_limit_enabled": True,
            "max_consecutive_work_days": 6,
            "max_monthly_work_hours": 208,
        }
        request.query_params = {}
        with patch(
            "patrol_backend.utils.consecutive_work.upsert_site_consecutive_work_settings"
        ) as site_upsert, patch(
            "patrol_backend.utils.consecutive_work.upsert_org_labour_law_settings"
        ) as org_upsert, patch(
            "patrol_backend.utils.consecutive_work.resolve_labour_law_settings",
            return_value=(True, 6, 208, False),
        ):
            response = view.labour_law(request)

        org_upsert.assert_called_once()
        site_upsert.assert_not_called()
        self.assertEqual(response.data["scope"], "org")
        self.assertEqual(org_upsert.call_args.args[0], "loc-1")
