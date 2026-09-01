import json
import logging
from datetime import datetime

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.utils.timezone import now

from patrol_backend.utils.timezone_utils import to_user_timezone

from .boundary_runtime import process_location_boundary_update
from .models import UserLiveLocation, UserLocationHistory
from .ws_groups import get_on_duty_context as fetch_on_duty_context
from .ws_groups import resolve_ws_groups_for_user

logger = logging.getLogger(__name__)


class LocationConsumer(AsyncWebsocketConsumer):
    # =========================================================================
    # LIFECYCLE METHODS
    # =========================================================================

    async def connect(self):
        """Authenticate and join role-based channel groups."""
        self.user = self.scope["user"]
        self.ws_groups = []

        if self.user.is_anonymous:
            await self.close()
            return

        self.ws_groups = await self._resolve_groups()
        for group_name in self.ws_groups:
            await self.channel_layer.group_add(group_name, self.channel_name)

        logger.info(
            "[WS_CONN] User:%s Role:%s groups=%s",
            self.user.email,
            getattr(self.user, "role", None),
            self.ws_groups,
        )
        await self.accept()

    async def disconnect(self, close_code):
        """Leave all groups joined at connect."""
        if getattr(self, "user", None) and not self.user.is_anonymous:
            for group_name in getattr(self, "ws_groups", []):
                await self.channel_layer.group_discard(group_name, self.channel_name)

    # =========================================================================
    # INCOMING MESSAGE ROUTING (The Router)
    # =========================================================================

    async def receive(self, text_data):
        """Receives raw JSON from the client and routes it to the correct handler."""
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        message_type = data.get("type", "location_update")

        if message_type == "location_update":
            await self.handle_location_update(data)
        elif message_type == "emergency_alert":
            await self.handle_emergency_alert(data)

    # =========================================================================
    # MESSAGE HANDLERS (Business Logic)
    # =========================================================================

    async def handle_location_update(self, data):
        """
        Processes coordinates sent by an on-duty mobile user.
        Saves to DB in UTC and broadcasts enriched payload.
        """
        lat = data.get("lat")
        lng = data.get("lng")
        if not lat or not lng:
            return

        on_duty_ctx = await self.get_on_duty_context()
        if not on_duty_ctx.get("on_duty"):
            await self.send(
                text_data=json.dumps(
                    {
                        "type": "error",
                        "code": "not_on_duty",
                        "message": "Location updates are only accepted while checked in.",
                    }
                )
            )
            logger.info(
                "[WS_GPS] Rejected location_update for %s: not on duty",
                self.user.email,
            )
            return

        server_now_utc = now()
        live_context = await self.save_user_location(lat, lng, server_now_utc, on_duty_ctx)

        payload = {
            "type": "location_update",
            "user_id": str(getattr(self.user, "id", "unknown")),
            "name": getattr(self.user, "name", getattr(self.user, "email", "Unknown")),
            "role": getattr(self.user, "role", "guard"),
            "lat": lat,
            "lng": lng,
            "timestamp_utc": server_now_utc.isoformat(),
            "on_duty": True,
            "assigned_site_id": live_context.get("assigned_site_id"),
            "is_inside_boundary": live_context.get("is_inside_boundary"),
            "boundary_status": live_context.get("boundary_status"),
        }

        await self.broadcast_to_groups(
            payload,
            assigned_site_id=live_context.get("assigned_site_id"),
        )

    async def handle_emergency_alert(self, data):
        """Handles high-priority Panic/SOS events."""
        server_now_utc = now()
        payload = {
            "type": "emergency_alert",
            "user_id": str(getattr(self.user, "id", "unknown")),
            "name": getattr(self.user, "name", getattr(self.user, "email", "Unknown")),
            "message": data.get("message", "Emergency Alert Triggered!"),
            "lat": data.get("lat"),
            "lng": data.get("lng"),
            "timestamp_utc": server_now_utc.isoformat(),
        }
        on_duty_ctx = await self.get_on_duty_context()
        assigned_site_id = on_duty_ctx.get("assigned_site_id")
        if assigned_site_id:
            payload["assigned_site_id"] = assigned_site_id
        await self.broadcast_to_groups(payload, assigned_site_id=assigned_site_id)

    # =========================================================================
    # BROADCASTING HELPERS
    # =========================================================================

    async def broadcast_to_groups(self, payload, assigned_site_id=None):
        """Broadcast to superadmin, org live-map, and site-scoped alert groups."""
        await self.channel_layer.group_send("all_locations", payload)

        location_id = getattr(self.user, "location_id", None)
        if location_id:
            await self.channel_layer.group_send(f"location_{location_id}", payload)

        if assigned_site_id:
            await self.channel_layer.group_send(
                f"site_{assigned_site_id}_tracking",
                payload,
            )

    # =========================================================================
    # OUTGOING EVENT HANDLERS (Sending to Client)
    # =========================================================================

    async def location_update(self, event):
        """Send location updates; convert UTC timestamp to the listener's timezone."""
        event = self._localize_event_timestamps(event)
        await self.send(text_data=json.dumps(event))

    async def emergency_alert(self, event):
        """Send emergency alerts with localized timestamps."""
        event = self._localize_event_timestamps(event)
        await self.send(text_data=json.dumps(event))

    async def tracking_alert(self, event):
        """Send boundary / location-missing alerts to configured recipients."""
        event = self._localize_event_timestamps(event)
        await self.send(text_data=json.dumps(event))

    def _localize_event_timestamps(self, event):
        """Copy event and add localized timestamp fields for the connected user."""
        event = dict(event)
        timestamp_utc = event.get("timestamp_utc")
        if not timestamp_utc:
            return event

        utc_dt = datetime.fromisoformat(timestamp_utc)
        local_dt = to_user_timezone(utc_dt, self.user)
        event["timestamp"] = local_dt.strftime("%Y-%m-%d %H:%M:%S")
        event["timestamp_iso"] = local_dt.isoformat()
        return event

    # =========================================================================
    # DATABASE OPERATIONS (Blocking -> Async)
    # =========================================================================

    @database_sync_to_async
    def _resolve_groups(self):
        return resolve_ws_groups_for_user(self.user)

    @database_sync_to_async
    def get_on_duty_context(self):
        return fetch_on_duty_context(self.user)

    @database_sync_to_async
    def save_user_location(self, lat, lng, server_now_utc, on_duty_ctx):
        """
        Saves location data to live + history tables.
        Returns boundary context for the broadcast payload.
        """
        user_location = on_duty_ctx.get("org_location") or getattr(self.user, "location", None)
        assigned_site = on_duty_ctx.get("assigned_site")

        defaults = {
            "location": user_location,
            "latitude": lat,
            "longitude": lng,
            "last_location_at": server_now_utc,
        }
        if assigned_site:
            defaults["assigned_site"] = assigned_site

        live_loc, _created = UserLiveLocation.objects.update_or_create(
            user=self.user,
            defaults=defaults,
        )

        UserLocationHistory.objects.create(
            user=self.user,
            location=user_location,
            latitude=lat,
            longitude=lng,
            timestamp=server_now_utc,
        )

        boundary_ctx = process_location_boundary_update(
            user=self.user,
            live_loc=live_loc,
            site=assigned_site,
            lat=float(lat),
            lng=float(lng),
            checkin=on_duty_ctx.get("checkin"),
            server_now_utc=server_now_utc,
        )

        return boundary_ctx
