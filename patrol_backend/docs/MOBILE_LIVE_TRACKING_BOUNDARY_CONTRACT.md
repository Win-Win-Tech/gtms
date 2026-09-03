# Mobile Live Tracking, Map & Boundary Alerts — Contract

**Audience:** Mobile app team  
**Status:** Align with web live map + tracking alerts (Phases 9–11)  
**Related:** [`SITE_BOUNDARY_SOS_PLAN.md`](SITE_BOUNDARY_SOS_PLAN.md)

Mobile has a **live map** (same as web). Do the same steps the web frontend does: connect WS, load last-known locations, draw site boundaries, listen for `location_update` + `tracking_alert`, and use the alerts REST inbox for history / unread badge.

---

## Delivery channels (important)

| Channel | Used for tracking alerts? |
|---------|---------------------------|
| **WebSocket** `tracking_alert` | **Yes** — realtime while app is connected |
| **FCM / push** | **Yes** — to each recipient’s registered android/ios tokens |
| **REST inbox** `GET /livetracking/alerts/` | **Yes** — history, unread count, mark read |

Recipients get **both** socket + FCM on create / resolve / still-outside reminder.  
Tracking alerts do **not** write visitor `NotificationLog` — inbox is only `/livetracking/alerts/`.  
Device tokens: same `POST /notifications/device-token/` as visitor push.

---

## Auth

| | |
|---|---|
| **REST** | `Authorization: Bearer <JWT>` |
| **WebSocket** | Query: `?token=<JWT access token>` |
| **Base URL** | Same host as API (use `wss://` in production for WS) |

---

## End-to-end steps (mirror web)

These are the steps web does in `websocket.jsx` + `LiveTrackingMap.jsx` + tracking-alerts page. Mobile should do the same.

### Step 1 — Login

Obtain JWT (existing auth). Keep access token for REST + WS.

**Register FCM** (required for push alerts):

```http
POST /notifications/device-token/
Authorization: Bearer <JWT>
Content-Type: application/json

{
  "token": "<FCM_DEVICE_TOKEN>",
  "device_type": "android"
}
```

`device_type`: `android` | `ios` (web tokens are not used for tracking push).  
On logout: `DELETE /notifications/device-token/` with the same token.

### Step 2 — Open live map screen (viewer / SO / FO / Admin / on-duty app with map)

1. Connect WebSocket (Step 3).
2. Call **last-known locations** REST (Step 4) to seed markers.
3. Call **sites list** REST (Step 5) to draw boundaries.
4. Optionally call **alerts inbox** (Step 6) for unread badge.
5. Keep listening on WS for `location_update` and `tracking_alert`.

### Step 3 — Connect WebSocket

| | |
|---|---|
| **URL** | `ws://<host>/ws/live-location/?token=<ACCESS_TOKEN>` |
| **When** | When opening live map (and/or after check-in if the same app also **sends** GPS) |
| **Reconnect** | On token refresh or network drop; re-run Step 4 after reconnect |

Server joins the user to role/site groups automatically (live map org group, `site_{id}_tracking`, `tracking_user_{user_id}` for alert recipients). Mobile does **not** choose groups.

### Step 4 — Seed map markers (REST)

```http
GET /livetracking/last-known-locations/
Authorization: Bearer <JWT>
```

Optional query (superadmin only): `?location_id=<org_uuid>`

**Response:** JSON **array** of location payloads (same shape as WS `location_update`):

```json
[
  {
    "type": "location_update",
    "user_id": "uuid",
    "name": "Guard Name",
    "role": "guard",
    "lat": 9.9252,
    "lng": 78.1198,
    "timestamp": "2026-09-03 15:00:00",
    "timestamp_iso": "2026-09-03T15:00:00+05:30",
    "timestamp_utc": "2026-09-03T09:30:00+00:00",
    "on_duty": true,
    "assigned_site_id": "site-uuid-or-null",
    "is_inside_boundary": true,
    "boundary_status": "inside"
  }
]
```

| Field | Use on mobile map |
|-------|-------------------|
| `lat` / `lng` | Marker position |
| `user_id` / `name` / `role` | Marker identity |
| `assigned_site_id` | Link pin to site |
| `is_inside_boundary` | `true` / `false` / `null` |
| `boundary_status` | e.g. `inside` \| `outside` \| `unknown` |
| timestamps | Stale / offline colouring |

