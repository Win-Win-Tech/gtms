"""
Scenario tests for list_default_shifts (site + in-progress / ±1h) and ShiftSerializer.

Uses live DB inside an atomic block that always rolls back (no test_* DB required).
Run:
  python manage.py shell < dashboard/run_list_default_shifts_site_time_scenarios.py
or:
  python dashboard/run_list_default_shifts_site_time_scenarios.py
"""
from datetime import date, datetime, time
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytz
from django.db import transaction
from rest_framework import status
from rest_framework.test import APIRequestFactory, force_authenticate

from authapp.models import User
from dashboard.views import AttendanceCheckinViewSet
from scheduler.models import GuardSiteCache, Location, LocationSite, Shift
from scheduler.serializers import ShiftSerializer


TZ = pytz.timezone("Asia/Kolkata")
ON_DATE = date(2026, 11, 14)


def _aware(h, m=0):
    return TZ.localize(datetime(2026, 11, 14, h, m, 0))


class _Result:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.rows = []

    def ok(self, name, detail=""):
        self.passed += 1
        self.rows.append(("PASS", name, detail))
        print(f"  PASS  {name}" + (f" — {detail}" if detail else ""))

    def fail(self, name, detail=""):
        self.failed += 1
        self.rows.append(("FAIL", name, detail))
        print(f"  FAIL  {name} — {detail}")

    def check(self, name, cond, detail=""):
        if cond:
            self.ok(name, detail)
        else:
            self.fail(name, detail or "assertion failed")


