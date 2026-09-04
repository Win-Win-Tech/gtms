# Site Boundary & Live Tracking Alerts Plan

**Status:** Phase 11 complete — mobile live-tracking contract  
**Last updated:** 2026-09-03

## In one sentence

**Admin enables breach / location-missing alerts globally, draws each site boundary, and configures who is notified when a given role triggers an alert. While a user is checked in until checkout, the system tracks GPS and notifies the recipient roles chosen for that subject’s role (with site access).**

---

## Reference — scope rules

### Attendance radius vs site boundary (do not mix)

| | Attendance radius (today) | Site boundary (new) |
|---|---|---|
| **What** | Small circle at punch time | Circle or polygon drawn by admin |
| **When** | Check-in / checkout punch only | Every GPS update while checked in |
| **Who sets** | Org `attendance_distance` | Admin per site |

Attendance punch radius stays **unchanged**.

### Monitoring window

```
CHECK-IN ──────────────────────────────────────────────► CHECKOUT
   │  GPS + boundary checks + alerts                         │ stop all
```

- **Who sends GPS:** Any mobile user with open check-in (not guard-only).
- **Before check-in / after checkout:** No monitoring, no alerts.

### Two alert types

| Alert | Code | Trigger |
|-------|------|---------|
| Boundary breach | `boundary_breach` | GPS received, position **outside** site boundary |
| Location missing | `location_missing` | Checked in, **no GPS** for configured timeout |

**Manual SOS** (`emergency_alert`) — separate, user-triggered panic.

### Boundary alert state machine

- Alert on **inside → outside** only (not every GPS ping while outside).
- **Outside → inside** resolves active breach.
- **Inside → outside again** = **new alert**.
- Optional **still-outside reminder** after N min (org setting, default **off**).

Store on `UserLiveLocation`: `boundary_state` (`inside`/`outside`), `active_breach_alert_id`, `last_location_at`.

### Alert storage

**Do not use** `notifications.NotificationLog` (visitor-only). Use new tables in `livetracking`:

- `TrackingAlert` — alert history
- `TrackingAlertRecipient` — per-user delivery + read state
- `SiteAlertRecipientConfig` — per site: **subject role** (who triggered) → **recipient role** (who is notified), with flags for breach / location missing

**Recipient delivery (not global):**

| Layer | What it configures |
|-------|--------------------|
| **Org Site Settings** | Timeouts and buffers only (exit buffer, still-outside reminder, location-missing timeout). **No enable switches, no recipient list.** |
| **Per site** | Enable **boundary breach** and/or **location missing** for that site. Then, for each org role (Guard, SO, FO, custom…): if that role crosses / goes missing, which roles receive the alert. |
| **Runtime** | Match `subject_user.role` → configured recipient roles → users in those roles with **site access**. |

Example: Guard crosses → notify SO + FO + Admin. SO crosses → notify Admin only.

### Polygon storage (MySQL JSON on `LocationSite`)

```json
[[3.08510, 101.69050], [3.08580, 101.69120], [3.08600, 101.69000], [3.08510, 101.69050]]
```

Order: `[latitude, longitude]` — not GeoJSON `[lng, lat]`.

---

## Phase 1 — Database & models

**Goal:** Schema for boundaries, alerts, and recipient config.  
**Status:** Ready to implement (blocked in Plan mode — switch to **Agent** mode to apply).

### Tasks

- [x] Extend `LocationSite` in [`scheduler/models.py`](scheduler/models.py):
  - `boundary_type` (`none` / `circle` / `polygon`)
  - `boundary_radius_m`, `boundary_polygon` (JSONField)
  - `boundary_enabled`
- [x] Add to [`livetracking/models.py`](livetracking/models.py):
  - `TrackingAlert`
  - `TrackingAlertRecipient`
  - `SiteAlertRecipientConfig` (site + `subject_role` + `recipient_role` + `notify_boundary_breach` + `notify_location_missing`)
- [x] Extend `UserLiveLocation`:
  - `assigned_site`, `is_inside_boundary`, `boundary_state`, `last_location_at`, `active_breach_alert`
