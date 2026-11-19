# Support Ticketing API (Phase 1–7 + actions consolidation)

Phase 1: ticket creation, requester “my tickets” list, SUPPORT global queue list, ticket detail, categories.  
Phase 2: pickup / assign / transfer / invalidate / resolve / close, public vs internal comments, activity log, SUPPORT agents list.  
Phase 3: creator-filtered activity embedded on detail, resolve/close (+ lifecycle) notifications via `notifications` app, private attachments upload/download.  
Phase 4: SUPPORT dashboard metrics (global / org / site), list pagination, write rate limits, composite indexes, creator reopen of `RESOLVED`.  
Phase 5: **Mobile Help & Support** contract (Raise Ticket / My Tickets / Detail), FCM deep-link payload fields, optional multipart `file` on create.  
Phase 6: Incident-parity **multi-file assets/audio** on create + close evidence media; assign/close notify **SUPPORT + creator** into `notifications_notificationlog`.  
Phase 7: **SLA / due dates**, SUPPORT/Super Admin reopen of `CLOSED`/`INVALID`, **email channel** for key events (create/assign/close/reopen + SLA).  
**Actions consolidation:** workflow ops (pickup / assign / transfer / resolve / invalidate / reopen / comment) use one JSON endpoint `POST /support/tickets/<id>/actions/`. **Create** and **close** remain separate multipart APIs (media). Detail GET embeds `activity` + `durations`.

**Base path:** `/support/`  
**Auth:** JWT Bearer token (`Authorization: Bearer <access>`) unless noted.  
**Response envelope:** `{ "status": "success"|"error", "message": "...", "data": ... }`  
List endpoints may also include `pagination` (see §12).

**Mobile handoff:** [`MOBILE_SUPPORT_HELP_CONTRACT.md`](./MOBILE_SUPPORT_HELP_CONTRACT.md) · [`../support/MOBILE_API_CURL.md`](../support/MOBILE_API_CURL.md)

## Login / menu notes

- Super Admin fallback pages (when no role pages are configured) include **Support Tickets** (Tickets + Dashboard tabs in the UI).
- Seeded global Role `name=support` has default pages `["Support Tickets"]` and is **not** copied into organisation role lists.
- SUPPORT is identified by `User.role == "support"` (CharField). Only Super Admin (`is_superuser` or role `superadmin` / `super_admin`) may create or assign users with role `support`.
- Frontend route: `/support-tickets` with tabs **Dashboard → Tickets → My Tickets → Reports** (SUPPORT / Super Admin) and **Support Users** (Super Admin only). Non-support roles see **My Tickets**. Legacy `/support-dashboard` redirects to `/support-tickets?tab=dashboard`.
- SUPPORT users may use the header **Location / Site** switcher as view scope only (`organisation_id` / `site_id` query params). Their `User.location` stays null — do not call location-scoped scheduler APIs with a null UUID.

## Status machine

```text
OPEN → IN_PROGRESS → RESOLVED → CLOSED
OPEN | IN_PROGRESS → INVALID
RESOLVED → IN_PROGRESS | OPEN          (creator or SUPPORT)
CLOSED | INVALID → OPEN | IN_PROGRESS  (SUPPORT / Super Admin + comment)
```

- Create → `OPEN`, unassigned; `due_at` set from priority SLA (or explicit `due_at`)  
- Pickup / first assign → `IN_PROGRESS` + assignee  
- Transfer/reassign → owner change (mandatory comment); status usually unchanged  
- Resolve → `RESOLVED` + summary  
- Close → `CLOSED` (SUPPORT closes)  
- Invalidate → `INVALID` + customer-visible reason  
- Creator reopen → `RESOLVED` → `IN_PROGRESS` (if assignee) or `OPEN`; clears `resolved_at`  
- SUPPORT / Super Admin reopen → `RESOLVED` / `CLOSED` / `INVALID` → `OPEN` or `IN_PROGRESS` (`CLOSED`/`INVALID` require comment; optional `target_status`)  
- SLA: see §23  

---

## 1. List ticket categories

