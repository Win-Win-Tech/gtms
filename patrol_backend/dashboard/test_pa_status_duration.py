from django.test import SimpleTestCase

from dashboard.attendance_pa_status import compute_closed_pa_status


class ClosedPaStatusDurationTests(SimpleTestCase):
    """present_duration 8h → 480 minutes. half=240, half+1h=300."""

    def test_absent_under_half(self):
        self.assertEqual(compute_closed_pa_status(239, 480), "A")
        self.assertEqual(compute_closed_pa_status(0, 480), "A")

    def test_half_day_at_four_and_five_hours(self):
        self.assertEqual(compute_closed_pa_status(240, 480), "HP")
        self.assertEqual(compute_closed_pa_status(300, 480), "HP")
        self.assertEqual(compute_closed_pa_status(270, 480), "HP")

    def test_less_duration_between_five_and_eight(self):
        self.assertEqual(compute_closed_pa_status(301, 480), "LD")
        self.assertEqual(compute_closed_pa_status(479, 480), "LD")

    def test_present_at_or_over_eight_hours(self):
        self.assertEqual(compute_closed_pa_status(480, 480), "P")
        self.assertEqual(compute_closed_pa_status(600, 480), "P")

    def test_present_wins_over_half_window_when_required_is_short(self):
        self.assertEqual(compute_closed_pa_status(60, 60), "P")
