#!/usr/bin/env python3
"""
Integration flow: login → shift_today_v5 → (assign if needed) → checkin_v5.

Run from patrol_backend directory:
  ../venv/bin/python dashboard/test_attendance_v5_flow.py

Optional env:
  GTMS_API_BASE_URL=http://localhost:8000   (default; uses requests if server is up)
  GTMS_USE_API_CLIENT=1                     (force Django APIClient against DB, no HTTP server)

Credentials (override via env):
  GTMS_TEST_EMAIL=babu@test.com
  GTMS_TEST_PASSWORD=123456
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict, Optional, Tuple

# Django setup when run as script
_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _BACKEND_ROOT not in sys.path:
    sys.path.insert(0, _BACKEND_ROOT)
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "patrol_backend.settings")

import django

django.setup()

import requests
from rest_framework.test import APIClient
from scheduler.models import LocationSite

EMAIL = os.environ.get("GTMS_TEST_EMAIL", "babu@test.com")
PASSWORD = os.environ.get("GTMS_TEST_PASSWORD", "123456")
BASE_URL = os.environ.get("GTMS_API_BASE_URL", "http://localhost:8000").rstrip("/")
USE_API_CLIENT = os.environ.get("GTMS_USE_API_CLIENT", "").lower() in ("1", "true", "yes")


def _pretty(obj: Any) -> str:
    return json.dumps(obj, indent=2, default=str)


class FlowRunner:
    def __init__(self):
        self.use_http = not USE_API_CLIENT
        self.session = requests.Session()
        self.client = APIClient()
        self.token: Optional[str] = None
        self.user_id: Optional[str] = None
        self.location_id: Optional[str] = None
        self.steps: list[str] = []

    def log(self, title: str, response=None, extra: Optional[dict] = None):
        line = f"\n=== {title} ==="
        print(line)
        self.steps.append(title)
        if extra:
            print(_pretty(extra))
        if response is not None:
            status = getattr(response, "status_code", None)
            if status is not None:
                print(f"HTTP {status}")
            try:
                if hasattr(response, "json"):
                    body = response.json()
                else:
                    body = response.data if hasattr(response, "data") else response.content
                # Redact long JWT tokens from login output
                if isinstance(body, dict) and isinstance(body.get("data"), dict):
                    data = body["data"]
                    for key in ("access", "refresh"):
                        if key in data and isinstance(data[key], str) and len(data[key]) > 20:
                            data[key] = data[key][:16] + "…"
                print(_pretty(body))
            except Exception as exc:
                print(f"(could not parse body: {exc})")

    def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        data: Optional[dict] = None,
        params: Optional[dict] = None,
        files: Optional[dict] = None,
    ):
        path = path if path.startswith("/") else f"/{path}"
        if self.use_http:
            url = f"{BASE_URL}{path}"
            headers = {}
            if self.token:
                headers["Authorization"] = f"Bearer {self.token}"
            return self.session.request(
                method,
                url,
                json=json_body,
                data=data,
                params=params,
                files=files,
                headers=headers,
                timeout=60,
            )
        host = os.environ.get("GTMS_TEST_HTTP_HOST", "localhost")
        headers = {"HTTP_HOST": host}
        if self.token:
            headers["HTTP_AUTHORIZATION"] = f"Bearer {self.token}"
        if method.upper() == "GET":
            return self.client.get(path, data=params or {}, **headers)
        if method.upper() == "POST":
            if files:
                return self.client.post(path, data=data or {}, **headers)
            if json_body is not None:
                return self.client.post(path, data=json_body, format="json", **headers)
            return self.client.post(path, data=data or {}, **headers)
        raise ValueError(f"Unsupported method {method}")

    def login(self) -> bool:
        resp = self._request("POST", "/auth/login/", json_body={"email": EMAIL, "password": PASSWORD})
        self.log("1. Login", resp)
        if resp.status_code != 200:
            return False
        body = resp.json() if self.use_http else resp.data
        if body.get("status") != "success":
            return False
        data = body.get("data") or {}
        self.token = data.get("access")
        self.user_id = data.get("id")
        self.location_id = data.get("location")
        return bool(self.token and self.user_id)

    def my_sites(self) -> Dict[str, Any]:
        resp = self._request("GET", "/auth/v5/my-sites/")
        self.log("2. GET my-sites (site_id for punch)", resp)
        body = resp.json() if self.use_http else resp.data
        return (body.get("data") or {}) if body.get("status") == "success" else {}

    def shift_today_v5(self) -> Dict[str, Any]:
        resp = self._request("GET", "/dashboard/attendance/shift_today_v5/")
        self.log("3. GET shift_today_v5", resp)
        if resp.status_code != 200:
            return {}
        return resp.json() if self.use_http else resp.data

    def list_default_shifts(self) -> Dict[str, Any]:
        resp = self._request(
            "GET",
            "/dashboard/attendance/list_default_shifts/",
            params={"guard_id": self.user_id},
        )
        self.log("4. GET list_default_shifts", resp)
        if resp.status_code != 200:
            return {}
        return resp.json() if self.use_http else resp.data

    def list_checkpoint_templates(self, location_id: str, shift_id: Optional[str] = None) -> list:
        if shift_id:
            path = f"/scheduler/checkpoint-templates/by-shift/{shift_id}/"
        else:
            path = f"/scheduler/checkpoint-templates/by-location/{location_id}/"
        resp = self._request("GET", path)
        self.log(f"5. GET checkpoint templates ({path})", resp)
        if resp.status_code != 200:
            return []
        return resp.json() if self.use_http else resp.data

    def create_assignment_v5(
        self,
        shift_id: str,
        checkpoint_template_id: Optional[str],
        site_id: Optional[str],
    ) -> Dict[str, Any]:
        payload = {
            "guard_id": self.user_id,
            "shift_id": shift_id,
        }
        if checkpoint_template_id:
            payload["checkpoint_template_id"] = checkpoint_template_id
        if site_id:
            payload["site_id"] = site_id
        resp = self._request("POST", "/dashboard/attendance/create_assignment_v5/", data=payload)
        self.log("6. POST create_assignment_v5", resp, extra=payload)
        if resp.status_code not in (200, 201):
            return {}
        return resp.json() if self.use_http else resp.data

    def assign_checkpoint_template(self, checkpoint_template_id: str, shift_id: str) -> Dict[str, Any]:
        payload = {
            "guard_id": self.user_id,
            "checkpoint_template_id": checkpoint_template_id,
            "shift_id": shift_id,
        }
        resp = self._request("POST", "/dashboard/attendance/assign-checkpoint-template/", data=payload)
        self.log("6b. POST assign-checkpoint-template", resp, extra=payload)
        if resp.status_code != 200:
            return {}
        return resp.json() if self.use_http else resp.data

    def checkin_v5(self, site_id: Optional[str], latitude: float, longitude: float) -> Tuple[int, dict]:
        data = {
            "latitude": str(latitude),
            "longitude": str(longitude),
        }
        if site_id:
            data["site_id"] = site_id
        resp = self._request("POST", "/dashboard/attendance/checkin_v5/", data=data)
        self.log("7. POST checkin_v5", resp, extra=data)
        body = resp.json() if self.use_http else resp.data
        return resp.status_code, body

    def resolve_site_coords(self, site_id: Optional[str]) -> Tuple[float, float]:
        if site_id:
            site = LocationSite.objects.filter(id=site_id, is_active=True).first()
            if site and site.latitude is not None and site.longitude is not None:
                return float(site.latitude), float(site.longitude)
            api_sites = self.fetch_org_sites_api()
            for row in api_sites:
                if str(row.get("id")) == str(site_id):
                    return float(row["latitude"]), float(row["longitude"])
        raise RuntimeError(f"Site {site_id} not found or missing coordinates")

    def fetch_org_sites_api(self) -> list:
        if not self.location_id:
            return []
        resp = self._request("GET", f"/scheduler/locations/{self.location_id}/sites/")
        if resp.status_code != 200:
            return []
        body = resp.json() if self.use_http else resp.data
        if isinstance(body, list):
            return body
        return body.get("results") or []

    def pick_site_id(self, my_sites: dict, shift_data: dict) -> Optional[str]:
        for key in ("last_selected_site_id", "assigned_site_id"):
            val = shift_data.get(key) or my_sites.get(key)
            if val:
                return str(val)
        sites = my_sites.get("sites") or []
        if sites:
            return str(sites[0].get("id"))
        api_sites = self.fetch_org_sites_api()
        if api_sites:
            first = api_sites[0]
            sid = first.get("id")
            print(f"\n→ Using org site from API: {first.get('name')} ({sid})")
            return str(sid)
        if self.location_id:
            site = (
                LocationSite.objects.filter(location_id=self.location_id, is_active=True)
                .order_by("name")
                .first()
            )
            if site:
                print(f"\n→ Using org site from DB: {site.name} ({site.id})")
                return str(site.id)
        return None

    def pick_default_shift(self, defaults: dict) -> dict:
        """Prefer a non-overnight shift when multiple defaults exist (often easier to check in)."""
        shifts = defaults.get("default_shifts") or []
        if not shifts:
            return {}
        day_shifts = [s for s in shifts if not s.get("is_overnight")]
        return day_shifts[0] if day_shifts else shifts[0]

    def ensure_shift_and_template(self, shift_data: dict, my_sites: dict) -> dict:
        """If no shift today, run list_default → templates → create_assignment_v5."""
        if shift_data.get("has_shift"):
            print("\n→ User already has shift today; skipping assignment creation.")
            return shift_data

        defaults = self.list_default_shifts()
        if defaults.get("has_shift"):
            print("\n→ list_default_shifts reports has_shift; refreshing shift_today_v5.")
            return self.shift_today_v5()

        shifts = defaults.get("default_shifts") or []
        if not shifts:
            raise RuntimeError("No default shifts available for this guard/location.")

        shift = self.pick_default_shift(defaults)
        shift_id = shift["id"]
        location_id = defaults.get("location_id") or self.location_id

        templates = self.list_checkpoint_templates(location_id, shift_id=shift_id)
        template_id = None
        if templates:
            first = templates[0]
            template_id = first.get("template_id") or first.get("id")
            print(f"\n→ Selected template: {first.get('template_name') or template_id}")
        elif shift.get("checkpoint_template_id"):
            template_id = shift["checkpoint_template_id"]
            print(f"\n→ Using shift default template: {template_id}")

        site_id = self.pick_site_id(my_sites, shift_data)
        created = self.create_assignment_v5(shift_id, template_id, site_id)
        if not created and template_id:
            # Assignment may exist without checkpoints — try attach template
            self.assign_checkpoint_template(template_id, shift_id)

        return self.shift_today_v5()

    def run(self) -> int:
        print(f"Mode: {'HTTP → ' + BASE_URL if self.use_http else 'Django APIClient (DB)'}")
        print(f"User: {EMAIL}")

        if not self.login():
            print("\nFAILED: login")
            return 1

        my_sites = self.my_sites()
        shift_data = self.shift_today_v5()

        try:
            shift_data = self.ensure_shift_and_template(shift_data, my_sites)
        except RuntimeError as exc:
            print(f"\nFAILED: {exc}")
            return 1

        if not shift_data.get("has_shift"):
            print("\nFAILED: still no shift after assignment flow")
            return 1

        site_id = self.pick_site_id(my_sites, shift_data)
        lat, lon = None, None
        if site_id:
            try:
                lat, lon = self.resolve_site_coords(site_id)
            except RuntimeError as exc:
                print(f"\nWARN: {exc}")

        if lat is None or lon is None:
            # Nearest-site fallback: use coords from a checkpoint on the shift template
            templates = self.list_checkpoint_templates(
                self.location_id,
                shift_id=shift_data.get("shift_id"),
            )
            for tpl in templates:
                for cp in tpl.get("checkpoints") or []:
                    checkpoint = cp.get("checkpoint") or {}
                    if checkpoint.get("latitude") and checkpoint.get("longitude"):
                        lat = float(checkpoint["latitude"])
                        lon = float(checkpoint["longitude"])
                        print(f"\n→ Using checkpoint GPS for punch: {checkpoint.get('label')} ({lat}, {lon})")
                        break
                if lat is not None:
                    break

        if lat is None or lon is None:
            print("\nFAILED: could not resolve GPS coordinates for check-in")
            return 1

        if shift_data.get("show_checkout") and not shift_data.get("show_checkin"):
            print(
                "\nNOTE: User appears already checked in (show_checkout=True). "
                "Attempting checkin_v5 anyway (may fail if open session exists)."
            )
        if not shift_data.get("show_checkin") and shift_data.get("message"):
            print(f"\nNOTE: shift_today_v5 message: {shift_data.get('message')}")

        status_code, body = self.checkin_v5(site_id, lat, lon)
        if status_code == 201:
            print("\nSUCCESS: checkin_v5 completed.")
            print(f"  site_id: {body.get('site_id')}")
            print(f"  site_resolution: {body.get('site_resolution')}")
            print(f"  checkin_time: {body.get('checkin_time')}")
            return 0

        msg = shift_data.get("message") or ""
        if "Too early" in msg or body.get("message") == "Too early to check in":
            print("\nPARTIAL SUCCESS: API flow completed; check-in blocked by shift time window.")
            print(f"  shift_today_v5: {msg}")
            print(f"  checkin_v5 HTTP {status_code}: {_pretty(body)}")
            return 0

        print(f"\nFAILED: checkin_v5 returned HTTP {status_code}")
        print(_pretty(body))
        return 1


def main():
    runner = FlowRunner()
    if not USE_API_CLIENT:
        try:
            requests.post(
                f"{BASE_URL}/auth/login/",
                json={"email": EMAIL, "password": PASSWORD},
                timeout=5,
            )
            runner.use_http = True
        except requests.RequestException:
            print(f"Server not reachable at {BASE_URL}; using Django APIClient against database.")
            runner.use_http = False

    sys.exit(runner.run())


if __name__ == "__main__":
    main()