- [x] Migrations: `scheduler/0023_...`, `livetracking/0002_...`
- [x] Serializers: extend `LocationSiteSerializer`; new `livetracking/serializers.py`
- [x] Admin: new `livetracking/admin.py`

### Deliverable

DB ready; no UI or runtime logic yet.

### Phase 1 migration commands (after code merge)

```bash
cd backendnew/gtms/patrol_backend
../venv/bin/python manage.py makemigrations scheduler livetracking
../venv/bin/python manage.py migrate
../venv/bin/python manage.py check
```

---

## Phase 2 — Boundary math & org settings

**Goal:** Reusable geometry + global toggles.

### Tasks

- [x] New [`patrol_backend/utils/boundary_utils.py`](patrol_backend/utils/boundary_utils.py):
  - `is_point_in_circle()`, `is_point_in_polygon()`, `evaluate_site_boundary()`
  - Exit buffer from org setting (reduce GPS jitter)
- [x] `SiteSetting` keys (org-level) — migration `0024_seed_boundary_site_settings`:
  - `boundary_exit_buffer_m`
  - `boundary_still_outside_reminder_min` (0 = off)
  - `location_missing_timeout_min`
  - ~~`boundary_monitoring_enabled`~~ removed in `0027` (unused; enable is per-site)
- [x] Per-site enable (migration `0026`): `breach_alerts_enabled`, `location_missing_alerts_enabled` on `LocationSite`
- [x] Helper: `is_boundary_monitoring_active(org_id, site)` — per-site breach enable + drawable boundary

### Deliverable

Boundary can be evaluated in Python; org can turn feature on/off.

---

## Phase 3 — Django Channels / WebSocket (extend roles & groups)

**Goal:** Upgrade [`livetracking/consumers.py`](livetracking/consumers.py) and frontend WS client — today Channels is **hard-coded to a few roles**; this phase opens it for all on-duty senders and dynamic alert recipients.

### Current limitations (today)

| Area | Current behavior |
|------|------------------|
| **Who can send GPS** | Any authenticated user (no on-duty check); docs say guard-only but not enforced |
| **Who listens (live map)** | `superadmin` → `all_locations`; `admin` / `so` / `fo` → `location_{org_id}`; **guard joins no group** |
| **Roles** | Hard-coded strings — **dynamic/custom org roles not supported** |
| **Site scope** | Org-level groups only — **no per-site alert groups** |
| **Message types** | `location_update`, `emergency_alert` only |
| **REST live map** | `last-known-locations` filters `role=guard` only |

### Target architecture

```mermaid
flowchart TB
  subgraph senders [Senders mobile on duty]
    MobileUser[Any checked-in mobile user]
  end
  subgraph consumer [LocationConsumer]
    Receive[receive location_update]
    Broadcast[broadcast_to_groups]
  end
  subgraph groups [Redis channel groups]
    AllLoc[all_locations]
    OrgLoc["location_{org_id}"]
    SiteAlerts["site_{site_id}_tracking"]
    UserInbox["tracking_user_{user_id}"]
  end
  subgraph listeners [Listeners web]
    Superadmin[superadmin]
    LiveMap[live map viewers]
    AlertRecipient[configured alert recipients]
  end
  MobileUser --> Receive --> Broadcast
  Broadcast --> AllLoc --> Superadmin
  Broadcast --> OrgLoc --> LiveMap
  Broadcast --> SiteAlerts --> AlertRecipient
  Broadcast --> UserInbox --> AlertRecipient
```

### Group membership on `connect()`

| User type | Groups to join |
|-----------|----------------|
| **Superadmin** | `all_locations` |
| **Live map viewer** (admin / SO / FO / roles with Live Tracking menu) | `location_{org_id}` |
| **Alert recipient** (role in `SiteAlertRecipientConfig` + site access) | `tracking_user_{user_id}` **and** `site_{site_id}_tracking` for each allowed site |
| **On-duty mobile sender** | No listen groups required (send-only) — same as guard today |

**Dynamic roles:** Resolve listener eligibility from `Role` + `SiteAlertRecipientConfig`, not hard-coded `['admin','so','fo']`.

### Who can **send** `location_update`