- **Method / path:** `GET /support/categories/`
- **Auth:** Authenticated
- **Params:** none
- **Body:** none

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/categories/" \
  -H "Authorization: Bearer <access_token>"
```

### Success (`200`)

```json
{
  "status": "success",
  "message": "Categories fetched",
  "data": [
    {
      "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
      "name": "Attendance / GPS",
      "is_active": true,
      "sort_order": 10
    }
  ]
}
```

**Seeded defaults:** Attendance / GPS, Check-in / Check-out, Live Tracking, App / Login, Other.

---

## 2. Create ticket

- **Method / path:** `POST /support/tickets/`
- **Auth:** Authenticated (any role)
- **Body (JSON):**

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `subject` | string | yes | max 255 |
| `description` | string | yes | |
| `category_id` | UUID | yes | must be an active category |
| `priority` | string | no | `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` (default `MEDIUM`) |
| `due_at` | datetime | no | explicit SLA due; otherwise derived from priority hours |
| `site_id` | UUID | no | must belong to caller org / be accessible |
| `idempotency_key` | string | no | accepted; reserved for future use |
| `assets` / `photo` / `video` | file(s) | no | **multipart only**; multi-file image/video (Incident parity). Aliases: `asset`, `photos`, `images`, `image`, `videos` |
| `audio` / `description_audio` | file(s) | no | **multipart only**; multi-file audio (Incident `description_audio` parity). Alias: `audios` |
| `file` / `attachment` / `files` | file(s) | no | **multipart only**; legacy generic upload (kind inferred). Same size/type rules as §15 |

Organisation is taken from the authenticated user’s `location`. Ticket number format: `SP-YYYY-#####`. Initial status: `OPEN`.  
`Content-Type`: `application/json` **or** `multipart/form-data` (Raise Ticket with media).  
Attachments created on raise store `purpose=CREATE` and `kind` = `ASSET` \| `AUDIO` \| `FILE`. Max **10** media files per request.

### cURL

```bash
curl -X POST "http://127.0.0.1:8000/support/tickets/" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "GPS not updating",
    "description": "Check-in succeeds but live tracking stays stale.",
    "category_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "priority": "HIGH"
  }'
```

### Success (`201`)

```json
{
  "status": "success",
  "message": "Ticket created",
  "data": {
    "id": "11111111-2222-3333-4444-555555555555",
    "ticket_number": "SP-2026-00001",
    "subject": "GPS not updating",
    "status": "OPEN",
    "priority": "HIGH",
    "comments": [],
    "activity": [],
    "attachments": []
  }
}
```

---

## 3. My tickets list

- **Method / path:** `GET /support/tickets/my/`
- **Auth:** Authenticated
- **Query params:** `status` (optional)

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/tickets/my/?status=OPEN" \
  -H "Authorization: Bearer <access_token>"
```

---

## 4. SUPPORT ticket queue

- **Method / path:** `GET /support/support/tickets/`
- **Auth:** SUPPORT (`User.role == "support"`) or Super Admin (`is_superuser`)
- **Query params:** `status`, `organisation_id` / `location_id`, `site_id`, `assignee` (`me` \| `unassigned`)

Cap: first 200 tickets. Non-SUPPORT → **403**.

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/support/tickets/?status=OPEN&assignee=unassigned" \
  -H "Authorization: Bearer <support_access_token>"
```

---

## 5. Ticket detail

- **Method / path:** `GET /support/tickets/<uuid:id>/`
- **Auth:** Creator or SUPPORT / Super Admin

Creators see **PUBLIC** comments and `is_public=true` activity only. SUPPORT sees INTERNAL notes too. Invalidation reason is always visible to the creator.

**Embedded in one response (no separate activity/timeline fetches required for the web UI):**

| Field | Type | Notes |
|-------|------|-------|
| `comments` | array | visibility-filtered |
| `activity` | array | creator-filtered (`is_public=true` only for creators); SUPPORT sees full log |
| `attachments` | array | |
| `durations` | object | response-timeline segments + compact labels (`open_to_assign`, `assign_to_resolve`, `resolve_to_close`, `total`, `*_ms`, `segments[]`, `is_complete`) |
| `resolved_at` / `closed_at` | datetime \| null | |

