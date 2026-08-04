# Visitor Management + Notifications — Mobile API (curl)

Canonical mobile contract for **Visitor Management** and **Push / Inbox Notifications**.

| | |
|---|---|
| **Auth** | `Authorization: Bearer <JWT>` |
| **Base URL** | `http://localhost:8000` (replace with your server) |
| **Content** | JSON unless noted (`multipart/form-data` for images) |
| **Errors** | Almost always `{ "error": "<message>" }` with HTTP 4xx/5xx |

Related shorter notes:

- Visitor AI OCR setup → [`visitor/docs/VISITOR_AI_OCR.md`](../visitor/docs/VISITOR_AI_OCR.md)

---

## API index

All endpoints documented in this file:

| Method | Endpoint | Use |
|--------|----------|-----|
| `POST` | `/notifications/device-token/` | Register or refresh FCM device token (login / token refresh) |
| `DELETE` | `/notifications/device-token/` | Deactivate FCM device token (logout) |
| `GET` | `/notifications/` | List notification inbox (paginated; unread + history) |
| `POST` | `/notifications/<id>/read/` | Mark one notification as read |
| `POST` | `/notifications/read-all/` | Mark all notifications as read |
| `GET` | `/auth/users/list/` | List users for **host (approver) dropdown** |
| `GET` | `/visitors/search/` | Search prior visitor by IC — **prefill** form + last-entry assets |
| `POST` | `/visitors/entries/checkin/` | Create manual walk-in or resubmit; **auto-reuse** missing photos from last visit |
| `POST` | `/visitors/entries/invite/` | Create Pre-Reg (Invite) entry (`visit_date` mandatory, assets optional) |
| `POST` | `/visitors/entries/<id>/complete-invite/` | Guard complete missing assets for invite (`visitor_photo` + `id_proof`) |
| `GET` | `/visitors/entries/` | List visitor entries (filters / pagination) |
| `GET` | `/visitors/entries/<ENTRY_UUID>/` | Entry detail (deep-link from FCM / inbox) |
| `GET` | `/visitors/entries/<ENTRY_UUID>/pass/` | Download visitor pass (PDF/image) |
| `GET` | `/visitors/entries/export/` | Export entries to Excel |
| `GET` | `/visitors/entries/export-pdf/` | Export entries to PDF (same filters) |
| `POST` | `/visitors/entries/<id>/approve/` | Host approve (= check-in; requires mandatory assets) |
| `POST` | `/visitors/entries/<id>/revert/` | Host revert for guard correction |
| `POST` | `/visitors/entries/<id>/cancel/` | Host reject / cancel visit |
| `POST` | `/visitors/entries/<id>/reschedule/` | Host reschedule visit date / times |
| `POST` | `/visitors/qr-scan/` | Scan QR on visit day (`scheduled` → `pending_approval` or `missing_document`) |
| `POST` | `/visitors/entries/<id>/checkout/` | Check out visitor (`multipart` + exit photo) |
| `POST` | `/visitors/ai/extract/` | Optional AI OCR — extract ID or vehicle number from image |

---

## Table of contents