- Any authenticated user with **open `AttendanceCheckin`** (check-in → checkout).
- Reject GPS if not on duty (Phase 4 enforces; stub check in this phase).

### New / updated WebSocket message types

| Direction | `type` | Purpose |
|-----------|--------|---------|
| Client → Server | `location_update` | GPS ping (on-duty only) |
| Client → Server | `emergency_alert` | Manual SOS (unchanged) |
| Server → Client | `location_update` | Live position + `boundary_status`, `assigned_site_id`, `is_inside_boundary` |
| Server → Client | `tracking_alert` | Boundary breach / location missing to **recipients** |
| Server → Client | `emergency_alert` | Manual SOS broadcast (unchanged) |

### Tasks

- [x] Refactor `connect()` / `disconnect()` — dynamic group join via helper `resolve_ws_groups_for_user(user)`
- [x] Add groups: `site_{site_id}_tracking`, `tracking_user_{user_id}`
- [x] On site alert config change: optional regroup connected clients (or reconnect on next login)
- [x] Add outbound handler `tracking_alert(event)` on consumer
- [x] Enrich `location_update` broadcast payload (boundary fields)
- [x] [`GTMS_NEw/src/services/websocket.jsx`](GTMS_NEw/src/services/websocket.jsx): handle `tracking_alert`; support non-guard users in state
- [x] [`livetracking/routing.py`](livetracking/routing.py) — no route change (`ws/live-location/`)
- [x] Document WS contract in plan appendix / mobile doc (Phase 11) — [`MOBILE_LIVE_TRACKING_BOUNDARY_CONTRACT.md`](MOBILE_LIVE_TRACKING_BOUNDARY_CONTRACT.md)

### Deliverable

Channels supports **all on-duty senders**, **dynamic role-based alert recipients**, and **site-scoped alert groups** — not limited to guard + admin/so/fo.

---

## Phase 4 — Check-in gate & live location REST

**Goal:** Server-side on-duty enforcement + REST aligned with new WS model.

### Tasks

- [x] In `handle_location_update`:
  - Reject if no open `AttendanceCheckin`
  - Resolve site from `AttendanceCheckin.site`
  - Update `UserLiveLocation` (`last_location_at`, `assigned_site`, etc.)
- [x] [`livetracking/views.py`](livetracking/views.py):
  - `last-known-locations`: on-duty users (open check-in), **not** `role=guard` only
  - Return boundary status fields
- [x] History API: same on-duty scope

### Deliverable

REST live map matches WebSocket — all on-duty roles visible, not guard-only.

---

## Phase 5 — Alert engine (create, resolve, notify)

**Goal:** Create `TrackingAlert` rows and resolve recipients by role + site access.

### Tasks

- [x] New [`livetracking/alert_service.py`](livetracking/alert_service.py):
  - `create_tracking_alert(...)`, `resolve_tracking_alert(...)`
  - `get_recipients_for_alert(site, alert_type, subject_user=...)` — match subject’s role → recipient roles via `SiteAlertRecipientConfig` + site access
  - `dispatch_tracking_alert_ws(alert, recipients)` — uses Phase 3 groups (`tracking_user_{id}`, `site_{site_id}_tracking`)
- [x] REST API: alerts inbox, mark read, site recipient config CRUD

### Deliverable

Alerts persist in separate table; WS dispatch wired to new groups.

---

## Phase 6 — Boundary breach & location missing (runtime)

**Goal:** Fire alerts at the right times with state machine; push via Phase 3 WebSocket.

### Tasks

- [x] In `handle_location_update` (after Phase 4 gate):
  1. `boundary_utils` → update `boundary_state`
  2. **inside → outside:** create `TrackingAlert` + `dispatch_tracking_alert_ws`
  3. **outside → inside:** resolve breach (**silent** — no WS/FCM)
  4. Update `last_location_at` → resolve `location_missing` (**silent**)
- [x] Optional still-outside reminder (org setting; default **0 = off**)
- [x] Background job: `location_missing` when no GPS past timeout (Celery Beat: `check-location-missing-alerts` every 2 min)
- [x] On checkout: resolve alerts (silent — no WS/FCM); clear boundary state

