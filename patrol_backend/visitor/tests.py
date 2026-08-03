import tempfile
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from notifications.models import NotificationLog
from scheduler.models import Location
from visitor.models import Visitor, VisitorAsset, VisitorEntry

User = get_user_model()


@override_settings(MEDIA_ROOT=tempfile.gettempdir())
class VisitorInviteTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(
            name="HQ Site",
            timezone="Asia/Kuala_Lumpur",
        )
        self.host = User.objects.create_user(
            email="host@example.com",
            password="Password123!",
            name="Host User",
            location=self.location,
            role="client",
        )
        self.guard = User.objects.create_user(
            email="guard@example.com",
            password="Password123!",
            name="Guard User",
            location=self.location,
            role="security_guard",
        )
        self.today = timezone.now().date()

    def test_create_invite_success(self):
        """Invite creation with mandatory fields (visit_date) & optional assets."""
        self.client.force_authenticate(user=self.host)
        url = "/visitors/entries/invite/"
        data = {
            "location_id": str(self.location.id),
            "ic_passport_number": "INV123456",
            "visitor_name": "Invited Guest",
            "host_id": str(self.host.id),
            "visit_date": self.today.isoformat(),
            "visitor_type": "guest",
            "purpose_of_visit": "Official Discussion",
        }
        res = self.client.post(url, data, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["status"], "scheduled")
        self.assertEqual(res.data["entry_source"], "invitation")
        self.assertEqual(res.data["visit_date"], self.today.isoformat())

        # Assert no host notification was created on invite creation
        self.assertFalse(
            NotificationLog.objects.filter(
                recipient=self.host,
                notification_type=NotificationLog.TYPE_VISITOR_PENDING_APPROVAL,
            ).exists()
        )

    def test_qr_scan_missing_document_and_completion(self):
        """
        Scan QR on visit date when assets are missing returns type missing_document.
        Completing invite with uploaded assets transitions to pending_approval and notifies host.
        """
        self.client.force_authenticate(user=self.host)
        invite_url = "/visitors/entries/invite/"
        invite_data = {
            "location_id": str(self.location.id),
            "ic_passport_number": "INV999888",
            "visitor_name": "VIP Invited Visitor",
            "host_id": str(self.host.id),
            "visit_date": self.today.isoformat(),
        }
        res_invite = self.client.post(invite_url, invite_data, format="json")
        self.assertEqual(res_invite.status_code, status.HTTP_201_CREATED)
        entry_id = res_invite.data["id"]
        qr_token = res_invite.data["qr_token"]

        # Guard scans QR on visit date -> assets are missing
        self.client.force_authenticate(user=self.guard)
        scan_res = self.client.post("/visitors/qr-scan/", {"qr_token": qr_token}, format="json")
        self.assertEqual(scan_res.status_code, status.HTTP_200_OK)
        self.assertEqual(scan_res.data["action"], "missing_document")
        self.assertEqual(scan_res.data["type"], "missing_document")

        # Guard completes invite by uploading mandatory assets (photo & ID)
        photo = SimpleUploadedFile("vphoto.jpg", b"photo_content", content_type="image/jpeg")
        id_proof = SimpleUploadedFile("idproof.jpg", b"id_content", content_type="image/jpeg")
        complete_url = f"/visitors/entries/{entry_id}/complete-invite/"
        complete_data = {
            "visitor_photo": photo,
            "id_proof": id_proof,
            "vehicle_number": "MYPLATE123",
            "visitor_name": "Attempt Hacked Name",  # Should be ignored for non-host guard
        }
        complete_res = self.client.post(complete_url, complete_data, format="multipart")
        self.assertEqual(complete_res.status_code, status.HTTP_200_OK)
        self.assertEqual(complete_res.data["status"], "pending_approval")
        # Ensure host-filled visitor_name was immutable for guard
        self.assertEqual(complete_res.data["visitor_name"], "VIP Invited Visitor")

        # Verify host received pending_approval notification
        self.assertTrue(
            NotificationLog.objects.filter(
                recipient=self.host,
                notification_type=NotificationLog.TYPE_VISITOR_PENDING_APPROVAL,
            ).exists()
        )

    def test_host_approve_fails_without_assets(self):
        """Host approve fails with HTTP 400 if mandatory assets are missing."""
        self.client.force_authenticate(user=self.host)
        invite_url = "/visitors/entries/invite/"
        invite_data = {
            "location_id": str(self.location.id),
            "ic_passport_number": "NOASSETS01",
            "visitor_name": "No Asset Visitor",
            "host_id": str(self.host.id),
            "visit_date": self.today.isoformat(),
        }
        res_invite = self.client.post(invite_url, invite_data, format="json")
        entry_id = res_invite.data["id"]

        # Manually force status to pending_approval to test approve validation
        entry = VisitorEntry.objects.get(id=entry_id)
        entry.status = VisitorEntry.STATUS_PENDING_APPROVAL
        entry.save()

        approve_url = f"/visitors/entries/{entry_id}/approve/"
        approve_res = self.client.post(approve_url, {}, format="json")
        self.assertEqual(approve_res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Mandatory visitor photo or ID proof is missing", approve_res.data["error"])

    def test_reschedule_invite_without_assets(self):
        """Rescheduling invite to a future date works without assets."""
        self.client.force_authenticate(user=self.host)
        invite_url = "/visitors/entries/invite/"
        invite_data = {
            "location_id": str(self.location.id),
            "ic_passport_number": "RESCHED01",
            "visitor_name": "Rescheduled Visitor",
            "host_id": str(self.host.id),
            "visit_date": self.today.isoformat(),
        }
        res_invite = self.client.post(invite_url, invite_data, format="json")
        entry_id = res_invite.data["id"]

        future_date = self.today + timedelta(days=5)
        resched_url = f"/visitors/entries/{entry_id}/reschedule/"
        resched_data = {
            "expected_arrival_time": f"{future_date.isoformat()}T10:00:00",
            "expected_out_time": f"{future_date.isoformat()}T12:00:00",
            "visit_date": future_date.isoformat(),
        }
        resched_res = self.client.post(resched_url, resched_data, format="json")
        self.assertEqual(resched_res.status_code, status.HTTP_200_OK)
        self.assertEqual(resched_res.data["status"], "scheduled")
        self.assertEqual(resched_res.data["visit_date"], future_date.isoformat())
