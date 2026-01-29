import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils.timezone import now
from .models import UserLiveLocation, UserLocationHistory
from patrol_backend.utils.timezone_utils import to_user_timezone
from datetime import datetime

class LocationConsumer(AsyncWebsocketConsumer):
    # =========================================================================
    # LIFECYCLE METHODS
    # =========================================================================

    async def connect(self):
        """
        Triggered when a client (Guard/Admin) connects to the WebSocket.
        Handles role-based room/group entry.
        """
        self.user = self.scope["user"]
        if self.user.is_anonymous:
            await self.close()
            return

        # Identify User Context
        role = getattr(self.user, 'role', None)
        location_id = getattr(self.user, 'location_id', None)

        # 1. Superadmins join a global group to see everything
        if role == 'superadmin':
            await self.channel_layer.group_add("all_locations", self.channel_name)
        
        # 2. Admins, SOs, and FOs join their specific location group
        elif role in ['admin', 'so', 'fo']:
            if location_id:
                self.loc_group = f"location_{location_id}"
                await self.channel_layer.group_add(self.loc_group, self.channel_name)
        
        # 3. Guards join their location group (used for sending updates to their admins)
        elif role == 'guard':
            if location_id:
                self.loc_group = f"location_{location_id}"
        
        await self.accept()

    async def disconnect(self, close_code):
        """
        Triggered when a client disconnects.
        Cleans up group memberships.
        """
        if not self.user.is_anonymous:
            role = getattr(self.user, 'role', None)
            
            if role == 'superadmin':
                await self.channel_layer.group_discard("all_locations", self.channel_name)
            
            elif role in ['admin', 'so', 'fo']:
                location_id = getattr(self.user, 'location_id', None)
                if location_id:
                    await self.channel_layer.group_discard(f"location_{location_id}", self.channel_name)

    # =========================================================================
    # INCOMING MESSAGE ROUTING (The Router)
    # =========================================================================

    async def receive(self, text_data):
        """
        Receives raw JSON from the client and routes it to the correct handler.
        """
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
        Processes coordinates sent by a Guard.
        Saves to DB in UTC and Broadcasts.
        """
        lat = data.get("lat")
        lng = data.get("lng")
        server_now_utc = now() # Current time in UTC
        
        # 1. Persist to Database (UTC)
        await self.save_user_location(lat, lng, server_now_utc)

        # 2. Build Broadcast Payload (Using UTC ISO string for the group message)
        payload = {
            "type": "location_update",
            "user_id": str(getattr(self.user, 'id', 'unknown')),
            "name": getattr(self.user, 'name', getattr(self.user, 'email', 'Unknown')),
            "role": getattr(self.user, 'role', 'guard'),
            "lat": lat,
            "lng": lng,
            "timestamp_utc": server_now_utc.isoformat() # Standard UTC exchange format
        }

        # 3. Emit to Groups
        await self.broadcast_to_groups(payload)

    async def handle_emergency_alert(self, data):
        """
        Handles high-priority Panic/SOS events.
        """
        server_now_utc = now()
        payload = {
            "type": "emergency_alert",
            "user_id": str(getattr(self.user, 'id', 'unknown')),
            "name": getattr(self.user, 'name', getattr(self.user, 'email', 'Unknown')),
            "message": data.get("message", "Emergency Alert Triggered!"),
            "lat": data.get("lat"),
            "lng": data.get("lng"),
            "timestamp_utc": server_now_utc.isoformat()
        }
        await self.broadcast_to_groups(payload)

    # =========================================================================
    # BROADCASTING HELPERS
    # =========================================================================

    async def broadcast_to_groups(self, payload):
        """
        Sends a message to both the global superadmin pool and the local admin pool.
        """
        await self.channel_layer.group_send("all_locations", payload)

        location_id = getattr(self.user, 'location_id', None)
        if location_id:
            loc_group = f"location_{location_id}"
            await self.channel_layer.group_send(loc_group, payload)

    # =========================================================================
    # OUTGOING EVENT HANDLERS (Sending to Client)
    # =========================================================================

    async def location_update(self, event):
        """
        Sends location updates to the individual Admin's browser.
        Converts UTC timestamp to the Admin's specific timezone.
        """
        # Convert the incoming UTC ISO string back to a datetime object
        utc_dt = datetime.fromisoformat(event["timestamp_utc"])
        
        # Convert to this specific user's timezone using your helper
        local_dt = to_user_timezone(utc_dt, self.user)
        
        # Update the event with the correctly formatted local time
        event["timestamp"] = local_dt.strftime('%Y-%m-%d %H:%M:%S')
        
        # Optional: Keep the ISO format for frontend flexibility
        event["timestamp_iso"] = local_dt.isoformat()

        await self.send(text_data=json.dumps(event))

    async def emergency_alert(self, event):
        """
        Sends emergency alerts to the individual Admin's browser.
        Converts UTC timestamp to the Admin's specific timezone.
        """
        utc_dt = datetime.fromisoformat(event["timestamp_utc"])
        local_dt = to_user_timezone(utc_dt, self.user)
        
        event["timestamp"] = local_dt.strftime('%Y-%m-%d %H:%M:%S')
        event["timestamp_iso"] = local_dt.isoformat()

        await self.send(text_data=json.dumps(event))

    # =========================================================================
    # DATABASE OPERATIONS (Blocking -> Async)
    # =========================================================================

    @database_sync_to_async
    def save_user_location(self, lat, lng, server_now_utc):
        """
        Saves location data to both Live and History tables in UTC.
        """
        if not lat or not lng:
            return

        user_location = getattr(self.user, 'location', None)

        # Update Live Table
        UserLiveLocation.objects.update_or_create(
            user=self.user,
            defaults={
                'location': user_location,
                'latitude': lat,
                'longitude': lng
            }
        )

        # Append to History Table
        UserLocationHistory.objects.create(
            user=self.user,
            location=user_location,
            latitude=lat,
            longitude=lng,
            timestamp=server_now_utc
        )
