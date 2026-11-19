from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APIClient

from authapp.models import Role
from authapp.serializers import UserSerializer
from notifications.models import NotificationLog
from scheduler.models import Location, LocationSite
from support.models import (
    SupportTicket,
    TicketActivityLog,
    TicketAssignment,
    TicketAttachment,
    TicketCategory,
    TicketComment,
)
from support.notifications import (
    TYPE_TICKET_ASSIGNED,
    TYPE_TICKET_CLOSED,
    TYPE_TICKET_COMMENT,
    TYPE_TICKET_CREATED,
    TYPE_TICKET_INVALIDATED,
    TYPE_TICKET_REOPENED,
    TYPE_TICKET_RESOLVED,
    TYPE_TICKET_SLA_BREACH,
)
from support.services import pickup_ticket, reopen_ticket
from support.sla import compute_due_at, sla_hours_for_priority

User = get_user_model()


class SupportTicketingPhase1Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Test Org")
        self.requester = User.objects.create_user(
            email="requester@example.com",
            password="Password123!",
            name="Requester",
            location=self.location,
            role="admin",
        )
        self.other_user = User.objects.create_user(
            email="other@example.com",
            password="Password123!",
            name="Other User",
            location=self.location,
            role="guard",
        )
        self.support_user = User.objects.create_user(
            email="support@example.com",
            password="Password123!",
            name="Support Agent",
            role="support",
        )
        self.super_admin = User.objects.create_superuser(
            email="superadmin@example.com",
            password="Password123!",
            name="Super Admin",
            role="superadmin",
        )
        # Seed migration should create these; ensure present for isolated DBs.
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        for name, sort_order in [
            ("Attendance / GPS", 10),
            ("Check-in / Check-out", 20),
            ("Live Tracking", 30),
            ("App / Login", 40),
            ("Other", 100),
        ]:
            TicketCategory.objects.get_or_create(
                name=name,
                defaults={"is_active": True, "sort_order": sort_order},
            )
        self.category = TicketCategory.objects.get(name="Other")

    def _create_ticket(self, user, subject="Need help"):
        self.client.force_authenticate(user=user)
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": subject,
                "description": "Something is broken in the app.",
                "category_id": str(self.category.id),
                "priority": "HIGH",
            },
            format="json",
        )
        return res

    def test_authenticated_user_can_create_ticket(self):
        res = self._create_ticket(self.requester, subject="GPS not updating")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED)
        self.assertEqual(res.data["status"], "success")
        data = res.data["data"]
        year = timezone.now().year
        self.assertRegex(data["ticket_number"], rf"^SP-{year}-\d{{5}}$")
        self.assertEqual(data["status"], SupportTicket.Status.OPEN)
        self.assertEqual(data["subject"], "GPS not updating")
        self.assertEqual(data["priority"], "HIGH")

        ticket = SupportTicket.objects.get(id=data["id"])
        self.assertEqual(ticket.created_by_id, self.requester.id)
        self.assertEqual(ticket.organisation_id, self.location.id)
        activity = TicketActivityLog.objects.filter(
            ticket=ticket, action="TICKET_CREATED"
        )
        self.assertTrue(activity.exists())
        self.assertTrue(activity.first().is_public)

    def test_requester_sees_my_list_support_sees_queue(self):
        create_res = self._create_ticket(self.requester, subject="My ticket")
        self.assertEqual(create_res.status_code, status.HTTP_201_CREATED)
        ticket_id = create_res.data["data"]["id"]

        # Requester my-list includes ticket
        self.client.force_authenticate(user=self.requester)
        my_res = self.client.get("/support/tickets/my/")
        self.assertEqual(my_res.status_code, status.HTTP_200_OK)
        my_ids = [t["id"] for t in my_res.data["data"]]
        self.assertIn(ticket_id, my_ids)

        # Other non-support user does not see requester's ticket on my-list
        self.client.force_authenticate(user=self.other_user)
        other_my = self.client.get("/support/tickets/my/")
        self.assertEqual(other_my.status_code, status.HTTP_200_OK)
        other_ids = [t["id"] for t in other_my.data["data"]]
        self.assertNotIn(ticket_id, other_ids)

        # SUPPORT role sees global queue
        self.client.force_authenticate(user=self.support_user)
        queue_res = self.client.get("/support/support/tickets/")
        self.assertEqual(queue_res.status_code, status.HTTP_200_OK)
        queue_ids = [t["id"] for t in queue_res.data["data"]]
        self.assertIn(ticket_id, queue_ids)

    def test_non_support_cannot_access_queue(self):
        self.client.force_authenticate(user=self.requester)
        res = self.client.get("/support/support/tickets/")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(res.data["status"], "error")

    def test_non_super_admin_cannot_assign_support_role(self):
        # Serializer-level guard (create)
        request = type("R", (), {"user": self.requester})()
        ser = UserSerializer(
            data={
                "email": "newsupport@example.com",
                "password": "Password123!",
                "name": "New Support",
                "role": "support",
            },
            context={"request": request},
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("role", ser.errors)

        # API create should not succeed for non–super admin
        self.client.force_authenticate(user=self.requester)
        api_res = self.client.post(
            "/auth/users/",
            {
                "email": "newsupport2@example.com",
                "password": "Password123!",
                "name": "New Support 2",
                "role": "support",
            },
            format="json",
        )
        self.assertNotEqual(api_res.status_code, status.HTTP_201_CREATED)
        self.assertFalse(User.objects.filter(email="newsupport2@example.com").exists())

        # Assign via update also blocked
        target = User.objects.create_user(
            email="to-promote@example.com",
            password="Password123!",
            name="To Promote",
            location=self.location,
            role="guard",
        )
        update_ser = UserSerializer(
            instance=target,
            data={"role": "support"},
            partial=True,
            context={"request": request},
        )
        self.assertFalse(update_ser.is_valid())
        self.assertIn("role", update_ser.errors)

        # Super Admin can assign
        sa_request = type("R", (), {"user": self.super_admin})()
        ok_ser = UserSerializer(
            data={
                "email": "allowed-support@example.com",
                "password": "Password123!",
                "name": "Allowed Support",
                "role": "support",
            },
            context={"request": sa_request},
        )
        self.assertTrue(ok_ser.is_valid(), ok_ser.errors)

    def test_super_admin_can_create_support_with_location_id_all(self):
        """SUPPORT is global-only: locationId=all / omitted → location null."""
        sa_request = type("R", (), {"user": self.super_admin})()
        cases = (
            {"locationId": "all"},
            {"locationId": "All"},
            {"locationId": ""},
            {"locationId": None},
            {},  # omitted
        )
        for idx, extra in enumerate(cases):
            data = {
                "email": f"support-global-{idx}@example.com",
                "password": "Password123!",
                "name": f"Support Global {idx}",
                "role": "support",
            }
            data.update(extra)
            ser = UserSerializer(data=data, context={"request": sa_request})
            self.assertTrue(ser.is_valid(), ser.errors)
            user = ser.save()
            self.assertIsNone(user.location_id)
            self.assertEqual(user.role, "support")

        # v5 API multipart-style create with locationId=all
        self.client.force_authenticate(user=self.super_admin)
        api_res = self.client.post(
            "/auth/v5/users/",
            {
                "email": "support-v5-all@example.com",
                "password": "Password123!",
                "name": "Support V5 All",
                "role": "support",
                "locationId": "all",
                "phone_no": "9999999999",
                "aadhar_no": "123412341234",
                "employee_code": "SUP-ALL-1",
            },
            format="multipart",
        )
        self.assertIn(api_res.status_code, (status.HTTP_200_OK, status.HTTP_201_CREATED), api_res.data)
        self.assertEqual(api_res.data.get("status"), "success", api_res.data)
        created = User.objects.get(email="support-v5-all@example.com")
        self.assertIsNone(created.location_id)
        self.assertEqual(created.role, "support")
        self.assertFalse(created.all_org_sites)
        self.assertIsNone((api_res.data.get("data") or {}).get("location"))

    def test_non_support_rejects_location_id_all(self):
        """Non-SUPPORT roles still require a real UUID when locationId is sent."""
        sa_request = type("R", (), {"user": self.super_admin})()
        ser = UserSerializer(
            data={
                "email": "guard-bad-loc@example.com",
                "password": "Password123!",
                "name": "Guard Bad Loc",
                "role": "guard",
                "locationId": "all",
            },
            context={"request": sa_request},
        )
        self.assertFalse(ser.is_valid())
        self.assertIn("locationId", ser.errors)

        # Valid UUID still works for normal roles
        ok = UserSerializer(
            data={
                "email": "guard-ok-loc@example.com",
                "password": "Password123!",
                "name": "Guard Ok Loc",
                "role": "guard",
                "locationId": str(self.location.id),
            },
            context={"request": sa_request},
        )
        self.assertTrue(ok.is_valid(), ok.errors)
        guard = ok.save()
        self.assertEqual(str(guard.location_id), str(self.location.id))

    def test_categories_list_returns_seeded_defaults(self):
        self.client.force_authenticate(user=self.requester)
        res = self.client.get("/support/categories/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        self.assertEqual(res.data["status"], "success")
        names = {c["name"] for c in res.data["data"]}
        expected = {
            "Attendance / GPS",
            "Check-in / Check-out",
            "Live Tracking",
            "App / Login",
            "Other",
        }
        self.assertTrue(expected.issubset(names))

    def test_ticket_detail_accessible_to_creator(self):
        create_res = self._create_ticket(self.requester)
        ticket_id = create_res.data["data"]["id"]
        self.client.force_authenticate(user=self.requester)
        detail = self.client.get(f"/support/tickets/{ticket_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(detail.data["data"]["id"], ticket_id)

        # Unrelated non-support user cannot view
        self.client.force_authenticate(user=self.other_user)
        denied = self.client.get(f"/support/tickets/{ticket_id}/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)


class SupportTicketingPhase2Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Phase2 Org")
        self.requester = User.objects.create_user(
            email="p2-requester@example.com",
            password="Password123!",
            name="P2 Requester",
            location=self.location,
            role="admin",
        )
        self.support_a = User.objects.create_user(
            email="support-a@example.com",
            password="Password123!",
            name="Support A",
            role="support",
        )
        self.support_b = User.objects.create_user(
            email="support-b@example.com",
            password="Password123!",
            name="Support B",
            role="support",
        )
        self.guard = User.objects.create_user(
            email="p2-guard@example.com",
            password="Password123!",
            name="P2 Guard",
            location=self.location,
            role="guard",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _create_ticket(self, user, subject="Phase2 ticket"):
        self.client.force_authenticate(user=user)
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": subject,
                "description": "Need support help.",
                "category_id": str(self.category.id),
                "priority": "MEDIUM",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data["data"]

    def test_pickup_claims_open_ticket(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        self.client.force_authenticate(user=self.support_a)
        pickup = self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.assertEqual(pickup.status_code, status.HTTP_200_OK, pickup.data)
        self.assertEqual(pickup.data["data"]["status"], SupportTicket.Status.IN_PROGRESS)
        self.assertEqual(str(pickup.data["data"]["current_assignee"]), str(self.support_a.id))

        ticket = SupportTicket.objects.get(id=ticket_id)
        self.assertEqual(ticket.status, SupportTicket.Status.IN_PROGRESS)
        self.assertTrue(
            TicketAssignment.objects.filter(
                ticket=ticket, action=TicketAssignment.Action.PICKUP
            ).exists()
        )
        self.assertTrue(
            TicketActivityLog.objects.filter(ticket=ticket, action="PICKED_UP").exists()
        )

        # Second agent cannot claim — conflict
        self.client.force_authenticate(user=self.support_b)
        conflict = self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.assertEqual(conflict.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(conflict.data["status"], "error")

    def test_non_support_cannot_pickup(self):
        data = self._create_ticket(self.requester)
        self.client.force_authenticate(user=self.requester)
        res = self.client.post(f"/support/tickets/{data['id']}/pickup/", {}, format="json")
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_assign_and_reassign_support_users(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        self.client.force_authenticate(user=self.support_a)
        assign = self.client.post(
            f"/support/tickets/{ticket_id}/assign/",
            {"assignee_id": str(self.support_b.id)},
            format="json",
        )
        self.assertEqual(assign.status_code, status.HTTP_200_OK, assign.data)
        self.assertEqual(assign.data["data"]["status"], SupportTicket.Status.IN_PROGRESS)
        self.assertEqual(str(assign.data["data"]["current_assignee"]), str(self.support_b.id))

        # Reassign requires comment
        reassign_fail = self.client.post(
            f"/support/tickets/{ticket_id}/assign/",
            {"assignee_id": str(self.support_a.id)},
            format="json",
        )
        self.assertEqual(reassign_fail.status_code, status.HTTP_400_BAD_REQUEST)

        reassign = self.client.post(
            f"/support/tickets/{ticket_id}/transfer/",
            {
                "assignee_id": str(self.support_a.id),
                "comment": "Handing back to Support A",
            },
            format="json",
        )
        self.assertEqual(reassign.status_code, status.HTTP_200_OK, reassign.data)
        self.assertEqual(str(reassign.data["data"]["current_assignee"]), str(self.support_a.id))

        # Cannot assign non-support user
        bad = self.client.post(
            f"/support/tickets/{ticket_id}/assign/",
            {
                "assignee_id": str(self.guard.id),
                "comment": "Nope",
            },
            format="json",
        )
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)

    def test_status_transitions_resolve_close_invalidate(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")

        # Resolve requires IN_PROGRESS + summary
        resolve_fail = self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {},
            format="json",
        )
        self.assertEqual(resolve_fail.status_code, status.HTTP_400_BAD_REQUEST)

        resolve = self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Fixed GPS refresh interval"},
            format="json",
        )
        self.assertEqual(resolve.status_code, status.HTTP_200_OK, resolve.data)
        self.assertEqual(resolve.data["data"]["status"], SupportTicket.Status.RESOLVED)
        self.assertTrue(resolve.data["data"]["resolved_at"])

        close = self.client.post(
            f"/support/tickets/{ticket_id}/close/",
            {"comment": "Confirmed with user"},
            format="json",
        )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.data)
        self.assertEqual(close.data["data"]["status"], SupportTicket.Status.CLOSED)
        self.assertTrue(close.data["data"]["closed_at"])

        # Invalidate path
        data2 = self._create_ticket(self.requester, subject="Bad ticket")
        ticket2 = data2["id"]
        self.client.force_authenticate(user=self.support_a)
        inv = self.client.post(
            f"/support/tickets/{ticket2}/invalidate/",
            {"reason": "Duplicate of SP ticket"},
            format="json",
        )
        self.assertEqual(inv.status_code, status.HTTP_200_OK, inv.data)
        self.assertEqual(inv.data["data"]["status"], SupportTicket.Status.INVALID)
        self.assertEqual(inv.data["data"]["invalidation_reason"], "Duplicate of SP ticket")

        # Creator sees invalidation reason
        self.client.force_authenticate(user=self.requester)
        detail = self.client.get(f"/support/tickets/{ticket2}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        self.assertEqual(
            detail.data["data"]["invalidation_reason"], "Duplicate of SP ticket"
        )

    def test_comments_public_vs_internal_visibility(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        self.client.force_authenticate(user=self.requester)
        pub = self.client.post(
            f"/support/tickets/{ticket_id}/comments/",
            {"body": "Still broken on my phone", "visibility": "PUBLIC"},
            format="json",
        )
        self.assertEqual(pub.status_code, status.HTTP_201_CREATED, pub.data)

        # Requester cannot create INTERNAL
        internal_denied = self.client.post(
            f"/support/tickets/{ticket_id}/comments/",
            {"body": "secret", "visibility": "INTERNAL"},
            format="json",
        )
        self.assertEqual(internal_denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.support_a)
        internal = self.client.post(
            f"/support/tickets/{ticket_id}/comments/",
            {"body": "Looks like device cache", "visibility": "INTERNAL"},
            format="json",
        )
        self.assertEqual(internal.status_code, status.HTTP_201_CREATED, internal.data)

        # Creator detail strips INTERNAL
        self.client.force_authenticate(user=self.requester)
        detail = self.client.get(f"/support/tickets/{ticket_id}/")
        bodies = [c["body"] for c in detail.data["data"]["comments"]]
        self.assertIn("Still broken on my phone", bodies)
        self.assertNotIn("Looks like device cache", bodies)
        activities = detail.data["data"]["activity"]
        self.assertTrue(all(a.get("is_public", True) for a in activities))

        # SUPPORT sees both
        self.client.force_authenticate(user=self.support_a)
        support_detail = self.client.get(f"/support/tickets/{ticket_id}/")
        support_bodies = [c["body"] for c in support_detail.data["data"]["comments"]]
        self.assertIn("Looks like device cache", support_bodies)
        self.assertEqual(
            TicketComment.objects.filter(
                ticket_id=ticket_id, visibility=TicketComment.Visibility.INTERNAL
            ).count(),
            1,
        )

    def test_support_agents_list(self):
        self.client.force_authenticate(user=self.support_a)
        res = self.client.get("/support/support/agents/")
        self.assertEqual(res.status_code, status.HTTP_200_OK)
        emails = {a["email"] for a in res.data["data"]}
        self.assertIn("support-a@example.com", emails)
        self.assertIn("support-b@example.com", emails)
        self.assertNotIn("p2-guard@example.com", emails)

        self.client.force_authenticate(user=self.requester)
        denied = self.client.get("/support/support/agents/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)


class SupportTicketingPhase3Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Phase3 Org")
        self.requester = User.objects.create_user(
            email="p3-requester@example.com",
            password="Password123!",
            name="P3 Requester",
            location=self.location,
            role="admin",
        )
        self.other_user = User.objects.create_user(
            email="p3-other@example.com",
            password="Password123!",
            name="P3 Other",
            location=self.location,
            role="guard",
        )
        self.support_a = User.objects.create_user(
            email="p3-support-a@example.com",
            password="Password123!",
            name="P3 Support A",
            role="support",
        )
        self.support_b = User.objects.create_user(
            email="p3-support-b@example.com",
            password="Password123!",
            name="P3 Support B",
            role="support",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _create_ticket(self, user, subject="Phase3 ticket"):
        self.client.force_authenticate(user=user)
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": subject,
                "description": "Need support help.",
                "category_id": str(self.category.id),
                "priority": "MEDIUM",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data["data"]

    def test_creator_activity_timeline_filters_internal(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        self.client.force_authenticate(user=self.support_a)
        self.client.post(
            f"/support/tickets/{ticket_id}/comments/",
            {"body": "Internal only", "visibility": "INTERNAL"},
            format="json",
        )
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Cleared cache"},
            format="json",
        )

        self.client.force_authenticate(user=self.requester)
        activity = self.client.get(f"/support/tickets/{ticket_id}/activity/")
        self.assertEqual(activity.status_code, status.HTTP_200_OK, activity.data)
        actions = [a["action"] for a in activity.data["data"]]
        self.assertIn("TICKET_CREATED", actions)
        self.assertIn("PICKED_UP", actions)
        self.assertIn("RESOLVED", actions)
        self.assertNotIn("COMMENT_ADDED", actions)  # internal comment activity
        self.assertTrue(all(a.get("is_public", True) for a in activity.data["data"]))

        # SUPPORT sees internal comment activity too
        self.client.force_authenticate(user=self.support_a)
        support_activity = self.client.get(f"/support/tickets/{ticket_id}/activity/")
        support_actions = [a["action"] for a in support_activity.data["data"]]
        self.assertIn("COMMENT_ADDED", support_actions)

        # Unrelated user denied
        self.client.force_authenticate(user=self.other_user)
        denied = self.client.get(f"/support/tickets/{ticket_id}/activity/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_resolve_close_create_notifications(self):
        data = self._create_ticket(self.requester, subject="Notify me")
        ticket_id = data["id"]

        # Create notifies SUPPORT agents
        created_logs = NotificationLog.objects.filter(
            type=TYPE_TICKET_CREATED, user=self.support_a
        )
        self.assertTrue(created_logs.exists())

        self.client.force_authenticate(user=self.support_a)
        pickup = self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.assertEqual(pickup.status_code, status.HTTP_200_OK, pickup.data)
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_ASSIGNED, user=self.requester
            ).exists()
        )

        resolve = self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Fixed on device"},
            format="json",
        )
        self.assertEqual(resolve.status_code, status.HTTP_200_OK, resolve.data)
        resolved_log = NotificationLog.objects.filter(
            type=TYPE_TICKET_RESOLVED, user=self.requester
        ).first()
        self.assertIsNotNone(resolved_log)
        self.assertIn("Fixed on device", resolved_log.body)
        self.assertEqual(resolved_log.data.get("ticket_id"), str(ticket_id))

        close = self.client.post(
            f"/support/tickets/{ticket_id}/close/",
            {"comment": "Done"},
            format="json",
        )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.data)
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_CLOSED, user=self.requester
            ).exists()
        )

        # Invalidate path
        data2 = self._create_ticket(self.requester, subject="Bad one")
        self.client.force_authenticate(user=self.support_a)
        inv = self.client.post(
            f"/support/tickets/{data2['id']}/invalidate/",
            {"reason": "Duplicate"},
            format="json",
        )
        self.assertEqual(inv.status_code, status.HTTP_200_OK, inv.data)
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_INVALIDATED, user=self.requester
            ).exists()
        )

    def test_public_comment_notifies_other_party(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]
        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")

        before = NotificationLog.objects.filter(
            type=TYPE_TICKET_COMMENT, user=self.requester
        ).count()
        self.client.post(
            f"/support/tickets/{ticket_id}/comments/",
            {"body": "Please try again", "visibility": "PUBLIC"},
            format="json",
        )
        after = NotificationLog.objects.filter(
            type=TYPE_TICKET_COMMENT, user=self.requester
        ).count()
        self.assertEqual(after, before + 1)

        # Internal notes do not notify
        before_int = NotificationLog.objects.filter(type=TYPE_TICKET_COMMENT).count()
        self.client.post(
            f"/support/tickets/{ticket_id}/comments/",
            {"body": "secret note", "visibility": "INTERNAL"},
            format="json",
        )
        self.assertEqual(
            NotificationLog.objects.filter(type=TYPE_TICKET_COMMENT).count(),
            before_int,
        )

    def test_attachment_upload_download_private(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        upload = SimpleUploadedFile(
            "screenshot.png",
            b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes",
            content_type="image/png",
        )
        self.client.force_authenticate(user=self.requester)
        res = self.client.post(
            f"/support/tickets/{ticket_id}/attachments/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        att = res.data["data"]
        self.assertEqual(att["file_name"], "screenshot.png")
        self.assertIn("/download/", att["download_url"])
        self.assertNotIn("file", att)  # no public MEDIA URL field
        self.assertTrue(
            TicketAttachment.objects.filter(id=att["id"], ticket_id=ticket_id).exists()
        )
        self.assertTrue(
            TicketActivityLog.objects.filter(
                ticket_id=ticket_id, action="ATTACHMENT_ADDED"
            ).exists()
        )

        # Creator can download
        dl = self.client.get(
            f"/support/tickets/{ticket_id}/attachments/{att['id']}/download/"
        )
        self.assertEqual(dl.status_code, status.HTTP_200_OK)
        self.assertTrue(dl.get("Content-Disposition", "").startswith("attachment"))

        # SUPPORT can download
        self.client.force_authenticate(user=self.support_a)
        dl2 = self.client.get(
            f"/support/tickets/{ticket_id}/attachments/{att['id']}/download/"
        )
        self.assertEqual(dl2.status_code, status.HTTP_200_OK)

        # Unrelated user cannot
        self.client.force_authenticate(user=self.other_user)
        denied = self.client.get(
            f"/support/tickets/{ticket_id}/attachments/{att['id']}/download/"
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        # Detail includes attachment metadata for creator
        self.client.force_authenticate(user=self.requester)
        detail = self.client.get(f"/support/tickets/{ticket_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK)
        names = [a["file_name"] for a in detail.data["data"]["attachments"]]
        self.assertIn("screenshot.png", names)

    def test_attachment_rejected_when_closed_or_bad_type(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        bad = SimpleUploadedFile(
            "malware.exe",
            b"MZ",
            content_type="application/octet-stream",
        )
        self.client.force_authenticate(user=self.requester)
        bad_res = self.client.post(
            f"/support/tickets/{ticket_id}/attachments/",
            {"file": bad},
            format="multipart",
        )
        self.assertEqual(bad_res.status_code, status.HTTP_400_BAD_REQUEST)

        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Done"},
            format="json",
        )
        self.client.post(f"/support/tickets/{ticket_id}/close/", {}, format="json")

        ok_file = SimpleUploadedFile(
            "note.txt", b"hello", content_type="text/plain"
        )
        self.client.force_authenticate(user=self.requester)
        closed = self.client.post(
            f"/support/tickets/{ticket_id}/attachments/",
            {"file": ok_file},
            format="multipart",
        )
        self.assertEqual(closed.status_code, status.HTTP_400_BAD_REQUEST)


class SupportTicketingPhase4Tests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Phase4 Org")
        self.location_b = Location.objects.create(name="Support Phase4 Org B")
        self.site = LocationSite.objects.create(
            location=self.location,
            name="Phase4 Site A",
            latitude=1.0,
            longitude=1.0,
            is_active=True,
        )
        self.requester = User.objects.create_user(
            email="p4-requester@example.com",
            password="Password123!",
            name="P4 Requester",
            location=self.location,
            role="admin",
        )
        self.other_user = User.objects.create_user(
            email="p4-other@example.com",
            password="Password123!",
            name="P4 Other",
            location=self.location,
            role="guard",
        )
        self.support_a = User.objects.create_user(
            email="p4-support-a@example.com",
            password="Password123!",
            name="P4 Support A",
            role="support",
        )
        self.support_b = User.objects.create_user(
            email="p4-support-b@example.com",
            password="Password123!",
            name="P4 Support B",
            role="support",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _create_ticket(self, user, subject="Phase4 ticket", site_id=None, priority="MEDIUM"):
        self.client.force_authenticate(user=user)
        payload = {
            "subject": subject,
            "description": "Need support help.",
            "category_id": str(self.category.id),
            "priority": priority,
        }
        if site_id:
            payload["site_id"] = str(site_id)
        res = self.client.post("/support/tickets/", payload, format="json")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data["data"]

    def test_dashboard_metrics_global_org_site(self):
        t1 = self._create_ticket(self.requester, subject="Open A", site_id=self.site.id)
        t2 = self._create_ticket(
            self.requester, subject="Open B", site_id=self.site.id, priority="HIGH"
        )
        # Ticket for second org (requester location is org A — create via ORM)
        SupportTicket.objects.create(
            ticket_number=f"SP-{timezone.now().year}-99991",
            subject="Org B ticket",
            description="Other org",
            category=self.category,
            priority=SupportTicket.Priority.LOW,
            status=SupportTicket.Status.OPEN,
            created_by=self.requester,
            organisation=self.location_b,
        )

        self.client.force_authenticate(user=self.support_a)
        pickup = self.client.post(f"/support/tickets/{t1['id']}/pickup/", {}, format="json")
        self.assertEqual(pickup.status_code, status.HTTP_200_OK, pickup.data)

        dash = self.client.get("/support/dashboard/")
        self.assertEqual(dash.status_code, status.HTTP_200_OK, dash.data)
        totals = dash.data["data"]["totals"]
        self.assertGreaterEqual(totals["all"], 3)
        self.assertGreaterEqual(totals["by_status"]["OPEN"], 2)
        self.assertGreaterEqual(totals["by_status"]["IN_PROGRESS"], 1)
        self.assertGreaterEqual(totals["by_priority"]["HIGH"], 1)
        self.assertGreaterEqual(totals["unassigned_open"], 1)
        self.assertGreaterEqual(totals["my_assigned_active"], 1)

        org_names = {o["organisation_name"] for o in dash.data["data"]["organisations"]}
        self.assertIn("Support Phase4 Org", org_names)
        self.assertIn("Support Phase4 Org B", org_names)

        site_names = {s["site_name"] for s in dash.data["data"]["sites"]}
        self.assertIn("Phase4 Site A", site_names)

        # Org filter
        filtered = self.client.get(
            "/support/dashboard/",
            {"organisation_id": str(self.location_b.id)},
        )
        self.assertEqual(filtered.status_code, status.HTTP_200_OK)
        self.assertEqual(filtered.data["data"]["totals"]["all"], 1)
        self.assertEqual(len(filtered.data["data"]["organisations"]), 1)

        # Non-support denied
        self.client.force_authenticate(user=self.requester)
        denied = self.client.get("/support/dashboard/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        # Unused t2 keeps lint/coverage of second create path
        self.assertTrue(t2["id"])

    def test_pagination_my_list_and_queue(self):
        for i in range(3):
            self._create_ticket(self.requester, subject=f"Page ticket {i}")

        self.client.force_authenticate(user=self.requester)
        page1 = self.client.get("/support/tickets/my/", {"page": 1, "page_size": 2})
        self.assertEqual(page1.status_code, status.HTTP_200_OK)
        self.assertEqual(len(page1.data["data"]), 2)
        self.assertEqual(page1.data["pagination"]["page"], 1)
        self.assertEqual(page1.data["pagination"]["page_size"], 2)
        self.assertGreaterEqual(page1.data["pagination"]["total"], 3)
        self.assertGreaterEqual(page1.data["pagination"]["total_pages"], 2)

        page2 = self.client.get("/support/tickets/my/", {"page": 2, "page_size": 2})
        self.assertEqual(page2.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(page2.data["data"]), 1)

        self.client.force_authenticate(user=self.support_a)
        queue = self.client.get(
            "/support/support/tickets/", {"page": 1, "page_size": 2}
        )
        self.assertEqual(queue.status_code, status.HTTP_200_OK)
        self.assertEqual(len(queue.data["data"]), 2)
        self.assertIn("pagination", queue.data)
        self.assertEqual(queue.data["pagination"]["page_size"], 2)

        report = self.client.get("/support/reports/", {"page": 1, "page_size": 2})
        self.assertEqual(report.status_code, status.HTTP_200_OK)
        self.assertEqual(len(report.data["data"]), 2)
        self.assertIn("pagination", report.data)
        self.assertEqual(report.data["pagination"]["page_size"], 2)
        self.assertGreaterEqual(report.data["pagination"]["total"], 3)

    def test_creator_reopen_resolved_not_closed(self):
        data = self._create_ticket(self.requester, subject="Reopen me")
        ticket_id = data["id"]

        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        resolve = self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Temporary fix"},
            format="json",
        )
        self.assertEqual(resolve.status_code, status.HTTP_200_OK, resolve.data)

        # Non-creator cannot reopen
        self.client.force_authenticate(user=self.other_user)
        denied = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {"comment": "Nope"},
            format="json",
        )
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        # Creator reopen → IN_PROGRESS (assignee retained)
        self.client.force_authenticate(user=self.requester)
        reopen = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {"comment": "Still broken"},
            format="json",
        )
        self.assertEqual(reopen.status_code, status.HTTP_200_OK, reopen.data)
        self.assertEqual(reopen.data["data"]["status"], SupportTicket.Status.IN_PROGRESS)
        self.assertIsNone(reopen.data["data"]["resolved_at"])
        self.assertTrue(
            TicketActivityLog.objects.filter(
                ticket_id=ticket_id, action="REOPENED"
            ).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_REOPENED, user=self.support_a
            ).exists()
        )

        # Close then reopen blocked
        self.client.force_authenticate(user=self.support_a)
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Fixed again"},
            format="json",
        )
        self.client.post(f"/support/tickets/{ticket_id}/close/", {}, format="json")
        self.client.force_authenticate(user=self.requester)
        closed_reopen = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {"comment": "Please"},
            format="json",
        )
        self.assertEqual(closed_reopen.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(
            closed_reopen.data["data"]["code"], "cannot_reopen_closed"
        )

    def test_concurrency_pickup_conflict_and_reopen_lock(self):
        """Row-lock path: second pickup conflicts; reopen rejects non-RESOLVED."""
        data = self._create_ticket(self.requester, subject="Concurrent")
        ticket_id = data["id"]

        pickup_ticket(ticket_id=ticket_id, actor=self.support_a)
        from support.services import TicketWorkflowError

        with self.assertRaises(TicketWorkflowError) as ctx:
            pickup_ticket(ticket_id=ticket_id, actor=self.support_b)
        self.assertEqual(ctx.exception.http_status, 409)
        self.assertEqual(ctx.exception.code, "already_assigned")

        # Resolve then reopen via service (locked path)
        self.client.force_authenticate(user=self.support_a)
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Done"},
            format="json",
        )
        ticket = reopen_ticket(
            ticket_id=ticket_id, actor=self.requester, comment="Again"
        )
        self.assertEqual(ticket.status, SupportTicket.Status.IN_PROGRESS)

        with self.assertRaises(TicketWorkflowError):
            reopen_ticket(ticket_id=ticket_id, actor=self.requester, comment="Twice")


class SupportTicketingPhase5MobileTests(TestCase):
    """Phase 5: Raise Ticket multipart + mobile FCM deep-link payload fields."""

    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Phase5 Org")
        self.site = LocationSite.objects.create(
            location=self.location,
            name="Phase5 Site A",
            latitude=1.0,
            longitude=1.0,
            is_active=True,
        )
        self.requester = User.objects.create_user(
            email="p5-requester@example.com",
            password="Password123!",
            name="P5 Requester",
            location=self.location,
            role="admin",
        )
        self.support_a = User.objects.create_user(
            email="p5-support-a@example.com",
            password="Password123!",
            name="P5 Support A",
            role="support",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def test_raise_ticket_multipart_with_optional_file(self):
        self.client.force_authenticate(user=self.requester)
        upload = SimpleUploadedFile(
            "screenshot.png",
            b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes",
            content_type="image/png",
        )
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": "Map blank",
                "description": "Blank after resume",
                "category_id": str(self.category.id),
                "priority": "HIGH",
                "site_id": str(self.site.id),
                "file": upload,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        data = res.data["data"]
        self.assertEqual(data["status"], SupportTicket.Status.OPEN)
        self.assertEqual(data["priority"], "HIGH")
        self.assertEqual(len(data["attachments"]), 1)
        self.assertEqual(data["attachments"][0]["file_name"], "screenshot.png")
        self.assertTrue(
            TicketActivityLog.objects.filter(
                ticket_id=data["id"], action="ATTACHMENT_ADDED"
            ).exists()
        )

    def test_raise_ticket_rejects_bad_attachment_without_orphan(self):
        before = SupportTicket.objects.count()
        self.client.force_authenticate(user=self.requester)
        bad = SimpleUploadedFile(
            "malware.exe",
            b"MZ-fake",
            content_type="application/octet-stream",
        )
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": "Should fail",
                "description": "Bad file type",
                "category_id": str(self.category.id),
                "file": bad,
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST, res.data)
        self.assertEqual(SupportTicket.objects.count(), before)

    def test_support_notification_payload_has_mobile_deeplink_fields(self):
        self.client.force_authenticate(user=self.requester)
        create = self.client.post(
            "/support/tickets/",
            {
                "subject": "Notify payload",
                "description": "Check deep link keys",
                "category_id": str(self.category.id),
            },
            format="json",
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.data)
        ticket_id = create.data["data"]["id"]

        log = NotificationLog.objects.filter(
            type=TYPE_TICKET_CREATED, user=self.support_a
        ).first()
        self.assertIsNotNone(log)
        self.assertEqual(log.data.get("click_action"), "FLUTTER_NOTIFICATION_CLICK")
        self.assertEqual(log.data.get("screen"), "support_ticket_detail")
        self.assertEqual(log.data.get("ticket_id"), str(ticket_id))
        self.assertEqual(
            log.data.get("route"), f"/help-support/tickets/{ticket_id}"
        )


class SupportTicketingPhase6MediaNotifyTests(TestCase):
    """Phase 6: multi-file create/close media + assign/close notify SUPPORT + creator."""

    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Phase6 Org")
        self.requester = User.objects.create_user(
            email="p6-requester@example.com",
            password="Password123!",
            name="P6 Requester",
            location=self.location,
            role="admin",
        )
        self.support_a = User.objects.create_user(
            email="p6-support-a@example.com",
            password="Password123!",
            name="P6 Support A",
            role="support",
        )
        self.support_b = User.objects.create_user(
            email="p6-support-b@example.com",
            password="Password123!",
            name="P6 Support B",
            role="support",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _png(self, name="shot.png"):
        return SimpleUploadedFile(
            name,
            b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes",
            content_type="image/png",
        )

    def _audio(self, name="note.mp3"):
        return SimpleUploadedFile(
            name,
            b"ID3fake-audio-bytes",
            content_type="audio/mpeg",
        )

    def test_create_ticket_multipart_assets_and_audio(self):
        self.client.force_authenticate(user=self.requester)
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": "Multi media",
                "description": "Photos and audio",
                "category_id": str(self.category.id),
                "assets": self._png("a1.png"),
                "photo": self._png("a2.png"),
                "audio": self._audio("voice.mp3"),
            },
            format="multipart",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        data = res.data["data"]
        atts = data["attachments"]
        self.assertEqual(len(atts), 3)
        kinds = {a["file_name"]: a["kind"] for a in atts}
        purposes = {a["purpose"] for a in atts}
        self.assertEqual(kinds["a1.png"], TicketAttachment.Kind.ASSET)
        self.assertEqual(kinds["a2.png"], TicketAttachment.Kind.ASSET)
        self.assertEqual(kinds["voice.mp3"], TicketAttachment.Kind.AUDIO)
        self.assertEqual(purposes, {TicketAttachment.Purpose.CREATE})

    def test_close_ticket_with_evidence_media(self):
        self.client.force_authenticate(user=self.requester)
        create = self.client.post(
            "/support/tickets/",
            {
                "subject": "Close media",
                "description": "Need evidence on close",
                "category_id": str(self.category.id),
            },
            format="json",
        )
        ticket_id = create.data["data"]["id"]
        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Fixed"},
            format="json",
        )
        close = self.client.post(
            f"/support/tickets/{ticket_id}/close/",
            {
                "comment": "Proof attached",
                "assets": self._png("proof.png"),
                "audio": self._audio("confirm.mp3"),
            },
            format="multipart",
        )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.data)
        self.assertEqual(close.data["data"]["status"], SupportTicket.Status.CLOSED)
        atts = close.data["data"]["attachments"]
        close_atts = [a for a in atts if a.get("purpose") == TicketAttachment.Purpose.CLOSE]
        self.assertEqual(len(close_atts), 2)
        kinds = {a["kind"] for a in close_atts}
        self.assertEqual(kinds, {TicketAttachment.Kind.ASSET, TicketAttachment.Kind.AUDIO})
        closed_log = TicketActivityLog.objects.filter(
            ticket_id=ticket_id, action="CLOSED"
        ).first()
        self.assertIsNotNone(closed_log)
        self.assertEqual(len(closed_log.metadata_json.get("close_attachment_ids") or []), 2)
        self.assertGreaterEqual(
            TicketActivityLog.objects.filter(
                ticket_id=ticket_id, action="ATTACHMENT_ADDED"
            ).count(),
            2,
        )

    def test_assign_notifies_creator_and_support(self):
        self.client.force_authenticate(user=self.requester)
        create = self.client.post(
            "/support/tickets/",
            {
                "subject": "Assign notify",
                "description": "Check recipients",
                "category_id": str(self.category.id),
            },
            format="json",
        )
        ticket_id = create.data["data"]["id"]
        NotificationLog.objects.filter(type=TYPE_TICKET_ASSIGNED).delete()

        self.client.force_authenticate(user=self.support_a)
        pickup = self.client.post(
            f"/support/tickets/{ticket_id}/pickup/", {}, format="json"
        )
        self.assertEqual(pickup.status_code, status.HTTP_200_OK, pickup.data)

        creator_logs = NotificationLog.objects.filter(
            type=TYPE_TICKET_ASSIGNED, user=self.requester
        )
        support_b_logs = NotificationLog.objects.filter(
            type=TYPE_TICKET_ASSIGNED, user=self.support_b
        )
        actor_logs = NotificationLog.objects.filter(
            type=TYPE_TICKET_ASSIGNED, user=self.support_a
        )
        self.assertTrue(creator_logs.exists())
        self.assertTrue(support_b_logs.exists())
        self.assertFalse(actor_logs.exists())  # actor skipped

    def test_close_notifies_creator_and_support(self):
        self.client.force_authenticate(user=self.requester)
        create = self.client.post(
            "/support/tickets/",
            {
                "subject": "Close notify",
                "description": "Check close recipients",
                "category_id": str(self.category.id),
            },
            format="json",
        )
        ticket_id = create.data["data"]["id"]
        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Done"},
            format="json",
        )
        NotificationLog.objects.filter(type=TYPE_TICKET_CLOSED).delete()

        close = self.client.post(
            f"/support/tickets/{ticket_id}/close/",
            {"comment": "Closing"},
            format="json",
        )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.data)
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_CLOSED, user=self.requester
            ).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_CLOSED, user=self.support_b
            ).exists()
        )
        self.assertFalse(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_CLOSED, user=self.support_a
            ).exists()
        )