Activity and comments come from this detail response — there is no separate documented activity or comment-list GET.

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/tickets/11111111-2222-3333-4444-555555555555/" \
  -H "Authorization: Bearer <access_token>"
```

---

## 6. Combined ticket actions

- **Method / path:** `POST /support/tickets/<uuid:id>/actions/`
- **Auth:** Depends on `action` (see below)
- **Content-Type:** `application/json` only
- **Discriminator:** `action` (string, required)

This is the documented API for pickup / assign / transfer / resolve / invalidate / reopen / comment.

**Not included here (remain separate multipart APIs):**

- `POST /support/tickets/` — create / raise (optional assets/audio)
- `POST /support/tickets/<id>/close/` — close with optional evidence media

### Common success

Most actions return **200** (comment **201**) with full ticket **detail** in `data` (same shape as §5, including `activity` + `durations`).

### Common errors

| Status | When |
|--------|------|
| `400` | Missing/invalid fields, unknown `action`, workflow validation (`data.code`) |
| `403` | Role not allowed for that action |
| `404` | Ticket not found |
| `409` | Pickup conflict (`already_assigned` / `already_owned`) |

### Actions

#### `pickup`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"pickup"` |

- **Auth:** SUPPORT / Super Admin  
- Locks row; unassigned `OPEN`/`IN_PROGRESS` → claim + `IN_PROGRESS`. Conflict → **409**.

```json
{ "action": "pickup" }
```

#### `assign`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"assign"` |
| `assignee_id` | UUID | yes | must be SUPPORT role user |
| `comment` | string | reassign only | required when ticket already has an assignee |

First assign on `OPEN` also moves status to `IN_PROGRESS`.

```json
{ "action": "assign", "assignee_id": "<uuid>", "comment": "Please take this" }
```

#### `transfer`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"transfer"` |
| `assignee_id` | UUID | yes | |
| `comment` | string | yes | always required |

```json
{ "action": "transfer", "assignee_id": "<uuid>", "comment": "Handing over" }
```

#### `resolve`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"resolve"` |
| `resolution` | string | yes* | preferred; aliases: `summary`, `resolution_summary`, `comment` |

\*Empty summary is rejected by the workflow service. Only `IN_PROGRESS` → `RESOLVED`. Sets `resolved_at`.

```json
{ "action": "resolve", "resolution": "Fixed on device reboot" }
```

#### `invalidate`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"invalidate"` |
| `reason` | string | yes* | alias: `comment` |

Allowed from `OPEN` or `IN_PROGRESS` → `INVALID`. Reason is stored on the ticket and visible to the creator.

```json
{ "action": "invalidate", "reason": "Duplicate of SP-2026-00001" }
```

#### `reopen`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"reopen"` |
| `comment` | string | conditional | required for SUPPORT on `CLOSED`/`INVALID`; alias `reason` |
| `target_status` | string | no | `OPEN` \| `IN_PROGRESS` (SUPPORT); alias `status` |

| Actor | Allowed from | Result |
|-------|--------------|--------|
| Creator | `RESOLVED` | `IN_PROGRESS` if assignee set, else `OPEN` |
| Creator | `CLOSED` / `INVALID` | **400** `cannot_reopen_closed` / `cannot_reopen_invalid` |
| SUPPORT / Super Admin | `RESOLVED` | same default as creator, or `target_status` |
| SUPPORT / Super Admin | `CLOSED` / `INVALID` | `OPEN` or `IN_PROGRESS` (**comment required**); unassigned + `IN_PROGRESS` claims the acting agent |

- Clears `resolved_at`; clears `closed_at` when reopening from `CLOSED`
- Activity action: `REOPENED` (public); notifies creator / assignee / SUPPORT as needed + email
- Rate limit scope: `support_reopen` (30/hour production)

```json
{ "action": "reopen", "target_status": "OPEN", "comment": "Customer called back" }
```

#### `comment`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `action` | string | yes | `"comment"` |
| `body` | string | yes | |
| `visibility` | string | no | `PUBLIC` (default) \| `INTERNAL` (SUPPORT only) |

