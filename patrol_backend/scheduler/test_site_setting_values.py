from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from scheduler.site_setting_values import overlay_site_setting_rows


class OverlaySiteSettingTests(SimpleTestCase):
    def test_site_override_wins(self):
        org = SimpleNamespace(key="attendance_distance", value="50")
        site = SimpleNamespace(key="attendance_distance", value="20")
        paired = overlay_site_setting_rows([org], [site])
        self.assertEqual(len(paired), 1)
        self.assertEqual(paired[0][0].value, "20")
        self.assertFalse(paired[0][1])

    def test_missing_override_is_inherited(self):
        org = SimpleNamespace(key="time", value="15")
        paired = overlay_site_setting_rows([org], [])
        self.assertEqual(paired[0][0].value, "15")
        self.assertTrue(paired[0][1])

    def test_unrelated_site_key_is_ignored(self):
        org = SimpleNamespace(key="time", value="15")
        other = SimpleNamespace(key="distance", value="100")
        paired = overlay_site_setting_rows([org], [other])
        self.assertEqual(paired[0][0].key, "time")
        self.assertTrue(paired[0][1])


class UpsertValuesEndpointTests(SimpleTestCase):
    def test_put_with_site_writes_site_scope(self):
        from scheduler.views import SiteSettingViewSet

        view = SiteSettingViewSet()
        request = MagicMock()
        request.method = "PUT"
        request.user = MagicMock(is_superuser=True, location_id="loc-1")
        request.data = {
            "site_id": "site-1",
            "location_id": "loc-1",
            "items": [{"key": "attendance_distance", "value": "20", "unit": "m"}],
        }
        site = MagicMock(id="site-1", location_id="loc-1")
        saved = MagicMock()
        with patch("scheduler.views.LocationSite.objects") as site_objects, patch(
            "scheduler.site_setting_values.upsert_setting_value",
            return_value=saved,
        ) as upsert, patch(
            "scheduler.views.SiteSettingSerializer",
        ) as serializer:
            site_objects.filter.return_value.first.return_value = site
            serializer.return_value.data = {"key": "attendance_distance", "value": "20"}
            response = view.upsert_values(request)

        upsert.assert_called_once()
        self.assertEqual(str(upsert.call_args.kwargs["site_id"]), "site-1")
        self.assertEqual(response.data[0]["scope"], "site")
        self.assertFalse(response.data[0]["inherited"])

    def test_put_without_site_writes_org_scope(self):
        from scheduler.views import SiteSettingViewSet

        view = SiteSettingViewSet()
        request = MagicMock()
        request.method = "PUT"
        request.user = MagicMock(is_superuser=False, location_id="loc-1")
        request.data = {
            "location_id": "loc-1",
            "site_id": None,
            "items": [{"key": "is_host_approve_enabled", "value": "false"}],
        }
        with patch(
            "scheduler.site_setting_values.upsert_setting_value",
            return_value=MagicMock(),
        ) as upsert, patch(
            "scheduler.views.SiteSettingSerializer",
        ) as serializer:
            serializer.return_value.data = {"key": "is_host_approve_enabled", "value": "false"}
            response = view.upsert_values(request)

        upsert.assert_called_once()
        self.assertEqual(upsert.call_args.kwargs["site_id"], None)
        self.assertEqual(upsert.call_args.kwargs["location_id"], "loc-1")
        self.assertEqual(response.data[0]["scope"], "org")