**403** if the user cannot view the live map.

### Step 5 — Draw site boundaries on the map (REST)

Same as web `getLocationSites` → `SiteBoundaryOverlays`:

```http
GET /scheduler/locations/<location_id>/sites/
Authorization: Bearer <JWT>
```

Use the logged-in user’s organisation `location_id` (same as web).

**Each site object (relevant fields):**

```json
{
  "id": "site-uuid",
  "name": "Babu Home",
  "latitude": 9.9252,
  "longitude": 78.1198,
  "is_active": true,
  "boundary_type": "circle",
  "boundary_radius_m": 150,
  "boundary_polygon": null,
  "boundary_enabled": true,
  "breach_alerts_enabled": true,
  "location_missing_alerts_enabled": true
}
```

**How web draws (mobile should match):**

| `boundary_type` | Draw when | How |
|-----------------|-----------|-----|
| `circle` | `boundary_radius_m > 0` and lat/lng present | Circle centered at site lat/lng, radius metres |
| `polygon` | `boundary_polygon` has ≥ 3 points | Polygon; points are `[lat, lng]` pairs (or `{lat,lng}`) |

Skip inactive sites (`is_active === false`). Optional: lighter stroke if `breach_alerts_enabled` is false (web does this).

### Step 6 — Alerts inbox + unread badge (REST)

```http
GET /livetracking/alerts/?limit=50&offset=0
Authorization: Bearer <JWT>
```

**Query params (optional):**

| Param | Example | Meaning |
|-------|---------|---------|
| `unread` | `true` | Only unread deliveries |
| `alert_type` | `boundary_breach` \| `location_missing` | Filter type |
| `site_id` | uuid | Filter site |
| `is_active` | `true` / `false` | Open vs resolved |
| `limit` / `offset` | pagination | Max limit 200 |

**Response body:**

```json
{
  "count": 12,
  "limit": 50,
  "offset": 0,
  "unread_count": 3,
  "results": [
    {
      "id": "delivery-uuid",
      "read_at": null,
      "created_at": "2026-09-03T09:30:00.000000Z",
      "alert": {
        "id": "alert-uuid",
        "alert_type": "boundary_breach",
        "site": "site-uuid",
        "site_name": "Babu Home",
        "location": "org-location-uuid",
        "subject_user": "user-uuid",
        "subject_user_name": "Guard Name",
        "subject_role": "guard",
        "attendance": "checkin-uuid-or-null",
        "latitude": "9.920000000",
        "longitude": "78.120000000",
        "message": "Guard Name left the site boundary at Babu Home",
        "is_active": true,
        "created_at": "2026-09-03T09:30:00.000000Z",
        "resolved_at": null
      }
    }
  ]
}
```

**Mark one read:**

```http
POST /livetracking/alerts/<alert_id>/read/
```

Response: one inbox row (same shape as a `results[]` item).

**Mark all read:**

```http
POST /livetracking/alerts/read-all/
```

```json
{ "marked_read": 3 }
```

Use `unread_count` for the badge (web header map-pin badge).

### Step 7 — Listen on WebSocket (realtime)

After connect, parse every message `type`:

| `type` | Action on mobile |
|--------|------------------|
| `location_update` | Upsert marker by `user_id`; apply lat/lng, `is_inside_boundary`, `boundary_status` |
| `tracking_alert` | Local notification / toast; update marker flags; refresh unread badge |
| `error` | Handle sender-side failures (see below) — **not** an alert |

#### `location_update` (inbound — map)

Same fields as Step 4. Merge into map state by `user_id`. Web also preserves breach / missing flags when a plain GPS ping arrives (do not clear `hasBoundaryBreach` / `isLocationMissing` unless a resolve `tracking_alert` says so).

#### `tracking_alert` (inbound — WebSocket **and** FCM `data`)

Sent to configured **recipients**. Delivery: **socket + FCM** (same fields in FCM `data`; all FCM values are **strings**).

**Notification (FCM display):**

| | New / open alert | Resolved |
|--|------------------|----------|
| **title** | `Boundary breach` / `Location missing` / `Emergency SOS` | `Tracking alert resolved` |
| **body** | Same as `message` | Same as `message` |