### Deliverable

Both alert types end-to-end: DB + WebSocket to configured recipients.

---

## Phase 7 — Organization UI (tabs + site management)

**Goal:** Admin configures sites, boundaries, and alert recipients.

### Tasks

- [x] Refactor [`Organization.jsx`](GTMS_NEw/src/pages/organisation/Organization.jsx):

**Tab 1 — Organization**
- Create/edit org: name, address, QR / face / AI flags
- Remove inline “Authorized Sites” list from org dialog

**Tab 2 — Sites**
- Site table per org
- Create / Edit site (dialog or route) with:
  1. Basic: name, lat, lng
  2. Boundary: type dropdown, map (circle or polygon)
  3. Per-site enable: boundary breach alerts, location missing alerts
  4. Alert routing: for each **subject role**, multi-select **recipient roles** for breach and for location missing

- [x] New component e.g. `SiteForm.jsx`, `SiteBoundaryMap.jsx`
- [x] Google Maps Drawing library (`libraries: ['drawing']`)
- [x] Wire to extended `/scheduler/locations/{id}/sites/` + alert config API

### Deliverable

Admins can draw boundaries and configure alert roles per site without touching visitor notifications.

---

## Phase 8 — Site Settings UI (timeouts)

**Goal:** Org-wide **timeouts and buffers only**. Enable is per site.

### Tasks

- [x] [`SiteSettings.jsx`](GTMS_NEw/src/pages/settings/SiteSettings.jsx) + [`BoundaryMonitoringSettings.jsx`](GTMS_NEw/src/pages/settings/BoundaryMonitoringSettings.jsx) — org panel:
  - Exit buffer (meters)
  - Still-outside reminder (minutes, 0 = off)
  - Location missing timeout (minutes)
- [x] Backend seed/default values for new orgs — `site_setting_sync.sync_propagatable_settings_for_location` on org create + settings list

### Deliverable

Org admin can tune thresholds; each site has its own breach / location-missing enable plus subject-role routing.

---

## Phase 7b — Subject-role → recipient routing (revision)

**Goal:** Replace flat “these roles always get alerts” with routing by who triggered the alert.

### Tasks

- [x] Migrate `SiteAlertRecipientConfig`: `subject_role` + `recipient_role` (unique per site pair); clear legacy flat rows
- [x] `get_recipients_for_alert(site, alert_type, subject_user)` uses subject role
- [x] API PUT/GET shape: `{ subject_role, recipient_role, notify_boundary_breach, notify_location_missing }`
- [x] Site form: per-site enable for breach / location missing; per subject role → recipient checkboxes
- [x] Gate alert creation by **site** (`breach_alerts_enabled` / `location_missing_alerts_enabled`)
- [x] WS join groups use `recipient_role`

### Deliverable

Guard breach notifies only the roles selected for Guard; SO breach notifies only the roles selected for SO; same for all dynamic roles.

---

## Phase 9 — Header icons & tracking alerts inbox

**Goal:** Separate visitor notifications from tracking alerts in UI.

### Tasks

- [x] [`DashboardLayout.jsx`](GTMS_NEw/src/pages/DashboardLayout.jsx):
  - **Visitor icon** → `/notifications` (existing `NotificationLog`)
  - **Tracking alert icon** → `/tracking-alerts` (`TrackingAlert`)
  - Separate unread badges
- [x] New `TrackingAlertHistory.jsx` + `api/trackingAlerts.js`
- [x] List, filter by type/status, mark read, link to live map
- [x] WebSocket listener → `gtms-tracking-alerts-updated` for badge refresh
- [x] Clarify visitor page title/icon (visitor-only)

### Deliverable

Recipients see tracking alerts in dedicated inbox; visitor flow unchanged.

---

## Phase 10 — Live map UI

**Goal:** Visual boundaries, status, and real-time alerts on map.

### Tasks

- [x] [`LiveTrackingMap.jsx`](GTMS_NEw/src/pages/livetracking/LiveTrackingMap.jsx):
  - Load site boundaries → render `Circle` / `Polygon` overlays
  - Marker colors: inside / outside / location missing
  - Breach / GPS-missing filter cards
