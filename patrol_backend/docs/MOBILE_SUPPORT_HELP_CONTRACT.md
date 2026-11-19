# Mobile Help & Support — Contract (Phase 5)

**Audience:** SMART PATROL mobile app team (Flutter; code lives outside this repo)  
**Status:** Backend + API contract ready; wire screens against `/support/`  
**Related:** [`support_ticketing_api.md`](./support_ticketing_api.md) · [`support/MOBILE_API_CURL.md`](../support/MOBILE_API_CURL.md)

This is **app helpdesk** (GPS mismatch, check-in failures, login/app issues).  
It is **not** Incident Management (site/security events). Keep Incident Report flows unchanged.

---

## Goal (mobile UX)

| Screen | Purpose |
|--------|---------|
| **Help & Support** (hub) | Entry from profile / settings / menu |
| **Raise Ticket** | Create ticket (+ optional screenshot) |
| **My Tickets** | List own tickets (paginated) |
| **Ticket Detail** | Status, public timeline, comments, attachments, reopen if `RESOLVED` |

SUPPORT queue / dashboard remain **web console** only in v1.

---

## Auth & site prefill

| | |
|---|---|
| **REST** | `Authorization: Bearer <JWT access>` |
| **Base path** | `/support/` |
| **Envelope** | `{ "status": "success\|error", "message": "...", "data": ... }` |

**Site prefill (Raise Ticket):**

1. After login, call existing `GET /auth/v5/my-sites/` (unchanged).
2. Prefer `current_site.id` as `site_id` when raising a ticket.
3. If no current site, allow omit `site_id` (org still derived from user `location`) or let the user pick from `assigned_sites`.

Do **not** invent a new sites API for support.

---

## Screen flows

### 1. Help & Support hub

Show two primary actions:

- **Raise Ticket** → form
- **My Tickets** → list

Optional: unread badge from notification inbox when `type` starts with `support_ticket_`.

### 2. Raise Ticket

**Load categories first:**

```http
GET /support/categories/
```

**Form fields**

| UI field | API field | Required |
|---------|-----------|----------|
| Subject | `subject` | yes |
| Description | `description` | yes |
| Category | `category_id` | yes (from categories list) |
| Priority | `priority` | no (`LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL`; default `MEDIUM`) |
| Site | `site_id` | no (prefill from `current_site`) |
| Photos / videos | `assets` / `photo` / `video` (multipart, multi) | no |
| Audio | `audio` / `description_audio` (multipart, multi) | no |
| Legacy screenshot | `file` / `attachment` (multipart) | no |

**Submit (preferred for media):** `multipart/form-data`

```http
POST /support/tickets/
```

Fields: `subject`, `description`, `category_id`, optional `priority`, `site_id`, plus optional multi-file media:
- `assets` (repeatable) / `photo` / `video` — image/video (Incident parity)
- `audio` / `description_audio` — audio (Incident parity)
- legacy `file` / `attachment` still accepted

**Submit (text only):** JSON body to the same path is still supported.

**Success:** `201` → navigate to Ticket Detail using `data.id`. Attachments include `kind` (`ASSET` \| `AUDIO` \| `FILE`) and `purpose` (`CREATE`).

**Limits (attachments):** max **10 MB** each, max **10** files per request; images/video (`.jpg` `.jpeg` `.png` `.gif` `.webp` `.mp4` `.mov` `.webm`), audio (`.mp3` `.wav` `.m4a` `.aac` `.ogg` `.amr` `.3gp`), docs (`.pdf` `.txt` `.csv` `.doc` `.docx` `.xls` `.xlsx`).

### 3. My Tickets

```http
GET /support/tickets/my/?page=1&page_size=25
```

Optional filter: `status=OPEN` (etc.).

Response: `data` = ticket array; `pagination` = `{ page, page_size, total, total_pages }`.

Row tap → Ticket Detail.

### 4. Ticket Detail

```http
GET /support/tickets/<id>/
```

Creator sees **PUBLIC** comments + public activity only (both embedded on detail). `invalidation_reason` / `resolution_summary` are visible when set. Refresh by re-fetching detail — no separate activity or comment-list GET.

