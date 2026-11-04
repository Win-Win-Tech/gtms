from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase
from rest_framework import serializers

from patrol_backend.utils.site_shift_headcount import SiteShiftRoleCapExceeded
from scheduler.serializers_v5 import AssignmentSerializerV5


class AssignmentSerializerV5HeadcountTests(SimpleTestCase):
    def test_validate_skips_when_no_site_id(self):
        ser = AssignmentSerializerV5()
        guard = SimpleNamespace(id="g1", role="guard")
        shift = SimpleNamespace(id="sh1", location_id="loc1")
        data = {
            "guard": guard,
            "shift": shift,
            "start_date": date(2026, 11, 3),
            "end_date": date(2026, 11, 3),
            "location": None,
        }
        with patch.object(AssignmentSerializerV5.__bases__[0], "validate", return_value=data), patch(
            "patrol_backend.utils.consecutive_work.assert_consecutive_work_range",
        ), patch(
            "patrol_backend.utils.site_shift_headcount.assert_headcount_range",
        ) as head_fn:
            out = ser.validate(dict(data))
        self.assertIs(out["guard"], guard)
        head_fn.assert_not_called()

    def test_validate_blocks_when_cap_exceeded(self):
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
            side_effect=SiteShiftRoleCapExceeded(
                role="guard",
                current=10,
                max_count=10,
                site_id="site-1",
                shift_id="sh1",
                on_date=date(2026, 11, 3),
            ),
        ):
            with self.assertRaises(serializers.ValidationError) as ctx:
                ser.validate(dict(data))
        self.assertIn("error", ctx.exception.detail)