- [x] [`websocket.jsx`](GTMS_NEw/src/services/websocket.jsx):
  - Handle `tracking_alert`, update guard flags
  - Preserve alert flags across `location_update` (clear missing on GPS; sync breach from boundary_status)
  - Toast on new alert (live map listens to `gtms-tracking-alerts-updated`)
- [x] Deep-link auto-pan from tracking inbox (`?user=` / `?site=`)

### Deliverable

Ops team sees boundaries and alert state on live map.

---

## Phase 11 — Mobile app contract

**Goal:** Document and align mobile team (code outside this repo).

### Tasks

- [x] Written contract: [`MOBILE_LIVE_TRACKING_BOUNDARY_CONTRACT.md`](MOBILE_LIVE_TRACKING_BOUNDARY_CONTRACT.md)
  - Connect WS + start GPS **after check-in**
  - Send `location_update` every N seconds until **checkout**
  - Stop GPS on checkout
  - Manual SOS: `emergency_alert` unchanged
- [x] Server rejects GPS outside check-in window regardless (`not_on_duty`) — already enforced in `LocationConsumer`

### Deliverable

Mobile behavior matches server assumptions.

---

## Phase summary

| Phase | Focus | Depends on |
|-------|--------|------------|
| **1** | DB & models | — |
| **2** | Boundary utils + org SiteSettings keys | 1 |
| **3** | **Django Channels / WebSocket** — groups, dynamic roles, new message types | 1 ✅ |
| **4** | Check-in gate + live location REST | 1, 3 ✅ |
| **5** | Alert engine + REST inbox APIs | 1, 3 ✅ |
| **6** | Boundary breach + location-missing runtime | 2, 4, 5 ✅ |
| **7** | Organization tabs + site/boundary/alert UI | 1, 5 ✅ |
| **8** | Site Settings global toggles UI | 2 |
| **9** | Header dual icons + tracking inbox (+ WS `tracking_alert`) | 5, 6 |
| **10** | Live map overlays + WS UX | 3, 6 |
| **11** | Mobile contract | 3, 4, 6 |

**Suggested build order:** 1 → 2 → **3** → 4 → 5 → 6 → (7 + 8 parallel) → 9 → 10 → 11

**Note:** Phase **3** is the dedicated socket phase — do not skip; Phases 5–6 and 9–10 depend on the new groups and `tracking_alert` messages.

---

## Files to change (by area)

| Area | Files |
|------|--------|
| Models | `scheduler/models.py`, `livetracking/models.py` |
| Boundary | `patrol_backend/utils/boundary_utils.py` |
| Alerts | `livetracking/alert_service.py`, `livetracking/views.py`, `livetracking/urls.py` |
| WebSocket / Channels | `livetracking/consumers.py`, `livetracking/routing.py`, `websocket.jsx` |
| Site access | `authapp/site_access.py` (reuse) |
| Org / sites UI | `Organization.jsx`, `SiteForm.jsx`, `SiteBoundaryMap.jsx` |
| Settings UI | `SiteSettings.jsx` |
| Header / inbox | `DashboardLayout.jsx`, `TrackingAlertHistory.jsx`, `api/trackingAlerts.js` |
| Live map | `LiveTrackingMap.jsx`, `websocket.jsx` |

**Do not modify** `notifications.NotificationLog` for tracking alerts.  
Tracking alerts use **WebSocket + FCM** to recipients; inbox remains `TrackingAlert` / `TrackingAlertRecipient` (`/livetracking/alerts/`). FCM reuses `DeviceToken` via `notifications.services.send_push_to_user`.

---

## Done when

- [x] Phases 9–10 complete (recipients notified in UI + live map)
- [x] Phase 11 documented for mobile
- [x] Circle OR polygon per site; check-in → checkout only
- [x] Boundary breach + location missing alerts with correct state machine
- [x] Per-site **subject role → recipient roles** config; delivery respects site access
- [x] Per-site enable for breach / location-missing; delivery respects site access and subject-role routing
- [x] Separate `TrackingAlert` table; dual header icons
- [x] Attendance punch radius unchanged
