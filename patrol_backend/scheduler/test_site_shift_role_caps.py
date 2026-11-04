from django.test import SimpleTestCase

from scheduler.views_shift_role_caps import _normalize_role, _parse_roles_map


class SiteShiftRoleCapHelpersTests(SimpleTestCase):
    def test_normalize_role_lowercases(self):
        self.assertEqual(_normalize_role(" SO "), "so")

    def test_parse_roles_skips_admin_and_zero(self):
        roles = _parse_roles_map({"SO": 5, "fo": 5, "guard": 10, "admin": 99, "x": 0})
        self.assertEqual(roles, {"so": 5, "fo": 5, "guard": 10})

    def test_parse_roles_empty(self):
        self.assertEqual(_parse_roles_map({}), {})
        self.assertEqual(_parse_roles_map(None), {})