**FCM `data` / WS body:**

```json
{
  "type": "tracking_alert",
  "alert_id": "alert-uuid",
  "alert_type": "boundary_breach",
  "site_id": "site-uuid",
  "site_name": "Babu Home",
  "location_id": "org-location-uuid",
  "subject_user_id": "user-uuid",
  "subject_name": "Guard Name",
  "subject_role": "guard",
  "message": "Guard Name left the site boundary at Babu Home",
  "is_active": "true",
  "latitude": "9.92",
  "longitude": "78.12",
  "timestamp_utc": "2026-09-03T09:30:00+00:00"
}
```

Notes:

- On **WebSocket**, `is_active` is a boolean; `latitude` / `longitude` may be numbers; server may also add localized `timestamp` / `timestamp_iso`.
- On **FCM**, every `data` value is a **string** (`"true"` / `"false"` for `is_active`).
- Tap handling: open live map focused on `subject_user_id` / `site_id`, or open tracking alerts inbox and load `alert_id`.
- Deduplicate by `alert_id` if both WS and FCM arrive while foregrounded.

### Step 8 — Optional history trail

```http
GET /livetracking/history/?user_id=<uuid>&timeframe=24h
```

Not required for boundary alerts; used if mobile shows a path history UI.

---

## Sending GPS (on-duty mobile user)

Separate from **viewing** the map. Same WS connection can both send and receive.

### When to send

| Event | Action |
|-------|--------|
| After successful **check-in** | Start GPS timer; ensure WS connected |
| Every **10–30 s** while on duty | Send `location_update` |
| On **checkout** | Stop GPS immediately |

### Client → server

```json
{
  "type": "location_update",
  "lat": 9.9252,
  "lng": 78.1198
}
```

Server saves location, runs boundary logic, may create/resolve alerts, then broadcasts `location_update` to live-map listeners.

---

## What `type: "error"` is for

This is **not** a tracking alert and **not** for the map inbox.

The server sends it **only to the client that tried to send GPS** when that send is rejected. Today there is one code:

```json
{
  "type": "error",
  "code": "not_on_duty",
  "message": "Location updates are only accepted while checked in."
}
```

| When | Meaning | Mobile should |
|------|---------|----------------|
| App sends `location_update` but user has **no open check-in** | GPS rejected; nothing saved; no boundary alert | Stop / pause GPS loop; avoid retry storms; optionally show soft toast |

If the user is a **map viewer only** (never sends GPS), they will normally **never** see `type: "error"`.

---

## Marker colour hints (same idea as web)

| Condition | Suggested colour |
|-----------|------------------|
| Online / normal | Purple `#7C3AED` |
| Outside boundary / active breach | Red `#DC2626` |
| Location missing | Deep orange `#EA580C` |
| Offline / no recent GPS | Grey `#94A3B8` |
| Stale but still online | Same pin, slightly faded |

---

## Checklist — mobile parity with web

**Map viewer**

- [ ] Register FCM token after login (`POST /notifications/device-token/`)
- [ ] Handle FCM `data.type === "tracking_alert"` (background + tap)
- [ ] Handle WS `tracking_alert` (foreground); dedupe with FCM by `alert_id`
- [ ] Connect `/ws/live-location/?token=...`
- [ ] `GET /livetracking/last-known-locations/` → seed markers
- [ ] `GET /scheduler/locations/<location_id>/sites/` → draw circle/polygon
- [ ] Handle WS `location_update` → move markers + boundary fields
- [ ] `GET /livetracking/alerts/` → list + `unread_count` badge
- [ ] Mark read / read-all APIs

**GPS sender (on duty)**

- [ ] Start GPS only after check-in
- [ ] Send `location_update` until checkout
- [ ] On `error` / `not_on_duty` → stop sending

---

## Out of scope for this doc

- Attendance check-in / checkout APIs (existing mobile flows)
- Configuring alert recipients (web Organisation → Sites only)
- Visitor inbox (`GET /notifications/`) — separate from tracking alerts

---

## Ownership

| Area | Owner |
|------|--------|
| Server WS + alerts + REST | Backend `livetracking/` |
| Web map + inbox reference | `GTMS_NEw` live tracking + tracking alerts |
| Mobile map, notifications, GPS loop | **Mobile app team** |