**Actions for creator**

| Action | When | API |
|--------|------|-----|
| Add public comment | status not `CLOSED` / `INVALID` | `POST .../actions/` `{ "action": "comment", "body": "..." }` |
| Upload attachment | same | `POST .../attachments/` multipart `file` |
| Download attachment | always if listed | `GET .../attachments/<aid>/download/` (Bearer) |
| Reopen | status `RESOLVED` only | `POST .../actions/` `{ "action": "reopen", "comment": "..." }` |

Do **not** show SUPPORT-only actions (pickup / assign / transfer / invalidate / resolve / close / internal notes).

---

## Push / inbox deep links

Support events already write `notifications.NotificationLog` and attempt FCM (android/ios tokens).

Register device as today:

```http
POST /notifications/device-token/
{ "token": "<FCM>", "device_type": "android" }
```

### Data payload (every support push)

| Key | Example | Use |
|-----|---------|-----|
| `type` | `support_ticket_resolved` | Branch copy / analytics |
| `ticket_id` | UUID | Open detail |
| `ticket_number` | `SP-2026-00001` | Subtitle |
| `subject` | string | Preview |
| `status` | `RESOLVED` | Badge |
| `priority` | `HIGH` | Badge |
| `click_action` | `FLUTTER_NOTIFICATION_CLICK` | Flutter background click |
| `screen` | `support_ticket_detail` | Router hint |
| `route` | `/help-support/tickets/<uuid>` | Deep link path |

**Suggested routes**

| `screen` | Open |
|----------|------|
| `support_ticket_detail` | Ticket Detail for `ticket_id` |
| `support_my_tickets` | My Tickets list |
| `help_support` | Help & Support hub |

Inbox: `GET /notifications/` — filter client-side where `type` starts with `support_ticket_`.

| Event | `type` | Typical recipient |
|-------|--------|-------------------|
| Created | `support_ticket_created` | SUPPORT agents |
| Assigned / pickup | `support_ticket_assigned` | Creator **and** SUPPORT agents |
| Resolved | `support_ticket_resolved` | Creator |
| Closed | `support_ticket_closed` | Creator **and** SUPPORT agents |
| Invalidated | `support_ticket_invalidated` | Creator |
| Public comment | `support_ticket_comment` | Other party |
| Attachment | `support_ticket_attachment` | Other party |
| Reopened | `support_ticket_reopened` | Assignee / SUPPORT |

---

## Status labels (creator-facing)

```text
OPEN → IN_PROGRESS → RESOLVED → CLOSED
OPEN | IN_PROGRESS → INVALID
RESOLVED → IN_PROGRESS | OPEN   (creator reopen only)
```

- `CLOSED` / `INVALID`: no reopen in v1  
- SLA / due dates: not in v1  

---

## Error handling

| HTTP | Meaning |
|------|---------|
| 400 | Validation (`data` may hold field errors or `{ "code": "..." }`) |
| 401 | Missing / expired JWT |
| 403 | Not creator (detail / comment / attachment) |
| 404 | Unknown ticket / attachment |
| 429 | Rate limited (create / comment / attachment / reopen) |

Show `message` from the envelope; do not expose stack traces.

---

## Out of scope for mobile v1

- SUPPORT global queue / assign / transfer / dashboard  
- Internal comments  
- Email channel  
- SLA timers  
- Merging with Incident tickets  

---

## Implementation checklist (mobile)

- [ ] Hub: Help & Support → Raise Ticket / My Tickets  
- [ ] Categories + Raise Ticket (JSON and multipart with multi-file assets/audio)  
- [ ] Prefill `site_id` from `GET /auth/v5/my-sites/` `current_site`  
- [ ] My Tickets list + pagination + status filter  
- [ ] Detail: public comments + activity from detail GET, attachments, reopen via `/actions/` when `RESOLVED`  
- [ ] FCM / inbox: handle `click_action` + `ticket_id` → detail  
- [ ] Keep Incident Report menu/entry separate  

Backend already provides the APIs above; no extra support endpoints are required for this phase unless mobile discovers a gap.