Blocked for `CLOSED` / `INVALID`. Success **201** with full detail in `data`.

```json
{ "action": "comment", "body": "Still happening", "visibility": "PUBLIC" }
```

### cURL

```bash
curl -X POST "http://127.0.0.1:8000/support/tickets/<id>/actions/" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{"action": "pickup"}'
```

---

## 7. Close (multipart — stays separate)

- **Method / path:** `POST /support/tickets/<uuid:id>/close/`
- **Auth:** SUPPORT
- **Body:** `comment` (optional)
- **Content-Type:** `application/json` **or** `multipart/form-data`
- **Note:** Intentionally **not** part of `/actions/` so close evidence media can stay multipart (same pattern as create).

Optional proof/evidence media (same field names as create: `assets` / `photo` / `audio` / `file`, etc.) are attached in the same transaction with `purpose=CLOSE` before status becomes `CLOSED`.

Only `RESOLVED` → `CLOSED`. Sets `closed_at`.

### cURL

```bash
curl -X POST "http://127.0.0.1:8000/support/tickets/<id>/close/" \
  -H "Authorization: Bearer <support_access_token>" \
  -H "Content-Type: application/json" \
  -d '{"comment": "Confirmed with requester"}'
```

```bash
curl -X POST "http://127.0.0.1:8000/support/tickets/<id>/close/" \
  -H "Authorization: Bearer <support_access_token>" \
  -F "comment=Confirmed with requester" \
  -F "assets=@/path/to/proof.png" \
  -F "audio=@/path/to/confirm.mp3"
```

---

## 8. List SUPPORT agents

- **Method / path:** `GET /support/support/agents/`
- **Auth:** SUPPORT
- **Params:** none

Returns active users with `role=support` for assign/transfer pickers.

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/support/agents/" \
  -H "Authorization: Bearer <support_access_token>"
```

### Success (`200`)

```json
{
  "status": "success",
  "message": "Support agents fetched",
  "data": [
    { "id": "...", "name": "Support A", "email": "support-a@example.com" }
  ]
}
```

---

## 9. Upload attachment (private)

- **Method / path:** `POST /support/tickets/<uuid:id>/attachments/`
- **Auth:** Creator or SUPPORT
- **Content-Type:** `multipart/form-data`

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `file` | file | yes | alias `attachment` also accepted |
| `comment_id` | UUID | no | optional link to an existing comment |

**Limits:** max **10 MB**; allowed extensions: images/video (`.jpg` `.jpeg` `.png` `.gif` `.webp` `.mp4` `.mov` `.webm`), audio (`.mp3` `.wav` `.m4a` `.aac` `.ogg` `.oga` `.3gp` `.amr`), docs (`.pdf` `.txt` `.csv` `.doc` `.docx` `.xls` `.xlsx`).  
Blocked for `CLOSED` / `INVALID` (use close multipart for close evidence). Writes public activity `ATTACHMENT_ADDED`. Response includes `kind` (`ASSET` \| `AUDIO` \| `FILE`) and `purpose` (`CREATE` \| `CLOSE` \| `GENERAL`). Files are stored under `support_tickets/<ticket_id>/` and are **not** exposed as public MEDIA URLs — use the download endpoint.

### cURL

```bash
curl -X POST "http://127.0.0.1:8000/support/tickets/<id>/attachments/" \
  -H "Authorization: Bearer <access_token>" \
  -F "file=@/path/to/screenshot.png"
```

### Success (`201`)

```json
{
  "status": "success",
  "message": "Attachment uploaded",
  "data": {
    "id": "...",
    "file_name": "screenshot.png",
    "mime_type": "image/png",
    "size_bytes": 12345,
    "created_on": "2026-11-18T16:05:00.000000Z",
    "comment": null,
    "uploaded_by": "...",
    "uploaded_by_name": "Jane Admin",
    "download_url": "/support/tickets/<ticket_id>/attachments/<attachment_id>/download/"
  }
}
```

---

## 10. Download attachment (private)

- **Method / path:** `GET /support/tickets/<uuid:id>/attachments/<uuid:attachment_id>/download/`
- **Auth:** Creator or SUPPORT
- **Response:** binary file stream (`Content-Disposition: attachment`)

Unrelated users → **403**. Missing attachment → **404**.

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/tickets/<id>/attachments/<attachment_id>/download/" \
  -H "Authorization: Bearer <access_token>" \
  -o screenshot.png
```