def run_scenarios():
    result = _Result()
    factory = APIRequestFactory()
    view = AttendanceCheckinViewSet.as_view({"get": "list_default_shifts"})

    print("\n=== ShiftSerializer scenarios ===")
    with transaction.atomic():
        loc = Location.objects.create(name=f"T-Org-{uuid4().hex[:8]}")
        site_a = LocationSite.objects.create(
            location=loc, name="Site A", latitude=1.0, longitude=1.0
        )
        site_b = LocationSite.objects.create(
            location=loc, name="Site B", latitude=2.0, longitude=2.0
        )
        other_loc = Location.objects.create(name=f"T-Other-{uuid4().hex[:8]}")
        other_site = LocationSite.objects.create(
            location=other_loc, name="Other", latitude=3.0, longitude=3.0
        )

        # all sites clears M2M
        shift = Shift.objects.create(
            name="Day",
            location=loc,
            start_time=time(8, 0),
            end_time=time(20, 0),
            is_default=True,
            applies_to_all_sites=False,
        )
        shift.sites.set([site_a])
        ser = ShiftSerializer(
            shift,
            data={
                "name": "Day",
                "location": str(loc.id),
                "start_time": "08:00 AM",
                "end_time": "08:00 PM",
                "is_default": True,
                "applies_to_all_sites": True,
                "site_ids": [],
            },
            partial=True,
        )
        result.check(
            "serializer: all_sites valid",
            ser.is_valid(),
            str(ser.errors) if not ser.is_valid() else "",
        )
        if ser.is_valid():
            obj = ser.save()
            result.check("serializer: all_sites clears M2M", obj.applies_to_all_sites and obj.sites.count() == 0)

        ser_bad = ShiftSerializer(
            data={
                "name": "Day",
                "location": str(loc.id),
                "start_time": "08:00 AM",
                "end_time": "08:00 PM",
                "is_default": True,
                "applies_to_all_sites": False,
                "site_ids": [],
            }
        )
        result.check(
            "serializer: specific sites requires ≥1",
            (not ser_bad.is_valid()) and ("site_ids" in ser_bad.errors),
        )

        ser_ok = ShiftSerializer(
            data={
                "name": "Day2",
                "location": str(loc.id),
                "start_time": "08:00 AM",
                "end_time": "08:00 PM",
                "is_default": True,
                "applies_to_all_sites": False,
                "site_ids": [str(site_a.id), str(site_b.id)],
            }
        )
        result.check("serializer: specific sites valid", ser_ok.is_valid(), str(ser_ok.errors))
        if ser_ok.is_valid():
            obj2 = ser_ok.save()
            data = ShiftSerializer(obj2).data
            result.check(
                "serializer: site_ids + site_names round-trip",
                set(data["site_ids"]) == {str(site_a.id), str(site_b.id)}
                and set(data["site_names"]) == {"Site A", "Site B"},
            )

        ser_x = ShiftSerializer(
            data={
                "name": "DayX",
                "location": str(loc.id),
                "start_time": "08:00 AM",
                "end_time": "08:00 PM",
                "is_default": True,
                "applies_to_all_sites": False,
                "site_ids": [str(other_site.id)],
            }
        )
        result.check(
            "serializer: rejects other-org site",
            (not ser_x.is_valid()) and ("site_ids" in ser_x.errors),
        )

        print("\n=== list_default_shifts scenarios ===")
        guard = User.objects.create(
            email=f"t-guard-{uuid4().hex[:8]}@test.local",
            name="TGuard",
            role="guard",
            location=loc,
            timezone="Asia/Kolkata",
            all_org_sites=True,
        )

        s_8 = Shift.objects.create(
            name="8-20", location=loc, start_time=time(8, 0), end_time=time(20, 0),
            is_default=True, applies_to_all_sites=True,
        )
        Shift.objects.create(
            name="9-21", location=loc, start_time=time(9, 0), end_time=time(21, 0),
            is_default=True, applies_to_all_sites=True,
        )
        Shift.objects.create(
            name="10-22", location=loc, start_time=time(10, 0), end_time=time(22, 0),
            is_default=True, applies_to_all_sites=True,
        )
        Shift.objects.create(
            name="14-22", location=loc, start_time=time(14, 0), end_time=time(22, 0),
            is_default=True, applies_to_all_sites=True,
        )
        Shift.objects.create(
            name="20-08", location=loc, start_time=time(20, 0), end_time=time(8, 0),
            is_default=True, applies_to_all_sites=True,
        )
        s_a_only = Shift.objects.create(
            name="SiteA-only-8-20", location=loc, start_time=time(8, 0), end_time=time(20, 0),
            is_default=True, applies_to_all_sites=False,
        )
        s_a_only.sites.set([site_a])
        _ = s_8  # silence unused

        def call(*, now_dt, site=None, has_assignment=False, guard_id=None):
            params = {}
            if guard_id is not False:
                params["guard_id"] = str(guard.id if guard_id is None else guard_id)
            request = factory.get(
                "/dashboard/attendance/list_default_shifts/",
                params,
            )
            force_authenticate(request, user=guard)
            selected = None
            if site is not None:
                selected = {"id": str(site.id), "name": site.name, "date": str(ON_DATE)}
            assignment = MagicMock() if has_assignment else None
            with patch.object(
                AttendanceCheckinViewSet,
                "get_today_assignment_v2",
                return_value=(assignment, None),
            ), patch(
                "dashboard.views.get_user_timezone_from_request", return_value=TZ
            ), patch(
                "dashboard.views.get_user_today", return_value=ON_DATE
            ), patch(
                "dashboard.views.get_user_now", return_value=now_dt
            ), patch(
                "scheduler.daily_site.current_site_for", return_value=selected
            ):
                return view(request)

        def names(resp):
            return [s["name"] for s in (resp.data or {}).get("default_shifts", [])]

        def detail(resp):
            return f"status={resp.status_code} data={getattr(resp, 'data', None)}"

        # missing guard_id
        resp = call(now_dt=_aware(9), site=site_a, guard_id=False)
        result.check(
            "API: missing guard_id → 400",
            resp.status_code == status.HTTP_400_BAD_REQUEST,
            detail(resp),
        )

        resp = call(now_dt=_aware(9), site=site_a, has_assignment=True)
        result.check(
            "API: already has shift",
            resp.status_code == 200 and resp.data.get("has_shift") is True,
            detail(resp),
        )

        resp = call(now_dt=_aware(9), site=None)
        result.check(
            "API: no site → empty + message",
            resp.status_code == 200
            and resp.data.get("default_shifts") == []
            and "Select a site" in (resp.data.get("message") or ""),
            detail(resp),
        )

        resp = call(now_dt=_aware(9), site=site_a)
        n = names(resp)
        result.check(
            "API @9am: includes 8-20, 9-21, 10-22",
            "8-20" in n and "9-21" in n and "10-22" in n,
            detail(resp),
        )
        result.check(
            "API @9am: excludes 14-22 and 20-08",
            resp.status_code == 200 and "14-22" not in n and "20-08" not in n,
            detail(resp),
        )
        result.check("API @9am: includes SiteA-only", "SiteA-only-8-20" in n, detail(resp))
        by_name = {s["name"]: s for s in (resp.data or {}).get("default_shifts", [])}
        result.check(
            "API @9am: in_progress flags",
            bool(by_name)
            and by_name.get("8-20", {}).get("in_progress")
            and by_name.get("9-21", {}).get("in_progress")
            and (not by_name.get("10-22", {}).get("in_progress", True)),
            detail(resp),
        )
        result.check(
            "API @9am: site_id/name echoed",
            (resp.data or {}).get("site_id") == str(site_a.id)
            and (resp.data or {}).get("site_name") == "Site A",
            detail(resp),
        )

        resp = call(now_dt=_aware(9), site=site_b)
        n = names(resp)
        result.check(
            "API @9am site B: excludes SiteA-only",
            "8-20" in n and "SiteA-only-8-20" not in n,
            detail(resp),
        )

        resp = call(now_dt=_aware(7), site=site_a)
        n = names(resp)
        result.check(
            "API @7am: 8-20 (±1h) + overnight 20-08; not 9/10/14",
            "8-20" in n
            and "20-08" in n
            and "9-21" not in n
            and "10-22" not in n
            and "14-22" not in n,
            detail(resp),
        )

        resp = call(now_dt=_aware(22), site=site_a)
        n = names(resp)
        result.check(
            "API @22:00: overnight + still-open day shifts; not ended 8/9",
            "20-08" in n
            and "14-22" in n
            and "10-22" in n
            and "8-20" not in n
            and "9-21" not in n,
            detail(resp),
        )

        resp = call(now_dt=_aware(8, 59), site=site_a)
        n = names(resp)
        result.check(
            "API @08:59: 10-22 not yet in ±1h window",
            "8-20" in n and "9-21" in n and "10-22" not in n,
            detail(resp),
        )

        # Real current_site_for via GuardSiteCache
        GuardSiteCache.objects.create(
            guard=guard, date=ON_DATE, site=site_b, location=loc
        )
        request = factory.get(
            "/dashboard/attendance/list_default_shifts/",
            {"guard_id": str(guard.id)},
        )
        force_authenticate(request, user=guard)
        with patch.object(
            AttendanceCheckinViewSet,
            "get_today_assignment_v2",
            return_value=(None, None),
        ), patch(
            "dashboard.views.get_user_timezone_from_request", return_value=TZ
        ), patch(
            "dashboard.views.get_user_today", return_value=ON_DATE
        ), patch(
            "dashboard.views.get_user_now", return_value=_aware(9)
        ):
            resp = view(request)
        n = names(resp)
        result.check(
            "API: GuardSiteCache selects site B (no SiteA-only)",
            (resp.data or {}).get("site_id") == str(site_b.id) and "SiteA-only-8-20" not in n,
            detail(resp),
        )

        # Always roll back fixture data
        transaction.set_rollback(True)

    print("\n=== Summary ===")
    print(f"Passed: {result.passed}  Failed: {result.failed}  Total: {result.passed + result.failed}")
    return result


if __name__ == "__main__":
    import os
    import sys
    import django

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")
    django.setup()
    r = run_scenarios()
    sys.exit(0 if r.failed == 0 else 1)