class SupportTicketingPhase7SlaReopenEmailTests(TestCase):
    """Phase 7: SLA due dates, CLOSED/INVALID reopen, email channel."""

    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Support Phase7 Org")
        self.requester = User.objects.create_user(
            email="p7-requester@example.com",
            password="Password123!",
            name="P7 Requester",
            location=self.location,
            role="admin",
        )
        self.support_a = User.objects.create_user(
            email="p7-support-a@example.com",
            password="Password123!",
            name="P7 Support A",
            role="support",
        )
        self.support_b = User.objects.create_user(
            email="p7-support-b@example.com",
            password="Password123!",
            name="P7 Support B",
            role="support",
        )
        self.super_admin = User.objects.create_superuser(
            email="p7-super@example.com",
            password="Password123!",
            name="P7 Super",
            role="superadmin",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _create(self, user, **extra):
        self.client.force_authenticate(user=user)
        payload = {
            "subject": extra.pop("subject", "Phase7 ticket"),
            "description": extra.pop("description", "Need SLA help"),
            "category_id": str(self.category.id),
            "priority": extra.pop("priority", "HIGH"),
        }
        payload.update(extra)
        return self.client.post("/support/tickets/", payload, format="json")

    def test_create_sets_due_at_from_priority(self):
        before = timezone.now()
        res = self._create(self.requester, priority="HIGH")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        data = res.data["data"]
        self.assertIsNotNone(data.get("due_at"))
        self.assertFalse(data.get("is_overdue"))
        self.assertEqual(data.get("sla_hours"), sla_hours_for_priority("HIGH"))
        ticket = SupportTicket.objects.get(id=data["id"])
        expected = compute_due_at(priority="HIGH", from_dt=before)
        # due_at within a few seconds of expected window
        self.assertAlmostEqual(
            ticket.due_at.timestamp(),
            expected.timestamp(),
            delta=5,
        )

    def test_explicit_due_at_and_overdue_flag(self):
        past = timezone.now() - timedelta(hours=1)
        res = self._create(
            self.requester,
            subject="Already overdue",
            due_at=past.isoformat(),
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        data = res.data["data"]
        self.assertTrue(data.get("is_overdue"))

        self.client.force_authenticate(user=self.support_a)
        queue = self.client.get("/support/support/tickets/", {"overdue": "true"})
        self.assertEqual(queue.status_code, status.HTTP_200_OK)
        ids = {row["id"] for row in queue.data["data"]}
        self.assertIn(data["id"], ids)

        dash = self.client.get("/support/dashboard/")
        self.assertEqual(dash.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(dash.data["data"]["totals"]["overdue_active"], 1)
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_SLA_BREACH, user=self.support_a
            ).exists()
            or NotificationLog.objects.filter(
                type=TYPE_TICKET_SLA_BREACH, user=self.support_b
            ).exists()
        )

    def test_support_reopen_closed_and_invalid(self):
        res = self._create(self.requester, subject="Reopen closed")
        ticket_id = res.data["data"]["id"]
        self.client.force_authenticate(user=self.support_a)
        self.client.post(f"/support/tickets/{ticket_id}/pickup/", {}, format="json")
        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Fixed"},
            format="json",
        )
        close = self.client.post(
            f"/support/tickets/{ticket_id}/close/",
            {"comment": "Done"},
            format="json",
        )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.data)

        # Creator still blocked
        self.client.force_authenticate(user=self.requester)
        denied = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {"comment": "Please"},
            format="json",
        )
        self.assertEqual(denied.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(denied.data["data"]["code"], "cannot_reopen_closed")

        # SUPPORT needs comment
        self.client.force_authenticate(user=self.support_a)
        missing = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {},
            format="json",
        )
        self.assertEqual(missing.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(missing.data["data"]["code"], "comment_required")

        reopen = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {"comment": "Customer called back", "target_status": "OPEN"},
            format="json",
        )
        self.assertEqual(reopen.status_code, status.HTTP_200_OK, reopen.data)
        self.assertEqual(reopen.data["data"]["status"], SupportTicket.Status.OPEN)
        self.assertIsNone(reopen.data["data"]["closed_at"])
        self.assertTrue(
            TicketActivityLog.objects.filter(
                ticket_id=ticket_id, action="REOPENED"
            ).exists()
        )
        self.assertTrue(
            NotificationLog.objects.filter(
                type=TYPE_TICKET_REOPENED, user=self.requester
            ).exists()
        )

        # Fresh INVALID (unassigned) then Super Admin reopen → IN_PROGRESS + claim
        inv_res = self._create(self.requester, subject="Invalidate me")
        inv_id = inv_res.data["data"]["id"]
        self.client.force_authenticate(user=self.support_a)
        inv = self.client.post(
            f"/support/tickets/{inv_id}/invalidate/",
            {"reason": "Duplicate"},
            format="json",
        )
        self.assertEqual(inv.status_code, status.HTTP_200_OK, inv.data)
        self.client.force_authenticate(user=self.super_admin)
        reopen_inv = self.client.post(
            f"/support/tickets/{inv_id}/reopen/",
            {"comment": "Not a duplicate", "target_status": "IN_PROGRESS"},
            format="json",
        )
        self.assertEqual(reopen_inv.status_code, status.HTTP_200_OK, reopen_inv.data)
        self.assertEqual(
            reopen_inv.data["data"]["status"], SupportTicket.Status.IN_PROGRESS
        )
        self.assertEqual(
            str(reopen_inv.data["data"]["current_assignee"]), str(self.super_admin.id)
        )

    def test_email_sent_on_create_assign_close_reopen(self):
        mail.outbox.clear()
        res = self._create(self.requester, subject="Email channel")
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        ticket_id = res.data["data"]["id"]
        create_subjects = [m.subject for m in mail.outbox]
        self.assertTrue(
            any("New support ticket" in s for s in create_subjects),
            create_subjects,
        )

        mail.outbox.clear()
        self.client.force_authenticate(user=self.support_a)
        pickup = self.client.post(
            f"/support/tickets/{ticket_id}/pickup/", {}, format="json"
        )
        self.assertEqual(pickup.status_code, status.HTTP_200_OK, pickup.data)
        self.assertTrue(
            any("assigned" in (m.subject or "").lower() for m in mail.outbox),
            [m.subject for m in mail.outbox],
        )

        self.client.post(
            f"/support/tickets/{ticket_id}/resolve/",
            {"summary": "Done"},
            format="json",
        )
        mail.outbox.clear()
        close = self.client.post(
            f"/support/tickets/{ticket_id}/close/",
            {"comment": "Closing"},
            format="json",
        )
        self.assertEqual(close.status_code, status.HTTP_200_OK, close.data)
        self.assertTrue(
            any("closed" in (m.subject or "").lower() for m in mail.outbox),
            [m.subject for m in mail.outbox],
        )

        mail.outbox.clear()
        reopen = self.client.post(
            f"/support/tickets/{ticket_id}/reopen/",
            {"comment": "Reopen for email test"},
            format="json",
        )
        self.assertEqual(reopen.status_code, status.HTTP_200_OK, reopen.data)
        self.assertTrue(
            any("reopened" in (m.subject or "").lower() for m in mail.outbox),
            [m.subject for m in mail.outbox],
        )