---

## 11. Notifications (Phase 3 + Phase 6 parity)

Ticket events write `notifications.NotificationLog` rows (table `notifications_notificationlog`) and attempt FCM when device tokens exist via `support/notifications.py` → `notifications.services.notify_user`. Failures never break ticket APIs.

**Email channel (Phase 7):** for create / assign / close / reopen / SLA warning / SLA breach, the same recipients also receive plain-text email via Django `EmailMessage` + `DEFAULT_FROM_EMAIL` (same SMTP stack as report emails). Email failures are logged only and never break ticket APIs.

| Event | `NotificationLog.type` | Recipients | Email |
|-------|------------------------|------------|-------|
| Ticket created | `support_ticket_created` | Active SUPPORT agents (excluding creator) | yes |
| Pickup / assign / transfer | `support_ticket_assigned` | **Ticket creator AND active SUPPORT agents** (excludes acting agent) | yes |
| Resolve | `support_ticket_resolved` | Ticket creator | no |
| Close | `support_ticket_closed` | **Ticket creator AND active SUPPORT agents** (excludes acting agent) | yes |
| Invalidate | `support_ticket_invalidated` | Ticket creator | no |
| PUBLIC comment | `support_ticket_comment` | Other party (creator ↔ assignee) | no |
| Attachment uploaded | `support_ticket_attachment` | Other party (creator ↔ assignee) | no |
| Reopen | `support_ticket_reopened` | Creator (if not actor) + assignee + SUPPORT as needed | yes |
| SLA approaching | `support_ticket_sla_warning` | Active SUPPORT agents (once per ticket) | yes |
| SLA breached | `support_ticket_sla_breach` | Active SUPPORT agents (once per ticket) | yes |

Payload `data` always includes `ticket_id`, `ticket_number`, `subject`, `status`, `priority`, `due_at` (when set), plus mobile deep-link fields:

| Key | Value |
|-----|-------|
| `click_action` | `FLUTTER_NOTIFICATION_CLICK` |
| `screen` | `support_ticket_detail` |
| `route` | `/help-support/tickets/<ticket_id>` |

Inbox listing remains `GET /notifications/`.

---

## 12. Pagination (Phase 4+)

Server-side pagination on all Support **list** endpoints (web tables use the same `page` / `page_size` query params and read the `pagination` object).

| Endpoint | Default `page_size` |
|----------|---------------------|
| `GET /support/tickets/my/` | `25` |
| `GET /support/support/tickets/` | `50` |
| `GET /support/reports/` | `50` |
| `GET /support/support-users/` | `25` |

| Param | Default | Max |
|-------|---------|-----|
| `page` | `1` | — |
| `page_size` | see table | `100` |

Success responses keep `data` as the **array for the current page** (backward compatible) and add:

```json
{
  "pagination": {
    "page": 1,
    "page_size": 25,
    "total": 42,
    "total_pages": 2
  }
}
```

**Not paginated (by design):** dashboard metrics (`GET /support/dashboard/`), category list, agent picker (`GET /support/support/agents/`), ticket detail, and report Excel/PDF exports (exports return the full filtered set).

Queue also accepts `priority` and `overdue=true|1` in addition to existing `status`, `organisation_id` / `location_id`, `site_id`, `assignee=me|unassigned`.  
`GET /support/tickets/my/` also accepts `overdue=true|1`.

List/detail ticket objects include `due_at`, `is_overdue`, and `sla_hours`.

---

## 13. Support dashboard metrics (Phase 4)

- **Method / path:** `GET /support/dashboard/`
- **Auth:** SUPPORT / Super Admin
- **Query:** optional `organisation_id` (or `location_id`), `site_id` to narrow totals and breakdowns

### Success (`200`) — `data` shape

