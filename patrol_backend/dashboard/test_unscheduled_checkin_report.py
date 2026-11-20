from datetime import datetime, timedelta

import pytz
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from checkin.models import CheckIn
from dashboard.views import (
    _append_unscheduled_checkin_report_rows,
    _assignment_has_scheduled_checkpoints,
)
from scheduler.models import Assignment, Checkpoint, Location, Shift

User = get_user_model()


class UnscheduledCheckinReportTests(TestCase):
    def setUp(self):
        self.tz = pytz.timezone("Asia/Kolkata")
        self.now_user = timezone.now().astimezone(self.tz)
        self.today = self.now_user.date()
        start = (self.now_user - timedelta(hours=2)).time().replace(second=0, microsecond=0)
        end = (self.now_user + timedelta(hours=2)).time().replace(second=0, microsecond=0)
        if end <= start:
            from datetime import time as dtime

            start = dtime(0, 0)
            end = dtime(23, 59)

        self.location = Location.objects.create(name="Report Unsched Org")
        self.guard = User.objects.create_user(
            email="report-unsched@example.com",
            password="Password123!",
            name="Report Guard",
            location=self.location,
            role="guard",
            timezone="Asia/Kolkata",
        )
        self.shift = Shift.objects.create(
            name="Day",
            location=self.location,
            start_time=start,
            end_time=end,
        )
        self.checkpoint = Checkpoint.objects.create(
            location=self.location,
            label="Gate B",
            type="qr",
            data="QR-B",
            latitude=12.97,
            longitude=77.59,
        )
        self.assignment = Assignment.objects.create(
            guard=self.guard,
            location=self.location,
            shift=self.shift,
            start_date=self.today - timedelta(days=1),
            end_date=self.today + timedelta(days=1),
            checkpoints=[],
        )

    def test_has_scheduled_checkpoints_flag(self):
        self.assertFalse(_assignment_has_scheduled_checkpoints(self.assignment))
        self.assignment.checkpoints = [
            {"checkpoint_id": str(self.checkpoint.id), "time": "10:00"}
        ]
        self.assertTrue(_assignment_has_scheduled_checkpoints(self.assignment))

    def test_append_unscheduled_rows_status_scanned(self):
        CheckIn.objects.create(
            guard=self.guard,
            checkpoint=self.checkpoint,
            shift=self.shift,
            timestamp=timezone.now(),
            latitude=12.97,
            longitude=77.59,
            has_checklist=False,
        )
        report = []
        start_dt_user = datetime.combine(self.today, datetime.min.time())
        _append_unscheduled_checkin_report_rows(
            report=report,
            assignments=[self.assignment],
            date_range=[self.today],
            start_dt_user=start_dt_user,
            end_date=self.today,
            today=self.today,
            user_tz=self.tz,
            checkpoints_lookup={str(self.checkpoint.id): self.checkpoint.label},
        )
        self.assertEqual(len(report), 1)
        self.assertEqual(report[0]["status"], "Scanned")
        self.assertIsNone(report[0]["expected_time"])
        self.assertIsNone(report[0]["delay_minutes"])
        self.assertEqual(report[0]["checkpoint_name"], "Gate B")
        self.assertEqual(report[0]["scan_mode"], "unscheduled")

    def test_scheduled_assignment_skipped_by_unscheduled_append(self):
        self.assignment.checkpoints = [
            {"checkpoint_id": str(self.checkpoint.id), "time": "10:00"}
        ]
        self.assignment.save(update_fields=["checkpoints"])
        CheckIn.objects.create(
            guard=self.guard,
            checkpoint=self.checkpoint,
            shift=self.shift,
            timestamp=timezone.now(),
            latitude=12.97,
            longitude=77.59,
        )
        report = []
        start_dt_user = datetime.combine(self.today, datetime.min.time())
        _append_unscheduled_checkin_report_rows(
            report=report,
            assignments=[self.assignment],
            date_range=[self.today],
            start_dt_user=start_dt_user,
            end_date=self.today,
            today=self.today,
            user_tz=self.tz,
        )
        self.assertEqual(report, [])
