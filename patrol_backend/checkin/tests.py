from datetime import datetime, time, timedelta

import pytz
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from checkin.models import CheckIn
from scheduler.models import Assignment, Checkpoint, Location, Shift

User = get_user_model()


class ScanUnscheduledAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.tz = pytz.timezone("Asia/Kolkata")
        self.now_user = timezone.now().astimezone(self.tz)
        self.today = self.now_user.date()

        # Wide day shift that always covers "now" for non-overnight cases
        start = (self.now_user - timedelta(hours=2)).time().replace(second=0, microsecond=0)
        end = (self.now_user + timedelta(hours=2)).time().replace(second=0, microsecond=0)
        # If wrapping midnight, use overnight shape
        if end <= start:
            start = time(0, 0)
            end = time(23, 59)

        self.location = Location.objects.create(name="Unscheduled Scan Org")
        self.other_location = Location.objects.create(name="Other Org")
        self.guard = User.objects.create_user(
            email="guard-unscheduled@example.com",
            password="Password123!",
            name="Guard Unscheduled",
            location=self.location,
            role="guard",
            timezone="Asia/Kolkata",
        )
        self.shift = Shift.objects.create(
            name="Day Shift",
            location=self.location,
            start_time=start,
            end_time=end,
        )
        self.checkpoint = Checkpoint.objects.create(
            location=self.location,
            label="Gate A",
            type="qr",
            data="QR-GATE-A",
            latitude=12.9716,
            longitude=77.5946,
        )
        self.other_org_checkpoint = Checkpoint.objects.create(
            location=self.other_location,
            label="Other Gate",
            type="qr",
            data="QR-OTHER",
            latitude=12.9716,
            longitude=77.5946,
        )
        self.assignment = Assignment.objects.create(
            guard=self.guard,
            location=self.location,
            shift=self.shift,
            start_date=self.today - timedelta(days=1),
            end_date=self.today + timedelta(days=1),
            checkpoints=[],
        )
        self.client.force_authenticate(user=self.guard)

    def _payload(self, **overrides):
        body = {
            "guard": str(self.guard.id),
            "shift": str(self.shift.id),
            "assign_id": str(self.assignment.id),
            "checkpoint": str(self.checkpoint.id),
            "latitude": 12.9716,
            "longitude": 77.5946,
            "data": "QR-GATE-A",
        }
        body.update(overrides)
        return body

    def test_unscheduled_scan_success(self):
        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertTrue(res.data.get("success"))
        self.assertEqual(res.data.get("scan_mode"), "unscheduled")
        self.assertFalse(res.data.get("requires_checklist"))
        self.assertEqual(CheckIn.objects.filter(guard=self.guard).count(), 1)
        checkin = CheckIn.objects.get(guard=self.guard)
        self.assertFalse(checkin.has_checklist)
        self.assertEqual(checkin.checkpoint_id, self.checkpoint.id)

    def test_rejects_when_assignment_has_scheduled_checkpoints(self):
        self.assignment.checkpoints = [
            {"checkpoint_id": str(self.checkpoint.id), "time": "10:00"}
        ]
        self.assignment.save(update_fields=["checkpoints"])
        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("scheduled", (res.data.get("error") or "").lower())
        self.assertEqual(CheckIn.objects.count(), 0)

    def test_rejects_other_organisation_checkpoint(self):
        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(
                checkpoint=str(self.other_org_checkpoint.id),
                data="QR-OTHER",
            ),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(CheckIn.objects.count(), 0)

    def test_rejects_qr_mismatch(self):
        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(data="WRONG-QR"),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(CheckIn.objects.count(), 0)

    def test_rejects_outside_shift_window(self):
        # Force a short window that does not include now
        past_end = (self.now_user - timedelta(hours=3)).time().replace(
            second=0, microsecond=0
        )
        past_start = (self.now_user - timedelta(hours=5)).time().replace(
            second=0, microsecond=0
        )
        if past_end <= past_start:
            past_start = time(1, 0)
            past_end = time(2, 0)
        self.shift.start_time = past_start
        self.shift.end_time = past_end
        self.shift.save(update_fields=["start_time", "end_time"])

        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("shift window", (res.data.get("error") or "").lower())
        self.assertEqual(CheckIn.objects.count(), 0)

    def test_debounce_same_checkpoint_within_one_minute(self):
        first = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(),
            format="json",
        )
        self.assertEqual(first.status_code, status.HTTP_201_CREATED, first.data)

        second = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(),
            format="json",
        )
        self.assertEqual(second.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("minute", (second.data.get("error") or "").lower())
        self.assertEqual(CheckIn.objects.filter(guard=self.guard).count(), 1)

    def test_allows_rescan_after_one_minute(self):
        old = CheckIn.objects.create(
            guard=self.guard,
            checkpoint=self.checkpoint,
            shift=self.shift,
            timestamp=timezone.now() - timedelta(minutes=2),
            latitude=12.9716,
            longitude=77.5946,
            has_checklist=False,
        )
        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        self.assertEqual(CheckIn.objects.filter(guard=self.guard).count(), 2)
        self.assertNotEqual(
            CheckIn.objects.exclude(id=old.id).get().id,
            old.id,
        )

    def test_rejects_too_far_from_checkpoint(self):
        res = self.client.post(
            "/checkin/checkins/scan_unscheduled/",
            self._payload(latitude=13.5, longitude=78.5),
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertIn("too far", (res.data.get("error") or "").lower())
        self.assertEqual(CheckIn.objects.count(), 0)