```json
{
  "totals": {
    "all": 42,
    "by_status": {
      "OPEN": 10,
      "IN_PROGRESS": 12,
      "RESOLVED": 5,
      "CLOSED": 12,
      "INVALID": 3
    },
    "by_priority": {
      "LOW": 4,
      "MEDIUM": 20,
      "HIGH": 14,
      "CRITICAL": 4
    },
    "unassigned_open": 6,
    "my_assigned_active": 3,
    "overdue_active": 2,
    "due_soon_active": 1
  },
  "organisations": [
    {
      "organisation_id": "...",
      "organisation_name": "Acme",
      "total": 10,
      "by_status": { "OPEN": 2, "IN_PROGRESS": 3, "RESOLVED": 1, "CLOSED": 3, "INVALID": 1 }
    }
  ],
  "sites": [
    {
      "site_id": "...",
      "site_name": "HQ",
      "organisation_id": "...",
      "organisation_name": "Acme",
      "total": 5,
      "by_status": { "OPEN": 1, "IN_PROGRESS": 2, "RESOLVED": 0, "CLOSED": 2, "INVALID": 0 }
    }
  ],
  "filters": { "organisation_id": null, "site_id": null }
}
```

Non-SUPPORT → **403**.

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/support/dashboard/?organisation_id=<uuid>" \
  -H "Authorization: Bearer <access_token>"
