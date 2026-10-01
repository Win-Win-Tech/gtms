import tempfile
from datetime import date, timedelta
from django.contrib.auth import get_user_model
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from notifications.models import NotificationLog
from scheduler.models import Location
from visitor.models import Visitor, VisitorAsset, VisitorEntry, VisitorLookupOption
from visitor.contact_details import (
    entry_phone_number,
    entry_visitor_name,
    master_is_placeholder,
)
from visitor.lookup_options import get_valid_codes, get_options_for_location

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

    def test_qr_scan_verify_entry_and_completion(self):
        """
        Scan QR on visit date returns action verify_entry.
        Completing invite with guard field edits & uploaded assets transitions to pending_approval and notifies host.
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

        # Guard scans QR on visit date -> always returns verify_entry action
        self.client.force_authenticate(user=self.guard)
        scan_res = self.client.post("/visitors/qr-scan/", {"qr_token": qr_token}, format="json")
        self.assertEqual(scan_res.status_code, status.HTTP_200_OK)
        self.assertEqual(scan_res.data["action"], "verify_entry")
        self.assertEqual(scan_res.data["type"], "verify_entry")

        # Guard verifies & completes invite by editing details & uploading mandatory assets (photo & ID)
        photo = SimpleUploadedFile("vphoto.jpg", b"photo_content", content_type="image/jpeg")
        id_proof = SimpleUploadedFile("idproof.jpg", b"id_content", content_type="image/jpeg")
        complete_url = f"/visitors/entries/{entry_id}/complete-invite/"
        complete_data = {
            "visitor_photo": photo,
            "id_proof": id_proof,
            "vehicle_number": "MYPLATE123",
            "visitor_name": "VIP Visitor (Verified)",  # Guards can verify/update fields
        }
        complete_res = self.client.post(complete_url, complete_data, format="multipart")
        self.assertEqual(complete_res.status_code, status.HTTP_200_OK)
        self.assertEqual(complete_res.data["status"], "pending_approval")
        self.assertEqual(complete_res.data["visitor_name"], "VIP Visitor (Verified)")

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


class VisitorLookupOptionTestCase(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Lookup HQ", timezone="Asia/Kolkata")
        self.other_location = Location.objects.create(name="Other HQ", timezone="Asia/Kolkata")
        self.admin = User.objects.create_user(
            email="admin@example.com",
            password="Password123!",
            name="Admin User",
            location=self.location,
            role="admin",
        )
        self.guard = User.objects.create_user(
            email="guard2@example.com",
            password="Password123!",
            name="Guard User",
            location=self.location,
            role="guard",
        )

    def test_list_returns_seeded_org_options(self):
        self.client.force_authenticate(user=self.guard)
        res = self.client.get(
            f"/visitors/lookup-options/?location_id={self.location.id}&kind=visitor_type"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data.get("results", res.data)
        codes = {row["code"] for row in data}
        self.assertIn("guest", codes)

    def test_fallback_to_global_when_org_has_no_rows(self):
        VisitorLookupOption.objects.filter(location=self.other_location).delete()
        self.client.force_authenticate(user=self.admin)
        res = self.client.get(
            f"/visitors/lookup-options/?location_id={self.other_location.id}&kind=visitor_type"
        )
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        data = res.data.get("results", res.data)
        self.assertTrue(any(row["code"] == "guest" for row in data))

    def test_org_admin_can_create_custom_visitor_type(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            "/visitors/lookup-options/",
            {
                "kind": "visitor_type",
                "label": "Vendor",
                "sort_order": 10,
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        row = VisitorLookupOption.objects.get(code="vendor", location=self.location)
        self.assertFalse(row.is_default)

    def test_checkin_rejects_invalid_visitor_type(self):
        self.client.force_authenticate(user=self.admin)
        res = self.client.post(
            "/visitors/entries/checkin/",
            {
                "location_id": str(self.location.id),
                "ic_passport_number": "IC001",
                "visitor_name": "Test Visitor",
                "visitor_type": "not_a_real_type",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("Invalid visitor_type", res.data["error"])

    def test_cannot_delete_default_type(self):
        default_row = VisitorLookupOption.objects.filter(
            location=self.location,
            kind=VisitorLookupOption.KIND_VISITOR_TYPE,
            code="guest",
        ).first()
        self.assertIsNotNone(default_row)
        self.client.force_authenticate(user=self.admin)
        res = self.client.delete(f"/visitors/lookup-options/{default_row.id}/")
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_get_valid_codes_uses_org_rows(self):
        VisitorLookupOption.objects.create(
            kind=VisitorLookupOption.KIND_VISITOR_TYPE,
            code="vendor",
            label="Vendor",
            location=self.location,
            is_default=False,
            sort_order=99,
        )
        codes = get_valid_codes(self.location.id, VisitorLookupOption.KIND_VISITOR_TYPE)
        self.assertIn("vendor", codes)
        self.assertIn("guest", codes)

    def test_get_options_for_location_fallback(self):
        VisitorLookupOption.objects.filter(location=self.other_location).delete()
        qs = get_options_for_location(self.other_location.id, VisitorLookupOption.KIND_VEHICLE_TYPE)
        self.assertTrue(qs.filter(code="car", location__isnull=True).exists())


class EntryContactDetailsHelperTestCase(SimpleTestCase):
    """visitor.contact_details on unsaved objects (no database)."""

    def _visitor(self, name, phone="", plate="TN59BP9717"):
        return Visitor(ic_passport_number=f"CCTV-{plate}", visitor_name=name, phone_number=phone)

    def test_entry_values_fall_back_to_visitor(self):
        entry = VisitorEntry(visitor=self._visitor("Ravi", "9876543210"))
        self.assertEqual(entry_visitor_name(entry), "Ravi")
        self.assertEqual(entry_phone_number(entry), "9876543210")

    def test_entry_values_win_when_set(self):
        entry = VisitorEntry(
            visitor=self._visitor("Ravi", "9876543210"),
            visitor_name="Kumar",
            phone_number="9123456780",
        )
        self.assertEqual(entry_visitor_name(entry), "Kumar")
        self.assertEqual(entry_phone_number(entry), "9123456780")

    def test_blank_entry_values_use_visitor(self):
        entry = VisitorEntry(
            visitor=self._visitor("Ravi", "9876543210"),
            visitor_name="  ",
            phone_number="",
        )
        self.assertEqual(entry_visitor_name(entry), "Ravi")
        self.assertEqual(entry_phone_number(entry), "9876543210")

    def test_master_placeholder_rules(self):
        self.assertTrue(master_is_placeholder(self._visitor("TN59BP9717"), "TN59BP9717"))
        self.assertTrue(master_is_placeholder(self._visitor("TN 59 BP 9717"), ""))
        self.assertTrue(master_is_placeholder(self._visitor(""), "TN59BP9717"))
        # OCR variant on the entry: IC plate still matches the name
        self.assertTrue(master_is_placeholder(self._visitor("TN59BP9717"), "TN59BP9711"))
        self.assertFalse(master_is_placeholder(self._visitor("Ravi"), "TN59BP9717"))
        self.assertFalse(
            master_is_placeholder(self._visitor("TN59BP9717", "9876543210"), "TN59BP9717")
        )
        self.assertFalse(master_is_placeholder(None, "TN59BP9717"))


class EntryContactDetailsV5TestCase(SimpleTestCase):
    """v5 serializer + v5 contact-details save rules; v1 untouched (no database)."""

    def _entry(self, name, phone="", own_name="", own_phone="", plate="TN59BP9717"):
        visitor = Visitor(
            ic_passport_number=f"CCTV-{plate}", visitor_name=name, phone_number=phone
        )
        return VisitorEntry(
            visitor=visitor,
            entry_source=VisitorEntry.ENTRY_CCTV,
            vehicle_number=plate,
            visitor_name=own_name,
            phone_number=own_phone,
        )

    def test_v1_fields_unchanged_v5_adds_can_edit_details(self):
        from visitor.serializers import VisitorEntrySerializer
        from visitor.serializers_v5 import VisitorEntrySerializerV5

        v1 = list(VisitorEntrySerializer.Meta.fields)
        v5 = list(VisitorEntrySerializerV5.Meta.fields)
        self.assertNotIn("can_edit_details", v1)
        self.assertEqual([f for f in v5 if f != "can_edit_details"], v1)
        self.assertEqual(v5.index("can_edit_details"), v5.index("needs_details") + 1)
        v1_fields = VisitorEntrySerializer().fields
        self.assertEqual(v1_fields["visitor_name"].source, "visitor.visitor_name")
        self.assertEqual(v1_fields["phone_number"].source, "visitor.phone_number")

    def test_v5_serializer_values(self):
        from visitor.serializers import VisitorEntrySerializer
        from visitor.serializers_v5 import VisitorEntrySerializerV5

        s = VisitorEntrySerializerV5()
        edited = self._entry("Ravi", "9876543210", "Kumar", "9123456780")
        self.assertEqual(s.get_visitor_name(edited), "Kumar")
        self.assertEqual(s.get_phone_number(edited), "9123456780")
        self.assertFalse(s.get_needs_details(edited))
        self.assertTrue(s.get_can_edit_details(edited))

        fresh = self._entry("TN59BP9717")
        self.assertEqual(s.get_visitor_name(fresh), "TN59BP9717")
        self.assertTrue(s.get_needs_details(fresh))
        self.assertTrue(VisitorEntrySerializer().get_needs_details(fresh))

        manual = self._entry("Ravi", "9876543210")
        manual.entry_source = VisitorEntry.ENTRY_MANUAL
        self.assertFalse(s.get_can_edit_details(manual))

    def _save_v5(self, entry, name, phone):
        from unittest import mock

        from visitor.views_v5 import VisitorEntryContactDetailsViewV5

        with mock.patch.object(Visitor, "save") as visitor_save, mock.patch.object(
            VisitorEntry, "save"
        ) as entry_save:
            VisitorEntryContactDetailsViewV5()._apply_contact(entry, entry.visitor, name, phone)
        return visitor_save.called, entry_save.called

    def test_v5_first_save_updates_master(self):
        entry = self._entry("TN59BP9717")
        master_saved, entry_saved = self._save_v5(entry, "Ravi", "9876543210")
        self.assertTrue(master_saved)
        self.assertFalse(entry_saved)
        self.assertEqual(entry.visitor.visitor_name, "Ravi")
        self.assertEqual(entry.visitor.phone_number, "9876543210")
        self.assertEqual((entry.visitor_name, entry.phone_number), ("", ""))

    def test_v5_later_edit_updates_entry_only(self):
        entry = self._entry("Ravi", "9876543210")
        master_saved, entry_saved = self._save_v5(entry, "Kumar", "9123456780")
        self.assertFalse(master_saved)
        self.assertTrue(entry_saved)
        self.assertEqual(entry.visitor.visitor_name, "Ravi")
        self.assertEqual((entry.visitor_name, entry.phone_number), ("Kumar", "9123456780"))

    def test_v5_same_as_master_clears_entry(self):
        entry = self._entry("Ravi", "9876543210", "Kumar", "9123456780")
        master_saved, entry_saved = self._save_v5(entry, "Ravi", "9876543210")
        self.assertFalse(master_saved)
        self.assertTrue(entry_saved)
        self.assertEqual((entry.visitor_name, entry.phone_number), ("", ""))

    def test_v1_save_always_updates_master(self):
        from unittest import mock

        from visitor.views import VisitorEntryContactDetailsView

        entry = self._entry("Ravi", "9876543210")
        with mock.patch.object(Visitor, "save") as visitor_save, mock.patch.object(
            VisitorEntry, "save"
        ) as entry_save:
            VisitorEntryContactDetailsView()._apply_contact(
                entry, entry.visitor, "Kumar", "9123456780"
            )
        self.assertTrue(visitor_save.called)
        self.assertFalse(entry_save.called)
        self.assertEqual(entry.visitor.visitor_name, "Kumar")


class EntryContactDetailsReportsTestCase(SimpleTestCase):
    """Excel/PDF export row + vehicle overstay row with/without entry_contact (no database)."""

    def _entry(self, own_name="", own_phone=""):
        import pytz

        visitor = Visitor(
            ic_passport_number="CCTV-TN59BP9717", visitor_name="Ravi", phone_number="9876543210"
        )
        now = timezone.now()
        return VisitorEntry(
            visitor=visitor,
            location=Location(name="HQ"),
            entry_source=VisitorEntry.ENTRY_CCTV,
            vehicle_number="TN59BP9717",
            visitor_name=own_name,
            phone_number=own_phone,
            check_in_time=now - timedelta(hours=5),
            check_out_time=now,
        ), pytz.UTC

    def _export_row(self, entry, tz, entry_contact):
        from unittest import mock

        from visitor import exports

        with mock.patch.object(exports, "get_user_timezone_from_request", return_value=tz):
            return exports._entry_export_row(entry, None, entry_contact=entry_contact)

    def _overstay_row(self, entry, tz, entry_contact):
        from unittest import mock

        from visitor import vehicle_overstay_report as vor

        with mock.patch.object(vor, "_vehicle_type_label", return_value="Car"):
            return vor._row_from_entry(
                entry,
                location_id=None,
                user_tz=tz,
                wl_plates=set(),
                status="checked_out",
                status_label="Checked out",
                entry_contact=entry_contact,
            )

    def test_export_row(self):
        entry, tz = self._entry("Kumar", "9123456780")
        self.assertEqual(self._export_row(entry, tz, False)[2:4], ["Ravi", "9876543210"])
        self.assertEqual(self._export_row(entry, tz, True)[2:4], ["Kumar", "9123456780"])
        plain, tz = self._entry()
        self.assertEqual(self._export_row(plain, tz, True)[2:4], ["Ravi", "9876543210"])

    def test_overstay_row(self):
        entry, tz = self._entry("Kumar", "9123456780")
        v1 = self._overstay_row(entry, tz, False)
        v5 = self._overstay_row(entry, tz, True)
        self.assertEqual((v1["visitor_name"], v1["phone_number"]), ("Ravi", "9876543210"))
        self.assertEqual((v5["visitor_name"], v5["phone_number"]), ("Kumar", "9123456780"))
        self.assertEqual(set(v1), set(v5))

    def test_overstay_alert_uses_entry_name(self):
        from unittest import mock

        from notifications import services

        entry, _tz = self._entry("Kumar", "9123456780")
        with mock.patch.object(services, "notify_user", return_value=object()) as notify:
            self.assertEqual(services.notify_vehicle_overstay(entry, [object()]), 1)
        _user, _type, _title, body = notify.call_args.args[:4]
        self.assertIn("Visitor: Kumar.", body)
        self.assertEqual(notify.call_args.kwargs["data"]["visitor_name"], "Kumar")

        plain, _tz = self._entry()
        with mock.patch.object(services, "notify_user", return_value=object()) as notify:
            services.notify_vehicle_overstay(plain, [object()])
        self.assertIn("Visitor: Ravi.", notify.call_args.args[3])

    def test_scheduled_reports_use_entry_contact(self):
        from unittest import mock

        from django.http import HttpResponse

        from reports.services import report_generator as rg
        from visitor import exports, vehicle_overstay_report

        qs = mock.Mock()
        qs.exists.return_value = True
        qs.count.return_value = 1
        with mock.patch.object(rg, "_visitor_queryset", return_value=qs), mock.patch.object(
            rg, "_period_dates", return_value=("2026-10-01", "2026-10-01")
        ), mock.patch.object(
            exports, "generate_visitor_excel", return_value=HttpResponse(b"x")
        ) as excel, mock.patch.object(
            exports, "generate_visitor_pdf", return_value=(b"x", "v.pdf")
        ) as pdf:
            rg._visitor_generate("loc", "p", None, None, True, True, "Daily")
        self.assertTrue(excel.call_args.kwargs["entry_contact"])
        self.assertTrue(pdf.call_args.kwargs["entry_contact"])

        with mock.patch.object(
            rg, "_period_dates", return_value=("2026-10-01", "2026-10-01")
        ), mock.patch.object(
            vehicle_overstay_report, "build_vehicle_overstay_report", return_value={}
        ) as build:
            rg._vehicle_overstay_data("loc", "p", None, None)
        self.assertTrue(build.call_args.kwargs["entry_contact"])

    def test_overstay_row_hides_plate_as_name(self):
        entry, tz = self._entry()
        entry.visitor.visitor_name = "TN59BP9717"
        entry.visitor.phone_number = ""
        self.assertEqual(self._overstay_row(entry, tz, True)["visitor_name"], "")
        self.assertEqual(self._overstay_row(entry, tz, False)["visitor_name"], "")