class SupportTicketingEnhancementsTests(TestCase):
    """Attachment inline MIME, reports/exports, support-user CRUD, org/site filters."""

    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Enhance Org A")
        self.location_b = Location.objects.create(name="Enhance Org B")
        self.site = LocationSite.objects.create(
            location=self.location,
            name="Enhance Site A",
            latitude=1.0,
            longitude=1.0,
            is_active=True,
        )
        self.requester = User.objects.create_user(
            email="enh-req@example.com",
            password="Password123!",
            name="Enh Requester",
            location=self.location,
            role="admin",
        )
        self.support_user = User.objects.create_user(
            email="enh-support@example.com",
            password="Password123!",
            name="Enh Support",
            role="support",
            location=None,
        )
        self.super_admin = User.objects.create_superuser(
            email="enh-super@example.com",
            password="Password123!",
            name="Enh Super",
            role="superadmin",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _create_ticket(self, user, subject="Enhance ticket", **extra):
        self.client.force_authenticate(user=user)
        payload = {
            "subject": subject,
            "description": "Enhance flow",
            "category_id": str(self.category.id),
            "priority": "HIGH",
            **extra,
        }
        return self.client.post("/support/tickets/", payload, format="json")

    def test_attachment_inline_guesses_png_content_type(self):
        res = self._create_ticket(self.requester)
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        ticket_id = res.data["data"]["id"]
        # Browser often sends octet-stream; mime must still resolve for preview.
        upload = SimpleUploadedFile(
            "visitor-pass-Xxx.png",
            b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes",
            content_type="application/octet-stream",
        )
        up = self.client.post(
            f"/support/tickets/{ticket_id}/attachments/",
            {"file": upload},
            format="multipart",
        )
        self.assertEqual(up.status_code, status.HTTP_201_CREATED, up.data)
        att = up.data["data"]
        self.assertEqual(att["mime_type"], "image/png")

        dl = self.client.get(
            f"/support/tickets/{ticket_id}/attachments/{att['id']}/download/"
        )
        self.assertEqual(dl.status_code, status.HTTP_200_OK)
        self.assertTrue(dl.get("Content-Disposition", "").startswith("attachment"))
        self.assertIn("image/png", (dl.get("Content-Type") or "").lower())

        inline = self.client.get(
            f"/support/tickets/{ticket_id}/attachments/{att['id']}/download/",
            {"inline": "1"},
        )
        self.assertEqual(inline.status_code, status.HTTP_200_OK)
        self.assertTrue(inline.get("Content-Disposition", "").startswith("inline"))
        self.assertIn("image/png", (inline.get("Content-Type") or "").lower())

    def test_queue_and_dashboard_filter_by_org_site(self):
        t1 = self._create_ticket(
            self.requester, subject="Org A", site_id=str(self.site.id)
        )
        self.assertEqual(t1.status_code, status.HTTP_201_CREATED, t1.data)
        # Ticket in org B
        req_b = User.objects.create_user(
            email="enh-req-b@example.com",
            password="Password123!",
            name="Req B",
            location=self.location_b,
            role="admin",
        )
        t2 = self._create_ticket(req_b, subject="Org B")
        self.assertEqual(t2.status_code, status.HTTP_201_CREATED, t2.data)

        self.client.force_authenticate(user=self.support_user)
        all_q = self.client.get("/support/support/tickets/")
        self.assertEqual(all_q.status_code, status.HTTP_200_OK)
        self.assertGreaterEqual(len(all_q.data["data"]), 2)

        org_q = self.client.get(
            "/support/support/tickets/",
            {"organisation_id": str(self.location.id)},
        )
        subjects = {row["subject"] for row in org_q.data["data"]}
        self.assertIn("Org A", subjects)
        self.assertNotIn("Org B", subjects)

        site_q = self.client.get(
            "/support/support/tickets/",
            {"organisation_id": str(self.location.id), "site_id": str(self.site.id)},
        )
        self.assertTrue(all(row["site_name"] == "Enhance Site A" for row in site_q.data["data"]))

        dash = self.client.get(
            "/support/dashboard/",
            {"organisation_id": str(self.location.id)},
        )
        self.assertEqual(dash.status_code, status.HTTP_200_OK, dash.data)
        self.assertEqual(
            dash.data["data"]["filters"]["organisation_id"], str(self.location.id)
        )

    def test_reports_and_exports(self):
        created = self._create_ticket(self.requester, subject="Report me")
        self.assertEqual(created.status_code, status.HTTP_201_CREATED, created.data)

        self.client.force_authenticate(user=self.support_user)
        report = self.client.get("/support/reports/", {"status": "OPEN"})
        self.assertEqual(report.status_code, status.HTTP_200_OK, report.data)
        self.assertTrue(any(r["subject"] == "Report me" for r in report.data["data"]))

        xlsx = self.client.get("/support/reports/export-excel/")
        self.assertEqual(xlsx.status_code, status.HTTP_200_OK)
        self.assertIn(
            "spreadsheetml",
            (xlsx.get("Content-Type") or "").lower(),
        )
        self.assertTrue(xlsx.get("Content-Disposition", "").startswith("attachment"))

        pdf = self.client.get("/support/reports/export-pdf/")
        self.assertEqual(pdf.status_code, status.HTTP_200_OK)
        self.assertIn("pdf", (pdf.get("Content-Type") or "").lower())

        # Non-support denied
        self.client.force_authenticate(user=self.requester)
        denied = self.client.get("/support/reports/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

    def test_support_users_crud_super_admin_only(self):
        self.client.force_authenticate(user=self.support_user)
        denied = self.client.get("/support/support-users/")
        self.assertEqual(denied.status_code, status.HTTP_403_FORBIDDEN)

        self.client.force_authenticate(user=self.super_admin)
        create = self.client.post(
            "/support/support-users/",
            {
                "name": "New Agent",
                "email": "new-agent@example.com",
                "password": "Password123!",
            },
            format="json",
        )
        self.assertEqual(create.status_code, status.HTTP_201_CREATED, create.data)
        data = create.data["data"]
        self.assertEqual(data["role"], "support")
        self.assertTrue(data["is_active"])
        user = User.objects.get(id=data["id"])
        self.assertIsNone(user.location_id)
        self.assertFalse(user.all_org_sites)

        listed = self.client.get("/support/support-users/")
        self.assertEqual(listed.status_code, status.HTTP_200_OK)
        self.assertIn("pagination", listed.data)
        emails = {u["email"] for u in listed.data["data"]}
        self.assertIn("new-agent@example.com", emails)

        paged = self.client.get(
            "/support/support-users/", {"page": 1, "page_size": 1}
        )
        self.assertEqual(paged.status_code, status.HTTP_200_OK)
        self.assertEqual(len(paged.data["data"]), 1)
        self.assertEqual(paged.data["pagination"]["page_size"], 1)
        self.assertGreaterEqual(paged.data["pagination"]["total"], 1)

        patch = self.client.patch(
            f"/support/support-users/{data['id']}/",
            {"name": "Renamed Agent", "is_active": False},
            format="json",
        )
        self.assertEqual(patch.status_code, status.HTTP_200_OK, patch.data)
        self.assertEqual(patch.data["data"]["name"], "Renamed Agent")
        self.assertFalse(patch.data["data"]["is_active"])

        # SUPPORT agent picker still excludes inactive
        self.client.force_authenticate(user=self.support_user)
        agents = self.client.get("/support/support/agents/")
        self.assertEqual(agents.status_code, status.HTTP_200_OK)
        agent_emails = {a["email"] for a in agents.data["data"]}
        self.assertNotIn("new-agent@example.com", agent_emails)

    def test_dashboard_and_queue_ignore_null_scope_params(self):
        self.client.force_authenticate(user=self.support_user)
        dash = self.client.get(
            "/support/dashboard/",
            {"organisation_id": "null", "location_id": "null", "site_id": "null"},
        )
        self.assertEqual(dash.status_code, status.HTTP_200_OK, dash.data)
        self.assertIn("totals", dash.data["data"])

        queue = self.client.get(
            "/support/support/tickets/",
            {"organisation_id": "null", "site_id": "all"},
        )
        self.assertEqual(queue.status_code, status.HTTP_200_OK, queue.data)


class SupportTicketActionsApiTests(TestCase):
    """Combined POST /support/tickets/<id>/actions/ + detail durations enrichment."""

    def setUp(self):
        self.client = APIClient()
        self.location = Location.objects.create(name="Actions API Org")
        self.requester = User.objects.create_user(
            email="actions-requester@example.com",
            password="Password123!",
            name="Actions Requester",
            location=self.location,
            role="admin",
        )
        self.support_a = User.objects.create_user(
            email="actions-support-a@example.com",
            password="Password123!",
            name="Actions Support A",
            role="support",
        )
        self.support_b = User.objects.create_user(
            email="actions-support-b@example.com",
            password="Password123!",
            name="Actions Support B",
            role="support",
        )
        Role.objects.get_or_create(
            name="support",
            location=None,
            defaults={
                "is_default": True,
                "is_allow_webapp": True,
                "is_allow_edit": True,
                "is_allow_create": True,
                "pages": ["Support Tickets"],
            },
        )
        self.category, _ = TicketCategory.objects.get_or_create(
            name="Other",
            defaults={"is_active": True, "sort_order": 100},
        )

    def _create_ticket(self, user, subject="Actions ticket"):
        self.client.force_authenticate(user=user)
        res = self.client.post(
            "/support/tickets/",
            {
                "subject": subject,
                "description": "Need help via actions API.",
                "category_id": str(self.category.id),
                "priority": "HIGH",
            },
            format="json",
        )
        self.assertEqual(res.status_code, status.HTTP_201_CREATED, res.data)
        return res.data["data"]

    def _actions(self, ticket_id, payload, user=None):
        if user is not None:
            self.client.force_authenticate(user=user)
        return self.client.post(
            f"/support/tickets/{ticket_id}/actions/",
            payload,
            format="json",
        )

    def test_actions_requires_action_field(self):
        data = self._create_ticket(self.requester)
        res = self._actions(data["id"], {}, user=self.support_a)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(res.data["status"], "error")

    def test_actions_unknown_action(self):
        data = self._create_ticket(self.requester)
        res = self._actions(data["id"], {"action": "explode"}, user=self.support_a)
        self.assertEqual(res.status_code, status.HTTP_400_BAD_REQUEST)

    def test_actions_pickup_assign_resolve_comment_reopen(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]

        pickup = self._actions(ticket_id, {"action": "pickup"}, user=self.support_a)
        self.assertEqual(pickup.status_code, status.HTTP_200_OK, pickup.data)
        self.assertEqual(pickup.data["data"]["status"], SupportTicket.Status.IN_PROGRESS)
        self.assertEqual(
            str(pickup.data["data"]["current_assignee"]), str(self.support_a.id)
        )
        self.assertIn("durations", pickup.data["data"])
        self.assertIn("activity", pickup.data["data"])

        assign = self._actions(
            ticket_id,
            {
                "action": "assign",
                "assignee_id": str(self.support_b.id),
                "comment": "Transferring ownership",
            },
            user=self.support_a,
        )
        self.assertEqual(assign.status_code, status.HTTP_200_OK, assign.data)
        self.assertEqual(
            str(assign.data["data"]["current_assignee"]), str(self.support_b.id)
        )

        resolve = self._actions(
            ticket_id,
            {"action": "resolve", "resolution": "Fixed via actions API"},
            user=self.support_b,
        )
        self.assertEqual(resolve.status_code, status.HTTP_200_OK, resolve.data)
        self.assertEqual(resolve.data["data"]["status"], SupportTicket.Status.RESOLVED)
        self.assertTrue(resolve.data["data"]["resolved_at"])
        self.assertIsNotNone(resolve.data["data"]["durations"])

        comment = self._actions(
            ticket_id,
            {
                "action": "comment",
                "body": "Thanks — confirming",
                "visibility": "PUBLIC",
            },
            user=self.requester,
        )
        # Comment blocked on RESOLVED? Check - TicketCommentCreateView blocks CLOSED/INVALID only
        self.assertEqual(comment.status_code, status.HTTP_201_CREATED, comment.data)
        self.assertEqual(comment.data["data"]["id"], ticket_id)
        bodies = [c["body"] for c in comment.data["data"]["comments"]]
        self.assertIn("Thanks — confirming", bodies)

        reopen = self._actions(
            ticket_id,
            {"action": "reopen", "comment": "Still broken"},
            user=self.requester,
        )
        self.assertEqual(reopen.status_code, status.HTTP_200_OK, reopen.data)
        self.assertEqual(reopen.data["data"]["status"], SupportTicket.Status.IN_PROGRESS)

    def test_actions_invalidate(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]
        inv = self._actions(
            ticket_id,
            {"action": "invalidate", "reason": "Duplicate"},
            user=self.support_a,
        )
        self.assertEqual(inv.status_code, status.HTTP_200_OK, inv.data)
        self.assertEqual(inv.data["data"]["status"], SupportTicket.Status.INVALID)

    def test_non_support_cannot_pickup_via_actions(self):
        data = self._create_ticket(self.requester)
        res = self._actions(data["id"], {"action": "pickup"}, user=self.requester)
        self.assertEqual(res.status_code, status.HTTP_403_FORBIDDEN)

    def test_detail_includes_activity_and_durations(self):
        data = self._create_ticket(self.requester)
        ticket_id = data["id"]
        self._actions(ticket_id, {"action": "pickup"}, user=self.support_a)

        self.client.force_authenticate(user=self.requester)
        detail = self.client.get(f"/support/tickets/{ticket_id}/")
        self.assertEqual(detail.status_code, status.HTTP_200_OK, detail.data)
        payload = detail.data["data"]
        self.assertIn("activity", payload)
        self.assertTrue(isinstance(payload["activity"], list))
        self.assertTrue(
            any(a["action"] == "PICKED_UP" for a in payload["activity"]),
            payload["activity"],
        )
        self.assertIn("durations", payload)
        durations = payload["durations"]
        self.assertIn("segments", durations)
        self.assertIn("total", durations)
        self.assertIn("open_to_assign", durations)