```

---

## 14. Rate limits & indexes (Phase 4)

**Scoped write throttles** (DRF `ScopedRateThrottle`, production rates):

| Scope | Applied on | Rate |
|-------|------------|------|
| `support_create` | `POST /support/tickets/` | 60/hour |
| `support_comment` | `POST .../actions/` with `action=comment` | 120/hour |
| `support_attachment` | `POST .../attachments/` | 60/hour |
| `support_reopen` | `POST .../actions/` with `action=reopen` | 30/hour |

**DB indexes** on `support_ticket`: `(status, created_on)`, `(organisation, status)`, `(site, status)`, `(current_assignee, status)`, `(created_by, created_on)`, `(priority, status)`, `(organisation, site, status)`.

**Concurrency:** pickup / assign / transfer / resolve / close / reopen / attachments use `select_for_update`; conflicting pickup returns **409** (`already_assigned` / `already_owned`).

---

## Path summary

| Method | Path | Who |
|--------|------|-----|
| `GET` | `/support/categories/` | Any authenticated user |
| `GET` | `/support/dashboard/` | SUPPORT / Super Admin |
| `GET` | `/support/reports/` | SUPPORT / Super Admin (paginated; filters: status, priority, org/site, assignee, overdue, date_filter / date_from / date_to) |
| `GET` | `/support/reports/export-excel/` | SUPPORT / Super Admin |
| `GET` | `/support/reports/export-pdf/` | SUPPORT / Super Admin |
| `GET` | `/support/support-users/` | Super Admin only |
| `POST` | `/support/support-users/` | Super Admin only (create SUPPORT user; location always null) |
| `PATCH` | `/support/support-users/<id>/` | Super Admin only (name / email / password / is_active) |
| `POST` | `/support/tickets/` | Any authenticated user (multipart create) |
| `GET` | `/support/tickets/my/` | Any authenticated user (own tickets) |
| `GET` | `/support/support/tickets/` | SUPPORT / Super Admin |
| `GET` | `/support/tickets/<id>/` | Creator or SUPPORT / Super Admin (embeds `comments`, `activity`, `attachments`, `durations`) |
| `POST` | `/support/tickets/<id>/actions/` | Combined JSON: `pickup` \| `assign` \| `transfer` \| `resolve` \| `invalidate` \| `reopen` \| `comment` |
| `POST` | `/support/tickets/<id>/attachments/` | Creator or SUPPORT |
| `GET` | `/support/tickets/<id>/attachments/<attachment_id>/download/` | Creator or SUPPORT (`?inline=1` for preview Content-Type) |
| `POST` | `/support/tickets/<id>/close/` | SUPPORT — multipart separate (close evidence media) |
| `GET` | `/support/support/agents/` | SUPPORT |

**Header scope (web):** SUPPORT and Super Admin use the same Location/Site header switcher. Queue, dashboard, and reports accept `organisation_id` (or `location_id`) and `site_id`; values `all` / `null` / empty are ignored so “All organisations” never hits `/by-location/null/`-style filters. SUPPORT users remain global (`User.location=null`); header scope is view-only filtering.

## 15. Mobile Help & Support (Phase 5 + Phase 6 media)

Flutter app lives **outside** this monorepo. Contract delivers:

1. Requester screen contract: Help & Support hub → Raise Ticket / My Tickets / Detail (+ reopen).  
2. Multipart Raise Ticket media: multi-file `assets` / `audio` (Incident parity); legacy `file` still works.  
3. FCM / inbox deep-link keys (`click_action`, `screen`, `route`) on all support notification payloads.  
4. Curl cookbook: [`../support/MOBILE_API_CURL.md`](../support/MOBILE_API_CURL.md).  
5. Assign/close notifications go to creator **and** SUPPORT (Phase 6).

Site prefill uses existing `GET /auth/v5/my-sites/` (`current_site.id` → `site_id`). SUPPORT queue/dashboard stay web-only in v1. Close evidence media is SUPPORT web/API (`POST .../close/` multipart).

---

## 16. SLA / due dates (Phase 7)

On create, `due_at` is set from priority defaults unless an explicit `due_at` is provided.

| Priority | Default hours (`settings.SUPPORT_SLA_HOURS`) |
|----------|-----------------------------------------------|
| `CRITICAL` | 4 |
| `HIGH` | 8 |
| `MEDIUM` | 24 |
| `LOW` | 72 |

**API surfaces**

- List/detail: `due_at`, `is_overdue` (active statuses only: `OPEN` / `IN_PROGRESS`), `sla_hours`
- Filters: `overdue=true` on my-list and SUPPORT queue
- Dashboard totals: `overdue_active`, `due_soon_active` (due within 2 hours)

**SLA notifications:** when SUPPORT loads the dashboard, approaching/breached active tickets are notified **once** each (`sla_warning_notified_at` / `sla_breach_notified_at`) via NotificationLog + email to SUPPORT agents.

---

## 17. Reports + exports

- **Preview:** `GET /support/reports/` — same list shape as the SUPPORT queue (paginated; default `page_size=50`, see §12); SUPPORT / Super Admin only.
- **Excel:** `GET /support/reports/export-excel/` → `.xlsx` download (full filtered set, not paginated)
- **PDF:** `GET /support/reports/export-pdf/` → `.pdf` download (full filtered set, not paginated)

**Shared filters (query params):** `status`, `priority`, `organisation_id` (alias `location_id`), `site_id`, `assignee` (`me` \| `unassigned`), `assignee_id`, `overdue=true`, `date_from` / `date_to` (or `start_date` / `end_date`), plus `page` / `page_size` on the JSON preview only.

Header Location/Site on the web app maps to `organisation_id` / `site_id`.

### Attachment preview note

`GET /support/tickets/<id>/attachments/<attachment_id>/download/?inline=1` returns `Content-Disposition: inline` with a guessed media `Content-Type` (from stored mime or filename). Default (no `inline`) remains `attachment` for Download. Empty / `octet-stream` mime is resolved from the file extension so blob previews render in `<img>` / `<video>` / `<audio>`.

---

## 18. Support Users (Super Admin)

Dedicated APIs (do **not** reuse Users v5 for this tab):

| Method | Path | Notes |
|--------|------|--------|
| `GET` | `/support/support-users/` | List SUPPORT-role users (`include_inactive=0` to hide inactive); paginated (`page` / `page_size`, default 25 — see §12) |
| `POST` | `/support/support-users/` | Body: `name`, `email`, `password` — role fixed to `support`, `location` null |
| `PATCH` | `/support/support-users/<id>/` | Optional `name`, `email`, `password`, `is_active` |

`GET /support/support/agents/` remains the active-agent picker for assign/transfer.

---

## Out of scope / deferred

Org-wide customer ticket views for non-SUPPORT; in-repo Flutter UI (separate mobile codebase); advanced SLA escalation policies / business-hours calendars.
