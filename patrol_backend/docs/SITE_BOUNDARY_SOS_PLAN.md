# Site Boundary & SOS Alert Plan

**Status:** Planned — not yet implemented  
**Last updated:** 2026-08-31

## In one sentence

**Admin draws a site boundary (circle or polygon). When a user checks in for attendance, we track their live location until checkout. If they leave that site boundary during that time, admins get an automatic SOS/breach alert.**

---

## Implementation checklist

- [ ] Add per-site boundary fields (circle OR polygon) + breach alert model + migrations
- [ ] Build boundary check logic (circle + polygon) with GPS jitter buffer
- [ ] Only accept GPS and run boundary checks between attendance check-in and checkout
- [ ] Send `boundary_breach` alerts over WebSocket + save alert history
- [ ] Organization page — admin picks circle OR polygon per site on map
- [ ] Live map shows boundaries, inside/outside status, and breach alerts
- [ ] Site Settings global toggle + buffer/cooldown; per-site `boundary_enabled` on LocationSite
- [ ] Mobile app sends GPS only after check-in; stops at checkout

---

## Two different things (do not mix them)

| | Attendance radius (today) | Site boundary (new) |
|---|---|---|
| **What** | Small circle around site center | Circle **or** polygon drawn by admin |
| **When checked** | Only when user punches check-in / checkout | **Every GPS update** while checked in |
| **Purpose** | Allow or block attendance punch | Know if user is still at the site during shift |
| **Who sets it** | Org setting `attendance_distance` (e.g. 150m) | Admin per site on map |

Attendance radius stays **unchanged**. This plan adds the second feature only.

---

## When monitoring starts and stops

```
CHECK-IN  ──────────────────────────────────────►  CHECKOUT
   │                                                    │
   │  ✓ Mobile sends GPS                               │
   │  ✓ Server checks boundary                         │  ✗ Stop GPS tracking
   │  ✓ Show user on live map                          │  ✗ Stop boundary checks
   │  ✓ Send breach alert if user leaves site          │  ✗ Clear "on duty" status
   └────────────────────────────────────────────────────┘
```

- **Start:** User successfully marks **attendance check-in** (open session).
- **Stop:** User marks **checkout** (session closed).
- **Before check-in or after checkout:** No boundary monitoring, no breach alerts.
- **Who:** Any mobile user with an open check-in session (not only Guard role).

---

## What admin does (per site)

In Organization → Authorized Sites, admin configures **one boundary type per site**:

### Option A — Circle
- Pick center point on map (or use existing site lat/lng)
- Set radius in meters (e.g. 500m)

### Option B — Polygon
- Draw shape on map (click corners, close the shape)

**One site = one type only.** Admin chooses **either** circle **or** polygon — not both at the same time.

---

## Phase 1 — Save boundary on each site

**File:** `scheduler/models.py` — extend `LocationSite`

| Field | Type | Meaning |
|-------|------|---------|
| `boundary_type` | string | `none` / `circle` / `polygon` |
| `boundary_radius_m` | integer, nullable | Used only when `boundary_type = circle` |
| `boundary_polygon` | JSONField, nullable | Used only when `boundary_type = polygon` |
| `boundary_enabled` | boolean | Per-site on/off — keeps shape but stops monitoring |

**New model:** `BoundaryBreachAlert` — who left which site, when, linked to check-in session.

**Update:** `UserLiveLocation` — `is_inside_boundary`, assigned site from check-in.

### Polygon storage (MySQL)

Database: **MySQL** with Django `JSONField` (same pattern as `Role.pages`, `Assignment.checkpoints`).

```python
boundary_polygon = models.JSONField(null=True, blank=True)
```

**Recommended format** (plain array):

```json
[
  [3.08510, 101.69050],
  [3.08580, 101.69120],
  [3.08600, 101.69000],
  [3.08510, 101.69050]
]
```

| Rule | Detail |
|------|--------|
| **Order** | `[latitude, longitude]` — same as site lat/lng and Google Maps |
| **Not GeoJSON** | GeoJSON uses `[lng, lat]` — do not use here |
| **Minimum points** | At least 3 corners |
| **Closed ring** | Last point = first point; backend auto-closes if open |
| **Circle sites** | `boundary_polygon` = null; use lat/lng + `boundary_radius_m` |

**Example API (polygon site):**