0. [API index](#api-index)
1. [Statuses & rules](#1-statuses--rules)
2. [End-to-end flows](#2-end-to-end-flows)
3. [Notification types (FCM + inbox)](#3-notification-types-fcm--inbox)
4. [Device token APIs](#4-device-token-apis)
5. [Notification inbox APIs](#5-notification-inbox-apis)
6. [Host dropdown — list users](#6-host-dropdown--list-users)
7. [Visitor search (prefill)](#7-visitor-search-prefill)
8. [Manual entry / resubmit / asset auto-reuse](#8-manual-entry--resubmit--asset-auto-reuse)
9. [List / detail / pass](#9-list--detail--pass)
10. [Host actions](#10-host-actions-approve-revert-reject-reschedule)
11. [QR scan](#11-qr-scan)
12. [Checkout](#12-checkout)
13. [AI extract (optional)](#13-ai-extract-optional)
14. [Entry object (shared shape)](#14-entry-object-shared-shape)
15. [Auth / common exceptions](#15-auth--common-exceptions)

---

## 1. Statuses & rules

### Entry statuses

| Status | Meaning |
|--------|---------|
| `pending_approval` | Waiting for host. QR usable for display; scan → `awaiting_approval` |
| `reverted` | Host sent back for correction. Guard must **resubmit** |
| `scheduled` | Future visit day (after **reschedule** to another day). Not checked in |
| `checked_in` | Host approved (= checked in). Ready for exit |
| `checked_out` | Left site. QR expired |
| `cancelled` | Rejected / cancelled. QR expired |

**Reject = Cancel** → status `cancelled`, `qr_expired=true`.

### Manual walk-in rules

| Field | Who sets it |
|-------|-------------|
| `visit_date` | Server → **today** (location TZ) |
| `expected_arrival_time` | Server → **submit/create time** (do **not** send from app) |
| `expected_out_time` | Optional from guard/host |

### Who is notified (actor)

| Event | Recipient |
|-------|-----------|
| New `pending_approval` | **Host** |
| Approve / cancel / revert / reschedule | **`scanned_by`** if set (visit-day QR on a `scheduled` entry), else **`created_by`** (manual) |

If host == actor, host-action push is skipped (no self-notify).

---

## 2. End-to-end flows

### A) Manual walk-in (guard + host)

```text
1. App login → POST /notifications/device-token/  (host + guard)
2. Guard: GET /auth/users/list/?location_id=...  → host dropdown
3. Guard: optional GET /visitors/search/?ic_number=...  → prefill form + prior assets
4. Guard: POST /visitors/entries/checkin/  (multipart)
      → missing visitor_photo / id_proof / additional auto-filled from last visit when IC matches
      → status=pending_approval, visit_date=today, arrival=now, QR+pass
      → Notify HOST: visitor_pending_approval
5. Host: GET /notifications/  OR FCM tap → open entry
6. Host chooses:
   a) POST .../approve/     → checked_in
        → Notify GUARD: visitor_approved
   b) POST .../cancel/      → cancelled
        → Notify GUARD: visitor_cancelled
   c) POST .../revert/      → reverted
        → Notify GUARD: visitor_reverted
   d) POST .../reschedule/  → pending_approval (same day) OR scheduled (other day)
        → Notify GUARD: visitor_rescheduled
7. If reverted: Guard POST checkin again with entry_id → pending_approval
        → Notify HOST: visitor_pending_approval
8. When checked_in: Guard/host POST .../checkout/ + exit_photo → checked_out
```

### B) Reschedule → scheduled → visit-day QR

```text
1. Host reschedules an entry to a future visit_date
      → status=scheduled, QR refreshed
      → Notify actor: visitor_rescheduled
2. Visit day: Guard POST /visitors/qr-scan/ { qr_token }
      → status=pending_approval, scanned_by=guard
      → Notify HOST: visitor_pending_approval
3. Same host actions as flow A (actor = scanned_by)
```

### C) Mobile deep-link from FCM

```text
FCM data.type + data.entry_id
  → GET /visitors/entries/<entry_id>/
  → Show detail / host actions / resubmit screen by status
```

---

## 3. Notification types (FCM + inbox)

| `type` | When | To | Suggested screen |
|--------|------|----|------------------|
| `visitor_pending_approval` | Manual create, resubmit, or visit-day QR on `scheduled` → pending | Host | Entry detail / approve |
| `visitor_approved` | Host approve | Guard (`created_by` / `scanned_by`) | Entry detail (checked in) |
| `visitor_cancelled` | Host reject/cancel | Guard | Entry detail |
| `visitor_reverted` | Host revert | Guard | Resubmit / manual entry |
| `visitor_rescheduled` | Host reschedule | Guard | Entry detail / schedule |

### FCM / history `data` payload (all string values)

```json
{
  "type": "visitor_pending_approval",
  "entry_id": "a1b2c3d4-....",
  "visitor_name": "Ahmad Bin Ali",
  "ic_passport_number": "900101145678",
  "status": "pending_approval",
  "host_id": "h1h2h3h4-....",
  "location_id": "l1l2l3l4-....",
  "entry_source": "manual"
}
```

Extras (when present):

| Extra key | On type |
|-----------|---------|
| `revert_reason` | `visitor_reverted` |
| `visit_date` | `visitor_rescheduled` |
| `status` | always (may reflect new status) |

**Push channel:** FCM to active `android` / `ios` tokens only.  
**History:** always created. If no tokens → `delivery_status: "skipped"`, `error_message: "no_active_mobile_tokens"`.

---

## 4. Device token APIs

### 4.1 Register / refresh — `POST /notifications/device-token/`

Call on login and when FCM token refreshes.

```bash
curl -X POST "http://localhost:8000/notifications/device-token/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "token": "<FCM_DEVICE_TOKEN>",
    "device_type": "android",
    "device_id": "optional-stable-device-id",
    "app_version": "1.2.0"
  }'
```

`device_type`: `android` | `ios` | `web`  
Same `token` → upsert + reactivate. Same user + `device_id` → older tokens deactivated.

**Success `201` (created) or `200` (updated):**

```json
{
  "id": "2c309a03-2b91-4b79-a274-cd8916545f20",
  "token": "<FCM_DEVICE_TOKEN>",
  "device_type": "android",
  "device_id": "optional-stable-device-id",
  "app_version": "1.2.0",
  "is_active": true,
  "created_on": "2026-07-25T07:07:30.107341Z",
  "modified_on": "2026-07-25T07:07:30.107385Z"
}
```

**Exceptions:**

```json
HTTP 400  { "error": "token is required" }
HTTP 400  { "error": "device_type must be android, ios, or web" }
HTTP 401  { "detail": "Authentication credentials were not provided." }
```

---

### 4.2 Deactivate — `DELETE /notifications/device-token/`

Call on logout.

```bash
curl -X DELETE "http://localhost:8000/notifications/device-token/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"token": "<FCM_DEVICE_TOKEN>"}'
```

Or: `DELETE /notifications/device-token/?token=<FCM_DEVICE_TOKEN>`

**Success `200`:**

```json
{ "deactivated": true }
```

(If token unknown for user: `{ "deactivated": false }`)

**Exceptions:**

```json
HTTP 400  { "error": "token is required" }
HTTP 401  { "detail": "..." }
```

---

## 5. Notification inbox APIs

### 5.1 List — `GET /notifications/`

```bash
curl -X GET "http://localhost:8000/notifications/?limit=50&offset=0" \
  -H "Authorization: Bearer <TOKEN>"

# Unread only (page 2 example: skip first 50)
curl -X GET "http://localhost:8000/notifications/?unread=true&limit=50&offset=50" \
  -H "Authorization: Bearer <TOKEN>"
```

| Query | Default | Notes |
|-------|---------|--------|
| `limit` | `50` | Page size, max `200` |
| `offset` | `0` | Skip N rows (newest first) |
| `unread` | — | `true` / `1` / `yes` → unread only |

**Success `200`:**

```json
{
  "count": 120,
  "limit": 50,
  "offset": 0,
  "unread_count": 1,
  "results": [
    {
      "id": "n1n2n3n4-1111-2222-3333-444444444444",
      "type": "visitor_pending_approval",
      "title": "Visitor approval needed",
      "body": "Ahmad Bin Ali is waiting for your approval.",
      "data": {
        "type": "visitor_pending_approval",
        "entry_id": "a1b2c3d4-....",
        "visitor_name": "Ahmad Bin Ali",
        "ic_passport_number": "900101145678",
        "status": "pending_approval",
        "host_id": "h1h2h3h4-....",
        "location_id": "l1l2l3l4-....",
        "entry_source": "manual"
      },
      "related_entry": "a1b2c3d4-....",
      "channel": "push",
      "delivery_status": "sent",
      "sent_at": "2026-07-25T18:00:00+08:00",
      "read_at": null,
      "is_read": false,
      "created_on": "2026-07-25T18:00:00+08:00"
    }
  ]
}
```

`delivery_status`: `pending` | `sent` | `failed` | `skipped`

- `count` = total rows matching the filter (for pagination UI)
- Next page: `offset = offset + limit` while `offset + limit < count`
- Datetimes (`created_on`, `sent_at`, `read_at`) are stored in **UTC** and returned converted to the **user / related entry location timezone** (same pattern as visitor ETA/ETO and report APIs).

---

### 5.2 Mark one read — `POST /notifications/<id>/read/`

```bash
curl -X POST "http://localhost:8000/notifications/<NOTIFICATION_UUID>/read/" \
  -H "Authorization: Bearer <TOKEN>"
```

**Success `200`:** same notification object with `read_at` set, `is_read: true`.

**Exceptions:**

```json
HTTP 404  { "error": "Not found" }
```

---

### 5.3 Mark all read — `POST /notifications/read-all/`

```bash
curl -X POST "http://localhost:8000/notifications/read-all/" \
  -H "Authorization: Bearer <TOKEN>"
```

**Success `200`:**

```json
{ "marked_read": 3 }
```

---

## 6. Host dropdown — list users

### `GET /auth/users/list/`

Use this to populate the **Host (approver)** dropdown on manual entry. Filter by the same `location_id` as the visit.

| Query | Required | Notes |
|-------|----------|--------|
| `location_id` | recommended | Users at that site |
| `role` | no | Filter by role name (omit or `all` = any role) |
| `search` | no | Matches `name`, `email`, `employee_code` |

Non-superusers never see users with role `admin`.

```bash
# Hosts for a location (manual entry dropdown)
curl -X GET "http://localhost:8000/auth/users/list/?location_id=<LOCATION_UUID>" \
  -H "Authorization: Bearer <TOKEN>"
```

```bash
# Optional: narrow by role + search
curl -X GET "http://localhost:8000/auth/users/list/?location_id=<LOCATION_UUID>&role=admin&search=Ravi" \
  -H "Authorization: Bearer <TOKEN>"
```

**Success `200` (envelope):**

```json
{
  "status": "success",
  "message": "Users fetched",
  "data": [
    {
      "id": "h1h2h3h4-aaaa-bbbb-cccc-ddddeeeeffff",
      "email": "ravi@example.com",
      "role": "admin",
      "is_active": true,
      "name": "Ravi",
      "phone_no": "0123456789",
      "employee_code": "EMP001",
      "timezone": "Asia/Kuala_Lumpur",
      "location": {
        "id": "l1l2l3l4-aaaa-bbbb-cccc-ddddeeeeffff",
        "name": "HQ Site"
      },
      "created_on": "2026-01-01T00:00:00Z",
      "modified_on": "2026-07-01T00:00:00Z"
    }
  ]
}
```

Web/mobile: use each item’s `id` as `host_id` on check-in. Display label: `name` + optional `employee_code`.

**Exceptions:**

```json
HTTP 401  { "detail": "Authentication credentials were not provided." }
```

---

## 7. Visitor search (prefill)

### `GET /visitors/search/`

**Purpose:** Returning-visitor **prefill** for manual entry.

Call after the user enters / searches an IC. Response drives the form (name, phone, host, type, purpose, vehicle, remarks) and shows prior photos. Expected arrival / visit date are **not** returned — always capture fresh on submit.

| Query | Required | Notes |
|-------|----------|--------|
| `ic_number` | yes | Also accepts `ic_passport_number` |
| `location_id` | yes* | Required for superadmin; else from user location |

### Scenario — search found (prefill UI)

1. Guard opens search → enters IC  
2. `GET /visitors/search/?ic_number=...&location_id=...`  
3. `found: true` → fill form fields from `visitor` + `last_entry`  
4. Show preview images from `last_entry.assets` (`visitor_photo`, `id_proof`, `additional`)  
5. User may keep or replace photos, then submit check-in (see §8 — server auto-reuses missing files)

```bash
curl -X GET "http://localhost:8000/visitors/search/?ic_number=900101145678&location_id=<LOCATION_UUID>" \
  -H "Authorization: Bearer <TOKEN>"
```

**Success — found `200`:**

```json
{
  "found": true,
  "visitor": {
    "id": "v1v2v3v4-aaaa-bbbb-cccc-ddddeeeeffff",
    "visitor_name": "Ahmad Bin Ali",
    "ic_passport_number": "900101145678",
    "phone_number": "0123456789",
    "location_id": "l1l2l3l4-aaaa-bbbb-cccc-ddddeeeeffff",
    "location_name": "HQ Site"
  },
  "last_entry": {
    "id": "e1e2e3e4-aaaa-bbbb-cccc-ddddeeeeffff",
    "visitor_type": "guest",
    "purpose_of_visit": "Meeting",
    "vehicle_number": "TN58B8050",
    "remarks": "Just for testing",
    "host_id": "h1h2h3h4-aaaa-bbbb-cccc-ddddeeeeffff",
    "host_name": "Ravi",
    "host_employee_code": "EMP001",
    "assets": [
      {
        "id": "a1111111-aaaa-bbbb-cccc-ddddeeeeffff",
        "asset_type": "visitor_photo",
        "file_url": "http://localhost:8000/media/visitor/assets/photo.jpg",
        "created_on": "2026-07-20T10:00:00.000000Z"
      },
      {
        "id": "a2222222-aaaa-bbbb-cccc-ddddeeeeffff",
        "asset_type": "id_proof",
        "file_url": "http://localhost:8000/media/visitor/assets/id.jpg",
        "created_on": "2026-07-20T10:00:01.000000Z"
      },
      {
        "id": "a3333333-aaaa-bbbb-cccc-ddddeeeeffff",
        "asset_type": "additional",
        "file_url": "http://localhost:8000/media/visitor/assets/extra1.jpg",
        "created_on": "2026-07-20T10:00:02.000000Z"
      }
    ]
  }
}
```

**Prefill field map (client):**

| UI field | Source |
|----------|--------|
| IC / Passport | request IC / `visitor.ic_passport_number` |
| Visitor name | `visitor.visitor_name` |
| Phone | `visitor.phone_number` |
| Visitor type | `last_entry.visitor_type` |
| Purpose / vehicle / remarks | `last_entry.*` |
| Host dropdown | `last_entry.host_id` (+ name/code) |
| Photo previews | `last_entry.assets[]` by `asset_type` |

**Success — not found `200`:**

```json
{ "found": false, "visitor": null, "last_entry": null }
```

**Scenario — not found:** treat as first-time visitor → require new `visitor_photo` + `id_proof` on check-in.

**Exceptions:**

```json
HTTP 400  { "error": "ic_number is required" }
HTTP 400  { "error": "location_id is required for superadmin" }
```

---

## 8. Manual entry / resubmit / asset auto-reuse

### `POST /visitors/entries/checkin/`

`multipart/form-data`. Creates or resubmits → always `pending_approval` for walk-in.

| Field | Required | Notes |
|-------|----------|--------|
| `location_id` | yes* | Required for superadmin; else from user location |
| `ic_passport_number` | yes | |
| `visitor_name` | yes | |
| `host_id` | yes | From `/auth/users/list/` |
| `visitor_type` | yes | `guest` \| `contractor` \| `client` \| `delivery` \| `other` |
| `visitor_photo` | conditional | File — **required** first visit; optional if prior photo exists for this IC |
| `id_proof` | conditional | File — **required** first visit; optional if prior ID exists |
| `phone_number` | no | |
| `purpose_of_visit` | no | |
| `vehicle_number` | no | Text plate only (photos go in `additional_images`) |
| `remarks` | no | |
| `expected_out_time` | no | e.g. `2026-07-25T18:00` — must be after create time |
| `additional_images` | no | Repeat field. If sent → only those stored. If omitted → prior additional auto-copied (unless `clear_additional`) |
| `clear_additional` | no | `true` / `1` — do not auto-attach prior additional |
| `entry_id` | resubmit only | Must be status `reverted` |

### Asset auto-reuse (server-side — no client keys)

After saving any uploaded files, for the same visitor (IC + location):

1. Missing **`visitor_photo`** → clone latest prior `visitor_photo` (new `VisitorAsset` row, **same file path**)  
2. Missing **`id_proof`** → same for ID  
3. No **`additional_images`** uploaded and not `clear_additional` → clone prior visit’s `additional` rows  
4. Still missing photo or ID → **`400`**

Do **not** send `reuse_*_asset_id`, `vehicle_photo`, or `is_existed_visitor`.  
Response includes **`returning_visitor`: true|false**.

**Do not send `expected_arrival_time` or `visit_date`** — server sets them.

---

### Curl A — first-time visitor (all photos required)

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "phone_number=0123456789" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "purpose_of_visit=Meeting" \
  -F "vehicle_number=TN58B8050" \
  -F "remarks=Just for testing" \
  -F "expected_out_time=2026-07-25T18:00" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg" \
  -F "additional_images=@/path/to/extra1.jpg"
```

---

### Curl B — returning visitor, **prefilled assets** (no photo re-upload)

**Scenario:** Search found prior visit. Guard keeps old visitor photo + ID (+ optional additional). Uploads nothing for those slots (or only new text fields).

```bash
# After GET /visitors/search/ found prior assets — omit photo/id files
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "phone_number=0123456789" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "purpose_of_visit=Meeting" \
  -F "vehicle_number=TN58B8050"
```

Server attaches last `visitor_photo`, `id_proof`, and prior `additional` (if any). New entry `assets` point at the **same media paths**.

---

### Curl C — returning visitor, only new additional (keep photo + ID from last visit)

**Scenario:** Guard uploads a new vehicle/extra image only. Photo + ID omitted → auto-filled from history.

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "vehicle_number=TN58B8050" \
  -F "additional_images=@/path/to/new_car.jpg" \
  -F "additional_images=@/path/to/new_extra.jpg"
```

Result: new `additional` rows from upload; `visitor_photo` + `id_proof` cloned from last entry.

---

### Curl D — returning visitor, replace ID only

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "id_proof=@/path/to/new_id.jpg"
```

Result: new `id_proof` file; `visitor_photo` (and additional if not cleared) from last visit.

---

### Curl E — clear prior additional (do not auto-copy)

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "clear_additional=true"
```

Photo/ID still auto-filled if missing; **no** prior additional attached.

---

### Curl F — resubmit after revert

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -F "entry_id=<REVERTED_ENTRY_UUID>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "expected_out_time=2026-07-25T18:00" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg"
```

---

### Scenario summary

| Scenario | Client sends | Server does |
|----------|--------------|-------------|
| First visit | photo + id (+ optional additional) | Store uploads; `returning_visitor: false` |
| Prefill keep all | text only, no files | Clone photo + id + additional from last visit |
| Prefill + new additional only | `additional_images` only | Save new additional; clone photo + id |
| Prefill + replace one slot | that file only | Save upload; clone other missing types |
| Drop old additional | `clear_additional=true` | No additional clone |
| First visit missing photo/id | no file & no history | `400` |

**Success `201` (create and resubmit) example:**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending_approval",
  "entry_source": "manual",
  "visitor_type": "guest",
  "purpose_of_visit": "Meeting",
  "vehicle_number": "TN58B8050",
  "remarks": "Just for testing",
  "revert_reason": "",
  "location_id": "l1l2l3l4-aaaa-bbbb-cccc-ddddeeeeffff",
  "location_name": "HQ Site",
  "visitor_id": "v1v2v3v4-aaaa-bbbb-cccc-ddddeeeeffff",
  "visitor_name": "Ahmad Bin Ali",
  "ic_passport_number": "900101145678",
  "phone_number": "0123456789",
  "host_id": "h1h2h3h4-aaaa-bbbb-cccc-ddddeeeeffff",
  "host_name": "Ravi",
  "host_employee_code": "EMP001",
  "expected_arrival_time": "2026-07-25T14:05:00+08:00",
  "expected_out_time": "2026-07-25T18:00:00+08:00",
  "visit_date": "2026-07-25",
  "visit_date_time": "2026-07-25T00:00:00+08:00",
  "check_in_time": null,
  "check_out_time": null,
  "qr_token": "qr_abc123...",
  "qr_image_url": "http://localhost:8000/media/visitor/qr/....png",
  "pass_image_url": "http://localhost:8000/media/visitor/pass/....png",
  "qr_expired": false,
  "qr_usable": true,
  "approved_by_id": null,
  "approved_by_name": null,
  "approved_on": null,
  "created_by": "g1g2g3g4-aaaa-bbbb-cccc-ddddeeeeffff",
  "created_by_name": "Guard Name",
  "scanned_by": null,
  "scanned_by_name": null,
  "returning_visitor": true,
  "assets": [
    {
      "id": "....",
      "asset_type": "visitor_photo",
      "file_url": "http://localhost:8000/media/visitor/assets/photo.jpg",
      "created_on": "...."
    },
    {
      "id": "....",
      "asset_type": "id_proof",
      "file_url": "http://localhost:8000/media/visitor/assets/id.jpg",
      "created_on": "...."
    }
  ]
}
```

**Exceptions (assets):**

```json
HTTP 400  { "error": "visitor_photo is required (upload a file, or use a returning visitor who has a prior photo)" }
HTTP 400  { "error": "id_proof is required (upload IC/ID copy, or use a returning visitor who has a prior ID image)" }
```

**Success `201` (resubmit note):** same shape as create; `status` is again `"pending_approval"`, `revert_reason` cleared, QR/pass regenerated.

Side effect: host receives `visitor_pending_approval`.

**Other exceptions:**

```json
HTTP 400  { "error": "ic_passport_number is required" }
HTTP 400  { "error": "visitor_name is required" }
HTTP 400  { "error": "location_id is required" }
HTTP 400  { "error": "Invalid visitor_type. Choose from: client, contractor, delivery, guest, other" }
HTTP 400  { "error": "expected_out_time must be after expected_arrival_time" }
HTTP 400  { "error": "expected_out_time cannot be before visit_date (out=..., visit_date=...)" }
HTTP 400  { "error": "Only reverted entries can be resubmitted" }
HTTP 404  { "error": "Location not found" }
HTTP 400  { "error": "<host resolution message>" }
```

---

## 9. List / detail / pass

### 9.1 List — `GET /visitors/entries/`

`date_filter` matches **`visit_date`** (fallback: check-in, then created).

| Query | Values |
|-------|--------|
| `date_filter` | `today` (default) \| `upcoming` \| `this_week` \| `this_month` \| `custom` \| `all` |
| `start_date` / `end_date` | With `custom` — `YYYY-MM-DD` |
| `status` | One status or omit for all |
| `visitor_type` | `guest` \| `contractor` \| `client` \| `delivery` \| `other` (omit / `all` = any) |
| `location_id` | Filter org |
| `search` | Name / IC / phone |
| `mine` | `true` — entries where user is host or created_by |
| `has_vehicle` | `true` — non-empty vehicle number |

```bash
curl -X GET "http://localhost:8000/visitors/entries/?date_filter=today&status=pending_approval&mine=true" \
  -H "Authorization: Bearer <TOKEN>"

curl -X GET "http://localhost:8000/visitors/entries/?date_filter=upcoming&status=scheduled" \
  -H "Authorization: Bearer <TOKEN>"

curl -X GET "http://localhost:8000/visitors/entries/?date_filter=today&has_vehicle=true" \
  -H "Authorization: Bearer <TOKEN>"

curl -X GET "http://localhost:8000/visitors/entries/?date_filter=today&visitor_type=guest" \
  -H "Authorization: Bearer <TOKEN>"
```

**Success `200`:** JSON array of entry objects (same fields as check-in success).

```json
[
  {
    "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "status": "pending_approval",
    "visitor_name": "Ahmad Bin Ali",
    "ic_passport_number": "900101145678",
    "host_name": "Ravi",
    "visit_date": "2026-07-25",
    "qr_usable": true,
    "assets": []
  }
]
```

(Empty list when no matches: `[]`.)

---

### 9.2 Detail — `GET /visitors/entries/<ENTRY_UUID>/`

Use after FCM / notification tap.

```bash
curl -X GET "http://localhost:8000/visitors/entries/<ENTRY_UUID>/" \
  -H "Authorization: Bearer <TOKEN>"
```

**Success `200`:** single entry object (same shape as check-in success in §7).

**Exceptions:**

```json
HTTP 404  { "error": "Entry not found" }
```

---

### 9.3 Download pass — `GET /visitors/entries/<ENTRY_UUID>/pass/`

Returns PNG bytes (prefer over raw `/media/` URL for CORS).

```bash
curl -X GET "http://localhost:8000/visitors/entries/<ENTRY_UUID>/pass/" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor-pass.png
```

**Success `200`:** binary `image/png` body (saved to `visitor-pass.png`).

**Exceptions:**

```json
HTTP 400  { "error": "QR is expired" }
HTTP 403  { "error": "Forbidden" }
HTTP 404  { "error": "Visitor entry not found" }
HTTP 404  { "error": "Pass image not available" }
```

---

### 9.4 Export Excel — `GET /visitors/entries/export/`

Same filters as list. Response: `.xlsx` file.

```bash
curl -X GET "http://localhost:8000/visitors/entries/export/?date_filter=today" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor_entries.xlsx
```

### 9.5 Export PDF — `GET /visitors/entries/export-pdf/`

Same filters as list / Excel. Landscape PDF with company header, date range, status/type summary, and page footer (same style as roll call / dashboard PDF exports).

```bash
curl -X GET "http://localhost:8000/visitors/entries/export-pdf/?date_filter=today&visitor_type=guest" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor_entries.pdf
```

**Success `200`:** binary `application/pdf` body.

**Success `200`:** binary Excel file (`application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`).

---

## 10. Host actions (approve / revert / reject / reschedule)

All require host (or superadmin where allowed). Body JSON unless noted.

### 10.1 Approve (= check-in) — `POST /visitors/entries/<id>/approve/`

Only from `pending_approval`.

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/approve/" \
  -H "Authorization: Bearer <HOST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"expected_out_time":"2026-07-25T18:00"}'
```

`expected_out_time` optional.

**Success `200`:**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "checked_in",
  "check_in_time": "2026-07-25T14:20:00+08:00",
  "expected_out_time": "2026-07-25T18:00:00+08:00",
  "approved_by_id": "h1h2h3h4-aaaa-bbbb-cccc-ddddeeeeffff",
  "approved_by_name": "Ravi",
  "approved_on": "2026-07-25T14:20:00+08:00",
  "qr_expired": false,
  "qr_usable": true,
  "visitor_name": "Ahmad Bin Ali",
  "...": "(remaining entry fields same as §7)"
}
```

Notify actor: `visitor_approved`.

**Exceptions:**

```json
HTTP 403  { "error": "Only the host can approve this entry" }
HTTP 403  { "error": "Forbidden" }
HTTP 404  { "error": "Visitor entry not found" }
HTTP 400  { "error": "Cannot approve entry with status=checked_in" }
```

---

### 10.2 Revert — `POST /visitors/entries/<id>/revert/`

Only from `pending_approval`.

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/revert/" \
  -H "Authorization: Bearer <HOST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"revert_reason":"Wrong IC number"}'
```

**Success `200`:**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "reverted",
  "revert_reason": "Wrong IC number",
  "visitor_name": "Ahmad Bin Ali",
  "...": "(remaining entry fields)"
}
```

Notify actor: `visitor_reverted` (includes reason in body/data).

**Exceptions:**

```json
HTTP 403  { "error": "Only the host can revert this entry" }
HTTP 404  { "error": "Visitor entry not found" }
HTTP 400  { "error": "Cannot revert entry with status=scheduled" }
```

---

### 10.3 Reject / Cancel — `POST /visitors/entries/<id>/cancel/`

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/cancel/" \
  -H "Authorization: Bearer <HOST_TOKEN>"
```

**Success `200`:**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "cancelled",
  "qr_expired": true,
  "qr_usable": false,
  "visitor_name": "Ahmad Bin Ali",
  "...": "(remaining entry fields)"
}
```

Notify actor: `visitor_cancelled`.

**Exceptions:**

```json
HTTP 403  { "error": "Forbidden" }
HTTP 404  { "error": "Visitor entry not found" }
HTTP 400  { "error": "Cannot cancel entry with status=checked_out" }
```

---

### 10.4 Reschedule — `POST /visitors/entries/<id>/reschedule/`

Allowed from `pending_approval` or `scheduled`.  
Requires `expected_arrival_time` + `expected_out_time`. Optional `visit_date`.

| Result | Condition |
|--------|-----------|
| `pending_approval` | `visit_date` == today |
| `scheduled` | `visit_date` > today |

Never auto check-in.

```bash
# Same day
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/reschedule/" \
  -H "Authorization: Bearer <HOST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_arrival_time": "2026-07-25T15:00",
    "expected_out_time": "2026-07-25T19:00",
    "visit_date": "2026-07-25"
  }'

# Future day
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/reschedule/" \
  -H "Authorization: Bearer <HOST_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_arrival_time": "2026-07-28T10:00",
    "expected_out_time": "2026-07-28T17:00",
    "visit_date": "2026-07-28"
  }'
```

**Success `200` (same day):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending_approval",
  "visit_date": "2026-07-25",
  "expected_arrival_time": "2026-07-25T15:00:00+08:00",
  "expected_out_time": "2026-07-25T19:00:00+08:00",
  "qr_image_url": "http://localhost:8000/media/visitor/qr/....png",
  "pass_image_url": "http://localhost:8000/media/visitor/pass/....png",
  "...": "(remaining entry fields)"
}
```

**Success `200` (future day):**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "scheduled",
  "visit_date": "2026-07-28",
  "expected_arrival_time": "2026-07-28T10:00:00+08:00",
  "expected_out_time": "2026-07-28T17:00:00+08:00",
  "...": "(remaining entry fields)"
}
```

Notify actor: `visitor_rescheduled`.

**Exceptions:**

```json
HTTP 403  { "error": "Only the host can reschedule this entry" }
HTTP 404  { "error": "Visitor entry not found" }
HTTP 400  { "error": "Cannot reschedule entry with status=checked_in" }
HTTP 400  { "error": "expected_arrival_time is required" }
HTTP 400  { "error": "expected_out_time is required" }
HTTP 400  { "error": "expected_out_time must be after expected_arrival_time" }
HTTP 400  { "error": "visit_date cannot be in the past" }
HTTP 400  { "error": "expected_arrival_time cannot be before visit_date (...)" }
HTTP 400  { "error": "expected_out_time cannot be before visit_date (...)" }
```

---

## 11. QR scan

### `POST /visitors/qr-scan/`

```bash
curl -X POST "http://localhost:8000/visitors/qr-scan/" \
  -H "Authorization: Bearer <GUARD_TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"qr_token":"<QR_TOKEN>"}'
```

**Success shapes (`action` drives UI):**

| Entry state | `action` | HTTP | Notes |
|-------------|----------|------|-------|
| `pending_approval` | `awaiting_approval` | 200 | Wait for host |
| `reverted` | `reverted` | 200 | Guard must resubmit |
| `scheduled` + visit day | `awaiting_approval` | 200 | Moves to pending; sets `scanned_by`; notifies host |
| `scheduled` + future | `too_early` | 400 | Not visit day yet |
| `checked_in` | `checkout` | 200 | Prompt exit flow |
| expired / cancelled / out | `expired` | 400 | |

**Example — awaiting approval:**

```json
{
  "action": "awaiting_approval",
  "message": "Not approved yet. Waiting for host approval.",
  "entry": { "...": "full entry object" }
}
```

**Example — scheduled → verify_entry on visit day:**

```json
{
  "action": "verify_entry",
  "type": "verify_entry",
  "message": "Invited visitor arrived. Redirect guard to verification form.",
  "entry": {
    "status": "scheduled",
    "...": "..."
  }
}
```

**Example — checkout prompt:**

```json
{
  "action": "checkout",
  "message": "Visitor is checked in. Proceed to checkout.",
  "entry": { "status": "checked_in", "...": "..." }
}
```

**Example — too early (scheduled, future visit day) `400`:**

```json
{
  "action": "too_early",
  "message": "Visit is scheduled for 2026-07-28. Too early to check in.",
  "entry": {
    "status": "scheduled",
    "visit_date": "2026-07-28",
    "...": "(full entry object)"
  }
}
```

**Example — reverted:**

```json
{
  "action": "reverted",
  "message": "Entry was reverted. Guard must correct and resubmit.",
  "entry": {
    "status": "reverted",
    "revert_reason": "Wrong IC number",
    "...": "(full entry object)"
  }
}
```

**Exceptions:**

```json
HTTP 400  { "error": "qr_token is required" }
HTTP 403  { "error": "Forbidden" }
HTTP 404  { "error": "Invalid QR" }
HTTP 400  {
  "action": "expired",
  "message": "QR has expired",
  "entry": { "...": "..." }
}
```

---

## 12. Checkout

### `POST /visitors/entries/<id>/checkout/`

Only `checked_in`. **`exit_photo` required.**

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/checkout/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "exit_photo=@/path/to/exit.jpg"
```

**Success `200`:**

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "checked_out",
  "check_out_time": "2026-07-25T17:45:00+08:00",
  "qr_expired": true,
  "qr_usable": false,
  "visitor_name": "Ahmad Bin Ali",
  "assets": [
    {
      "id": "....",
      "asset_type": "exit_photo",
      "file_url": "http://localhost:8000/media/visitor/assets/exit.jpg",
      "created_on": "2026-07-25T17:45:01.000000Z"
    }
  ],
  "...": "(remaining entry fields)"
}
```

**Exceptions:**

```json
HTTP 403  { "error": "Forbidden" }
HTTP 404  { "error": "Visitor entry not found" }
HTTP 400  { "error": "Entry is not checked in (status=pending_approval)" }
HTTP 400  { "error": "Checkout image (exit_photo) is required" }
```

---

## 13. AI extract (optional)

### `POST /visitors/ai/extract/`

Soft-assist OCR. See [`VISITOR_AI_OCR.md`](../visitor/docs/VISITOR_AI_OCR.md).

**Production tip:** call this on the **Daphne** host (e.g. `:7999`), not multi-worker Gunicorn — each worker would load torch/Paddle into RAM.

```bash
curl -X POST "http://localhost:8000/visitors/ai/extract/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "type=id" \
  -F "image=@/path/to/id.jpg"
```

**Success:**

```json
{ "type": "id", "found": true, "number": "900101145678", "confidence": 0.99 }
```

```bash
curl -X POST "http://localhost:8000/visitors/ai/extract/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "type=vehicle" \
  -F "image=@/path/to/car.jpg"
```

```json
{ "type": "vehicle", "found": true, "number": "TN58B8050", "confidence": 0.89 }
```

When not found: `"found": false`, `"number": null`.

---

## 14. Entry object (shared shape)

Typical success body for create / approve / list item / detail:

```json
{
  "id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "status": "pending_approval",
  "entry_source": "manual",
  "visitor_type": "guest",
  "purpose_of_visit": "Meeting",
  "vehicle_number": "TN58B8050",
  "remarks": "Just for testing",
  "revert_reason": "",
  "location_id": "l1l2l3l4-....",
  "location_name": "HQ Site",
  "visitor_id": "v1v2v3v4-....",
  "visitor_name": "Ahmad Bin Ali",
  "ic_passport_number": "900101145678",
  "phone_number": "0123456789",
  "host_id": "h1h2h3h4-....",
  "host_name": "Ravi",
  "host_employee_code": "EMP001",
  "expected_arrival_time": "25/07/2026 14:05",
  "expected_out_time": "25/07/2026 18:00",
  "visit_date": "2026-07-25",
  "visit_date_time": "25/07/2026 00:00",
  "check_in_time": null,
  "check_out_time": null,
  "qr_token": "qr_....",
  "qr_image_url": "http://localhost:8000/media/visitor/qr/....png",
  "pass_image_url": "http://localhost:8000/media/visitor/pass/....png",
  "qr_expired": false,
  "qr_usable": true,
  "approved_by_id": null,
  "approved_by_name": null,
  "approved_on": null,
  "created_by": "g1g2g3g4-....",
  "created_by_name": "Guard Name",
  "scanned_by": null,
  "scanned_by_name": null,
  "created_on": "25/07/2026 14:05",
  "assets": [
    {
      "id": "....",
      "asset_type": "visitor_photo",
      "file_url": "http://localhost:8000/media/....jpg",
      "created_on": "...."
    },
    {
      "id": "....",
      "asset_type": "id_proof",
      "file_url": "http://localhost:8000/media/....jpg",
      "created_on": "...."
    }
  ]
}
```

`asset_type`: `visitor_photo` | `id_proof` | `additional` | `exit_photo`

Check-in responses also include **`returning_visitor`**: `true` if this visitor already had a prior entry at the location.

Datetime display fields are location-TZ formatted strings from the API (not always raw ISO).

---

## 15. Auth / common exceptions

```json
HTTP 401
{ "detail": "Authentication credentials were not provided." }

HTTP 401
{ "detail": "Given token not valid for any token type", "code": "token_not_valid" }
```

Use a fresh access token; register FCM after login; deactivate on logout.

---

## Quick smoke checklist (mobile)

1. Guard + host: login → `POST /notifications/device-token/` (`android`/`ios`).
2. Guard: `GET /auth/users/list/?location_id=...` → pick `host_id`.
3. Guard (returning): `GET /visitors/search/?ic_number=...` → prefill form + asset previews.
4. Guard: `POST /visitors/entries/checkin/` (with or without photo files) → `pending_approval` (+ `returning_visitor` when applicable).
5. Host: FCM / `GET /notifications/` → `visitor_pending_approval`.
6. Host: `POST .../approve/` → entry `checked_in`.
7. Guard: FCM / inbox → `visitor_approved`.
8. Guard: `POST .../checkout/` + `exit_photo` → `checked_out`.
9. Logout: `DELETE /notifications/device-token/`.