```json
{
  "name": "Tower A",
  "latitude": 3.08517,
  "longitude": 101.69072,
  "boundary_type": "polygon",
  "boundary_enabled": true,
  "boundary_radius_m": null,
  "boundary_polygon": [
    [3.08510, 101.69050],
    [3.08580, 101.69120],
    [3.08600, 101.69000],
    [3.08510, 101.69050]
  ]
}
```

---

## Phase 2 — Boundary check logic

**New file:** `patrol_backend/utils/boundary_utils.py`

- Circle: distance from center ≤ radius
- Polygon: point inside drawn shape
- GPS buffer (e.g. 15m) to reduce false alerts at edge

---

## Phase 3 — Check-in to checkout gate (backend)

**File:** `livetracking/consumers.py`

On every `location_update`:

1. Is user checked in? (open `AttendanceCheckin`) — if no, ignore GPS
2. Which site? — from `AttendanceCheckin.site`
3. Inside boundary? — compare GPS to circle or polygon
4. Was inside, now outside? — send `boundary_breach` alert
5. Came back inside? — clear active breach (optional re-entry event)

**File:** `livetracking/views.py`

- Live map API: only users with open check-in
- Return `is_inside_boundary`, site name, check-in time

**Manual SOS** (`emergency_alert`) stays separate from automatic boundary breach.

---

## Phase 4 — Admin UI (draw boundary)

**File:** `GTMS_NEw/src/pages/organisation/Organization.jsx`

- Dropdown: Boundary type → None / Circle / Polygon
- Map: circle (center + radius) or polygon (draw corners)
- Google Maps Drawing library (`libraries: ['drawing']`)

---

## Phase 5 — Live map (admin view)

**Files:** `LiveTrackingMap.jsx`, `websocket.jsx`

- Draw site boundaries (circles and polygons)
- Inside/outside status per checked-in user
- `boundary_breach`: toast/banner + red marker
- Active breach alerts list
- User drops off map after checkout

---

## Phase 6 — Mobile app

Mobile code is not in this repo.

| Action | When |
|--------|------|
| Start WebSocket + send GPS | After successful **check-in** |
| Keep sending GPS | Until **checkout** |
| Stop GPS | On **checkout** |
| Manual SOS | Anytime (`emergency_alert`) |

Server enforces check-in window even if mobile sends GPS at wrong times.

---

## Phase 7 — Settings & rollout

### Two levels of enable/disable

| Level | Where | Purpose |
|-------|--------|---------|
| **Global (org)** | `SiteSetting` — Site Settings page | Master switch for entire org |
| **Per site** | `LocationSite.boundary_enabled` | Turn off one site; shape stays saved |

**Decision flow:**

```
org boundary_monitoring_enabled = true?
  → site boundary_enabled = true?
    → boundary_type = circle or polygon?
      → run inside/outside check → breach if outside
```

### Org settings (`SiteSetting` keys)

| Key | Example | Purpose |
|-----|---------|---------|
| `boundary_monitoring_enabled` | `true` / `false` | Global on/off |
| `boundary_exit_buffer_m` | `15` | GPS jitter buffer (meters) |
| `boundary_alert_cooldown_min` | `5` | Minutes between repeat alerts |

### Rollout order

1. Database migration
2. Backend logic (check-in window + boundary check + WebSocket)
3. Site Settings UI
4. Organization UI (draw boundary)
5. Live map
6. Mobile app update

---

## Files to change

| What | Where |
|------|-------|
| Site boundary fields | `scheduler/models.py`, `scheduler/serializers.py` |
| Breach alerts | `livetracking/models.py` |
| Check-in window + boundary check | `livetracking/consumers.py` |
| Live map API | `livetracking/views.py` |
| Draw boundary (admin) | `GTMS_NEw/src/pages/organisation/Organization.jsx` |
| Show boundaries + alerts | `LiveTrackingMap.jsx`, `websocket.jsx` |

---

## Done when

- [ ] Admin can set **circle OR polygon** per site
- [ ] Monitoring runs **only from check-in until checkout**
- [ ] Live map shows **inside / outside** status
- [ ] Leaving boundary triggers **automatic SOS alert**
- [ ] After checkout, user is **no longer tracked**
- [ ] **Global** + **per-site** enable/disable
- [ ] Polygon stored as JSON `[[lat,lng], ...]` on `LocationSite.boundary_polygon`
- [ ] Attendance punch radius is **not changed**
