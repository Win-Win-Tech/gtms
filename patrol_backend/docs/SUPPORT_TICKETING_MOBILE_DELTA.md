# Support Ticketing — Mobile delta

What changed for SMART PATROL Help & Support after the refer-back / assignee-lock release.
Full requester reference remains in [`support/MOBILE_API_CURL.md`](../support/MOBILE_API_CURL.md).

| | |
|---|---|
| **Auth** | `Authorization: Bearer <JWT>` |
| **Base** | `http://127.0.0.1:8000/support/` (replace host) |
| **Envelope** | `{ "status": "success\|error", "message": "...", "data": ... }` |

Scan / unscheduled patrol APIs are unchanged.

---

## What mobile must change

1. **Status `INVALID` is gone.** The replacement is `REFERRED_BACK`. Existing invalid tickets are migrated to `REFERRED_BACK` and the old reason is copied onto `referred_back_reason`. `invalidation_reason` is still returned and mirrors the refer-back reason for one release.
2. **Requester close and reopen** apply when status is `RESOLVED` **or** `REFERRED_BACK`. Comment and media on close are optional. `CLOSED` still cannot be reopened by the requester.
3. **Refer back clears the assignee.** `current_assignee` becomes `null`. Assignment history is not deleted (it stays on the ticket for web reports). The requester is notified (`support_ticket_referred_back`).
4. **Auto-close.** After 24 hours in `RESOLVED` or `REFERRED_BACK`, the server sets `status=CLOSED` and `auto_closed=true`. The 24h clock is `status_entered_at` and restarts if the ticket re-enters those statuses. No push/email is sent for auto-close.
5. **Lists.**
   - `GET /support/tickets/my/` for a normal user is still tickets they created.
   - Org admin (`role=admin`) on that endpoint sees their organisation only.
   - Support agents do not raise tickets (`POST` returns 403). For a support user, `GET /support/tickets/my/` is **assigned to me only**. The full queue stays `GET /support/support/tickets/`.
6. **Assignee lock (support app only).** Once `current_assignee` is set, only that user or a Super Admin can update the ticket. Other support users can still open the detail (view). Unassigned `OPEN` tickets can still be picked up or first-assigned by any support user.

Support agents reassign freely among support users (no L1/L2/L3 rule). Reassignment still requires a comment when the ticket already has an assignee.

---

## Status values

`OPEN` · `IN_PROGRESS` · `RESOLVED` · `CLOSED` · `REFERRED_BACK`

Do not send or filter `INVALID`.

---

## New / changed fields (detail)

| Field | Meaning |
|---|---|
| `referred_back_reason` | Why the ticket was sent back. Empty if it never was. |
| `referred_back_at` | When it was last referred back (viewer timezone, same as other datetimes). |
| `invalidation_reason` | Legacy mirror of `referred_back_reason`. |
| `status_entered_at` | When the current status started. Auto-close uses this. |
| `auto_closed` | `true` when the 24h job closed it. Manual close is `false`. |
| `can_act` | `true` when this caller may comment / run workflow actions they are allowed to use. Closed tickets still reject new comments even if `can_act` is true for the assignee (they may reopen). |
| `current_assignee` | `null` after refer-back. |

---

## API index (delta)

| Method | Endpoint | Who | Change |
|---|---|---|---|
| `POST` | `/support/tickets/` | Requester | Unchanged. Support role receives **403**. |
| `GET` | `/support/tickets/my/` | Requester | Created by me. Org admin: their org. Support: **assigned to me**. |
| `POST` | `/support/tickets/<id>/actions/` | Both | New action `refer_back`. `invalidate` is a deprecated alias and now returns `REFERRED_BACK`. |
| `POST` | `/support/tickets/<id>/refer-back/` | Support assignee or Super Admin | New. Same body as the action. |
| `POST` | `/support/tickets/<id>/invalidate/` | Support | Deprecated alias of refer-back. |
| `POST` | `/support/tickets/<id>/close/` | Creator, or assignee / Super Admin | Creator may close `RESOLVED` or `REFERRED_BACK` (comment and media optional). |
| `POST` | `/support/tickets/<id>/reopen/` | Creator or support | Creator: `RESOLVED` or `REFERRED_BACK` only. Support: also `CLOSED` (comment required). |
| `POST` | `/support/tickets/<id>/comments/` | Viewer who `can_act` | Blocked on `CLOSED`, and blocked for a support user who is not the assignee. |

---

## 1. Refer back (support)

Reason is required. Allowed from `OPEN` or `IN_PROGRESS`. Assignee is cleared. Requester gets a notification.

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_ID/actions/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "refer_back",
    "reason": "Need a clearer photo of the error"
  }'
```

Equivalent: `POST /support/tickets/TICKET_ID/refer-back/` with the same JSON.

```json
{
  "status": "success",
  "message": "Ticket referred back",
  "data": {
    "id": "TICKET_ID",
    "ticket_number": "SP-2026-00042",
    "subject": "GPS not updating",
    "status": "REFERRED_BACK",
    "current_assignee": null,
    "assignee_name": null,
    "referred_back_reason": "Need a clearer photo of the error",
    "invalidation_reason": "Need a clearer photo of the error",
    "referred_back_at": "2026-11-25T22:40:00+05:30",
    "status_entered_at": "2026-11-25T22:40:00+05:30",
    "auto_closed": false,
    "can_act": true,
    "resolved_at": null,
    "closed_at": null
  }
}
```

Missing reason:

```json
{
  "status": "error",
  "message": "A reason is required to refer a ticket back.",
  "data": { "code": "reason_required" }
}
```

Another support user, when someone is already assigned:

```json
{
  "status": "error",
  "message": "Only the current assignee or Super Admin can update this ticket.",
  "data": { "code": "assignee_locked" }
}
```

Notification type for the requester: `support_ticket_referred_back`  
(`support_ticket_invalidated` is no longer sent). Title: `Ticket SP-2026-00042 referred back`. Body is the reason. `data.status` is `REFERRED_BACK`. `data.screen` stays `support_ticket_detail`.

---

## 2. Creator closes a resolved or referred-back ticket

Comment and files are optional. Same multipart fields as before (`assets`, `audio`) when sending evidence.

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_ID/close/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "comment": "Sorted on my side" }'
```

```json
{
  "status": "success",
  "message": "Ticket closed",
  "data": {
    "id": "TICKET_ID",
    "status": "CLOSED",
    "auto_closed": false,
    "closed_at": "2026-11-25T22:55:00+05:30",
    "status_entered_at": "2026-11-25T22:55:00+05:30",
    "referred_back_reason": "Need a clearer photo of the error"
  }
}
```

`auto_closed` is `false` for this call. Show an “auto closed” label only when `status` is `CLOSED` and `auto_closed` is `true`.

---

## 3. Creator reopens

Allowed for `RESOLVED` and `REFERRED_BACK`. Not for `CLOSED`.

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_ID/actions/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "reopen",
    "comment": "Still broken after the photo"
  }'
```

After refer-back the assignee is empty, so the ticket returns to `OPEN` (not `IN_PROGRESS`).

```json
{
  "status": "success",
  "message": "Ticket reopened",
  "data": {
    "id": "TICKET_ID",
    "status": "OPEN",
    "current_assignee": null,
    "auto_closed": false,
    "resolved_at": null,
    "status_entered_at": "2026-11-25T23:05:00+05:30"
  }
}
```

If it was `RESOLVED` and still assigned, status comes back as `IN_PROGRESS` and the assignee stays.

Creator on a closed ticket:

```json
{
  "status": "error",
  "message": "Closed tickets can only be reopened by SUPPORT or Super Admin.",
  "data": { "code": "cannot_reopen_closed" }
}
```

Support reopen of `CLOSED` still requires `comment` (`code`: `comment_required`). Optional `target_status`: `OPEN` or `IN_PROGRESS`.

---

## 4. My tickets

```bash
curl -sS "http://127.0.0.1:8000/support/tickets/my/?page=1&page_size=25" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

```json
{
  "status": "success",
  "message": "My tickets fetched",
  "data": [
    {
      "id": "TICKET_ID",
      "ticket_number": "SP-2026-00042",
      "subject": "GPS not updating",
      "status": "IN_PROGRESS",
      "priority": "HIGH",
      "current_assignee": "USER_ID",
      "assignee_name": "Agent Alpha",
      "created_on": "2026-11-25T10:00:00+05:30"
    }
  ],
  "pagination": { "page": 1, "page_size": 25, "total": 1, "total_pages": 1 }
}
```

| Caller | Rows returned |
|---|---|
| Guard / normal user | Tickets they created |
| Org admin | Tickets in their organisation |
| Support / Super Admin | Tickets currently assigned to them |

Support users calling `POST /support/tickets/` or `POST /support/tickets/my/`:

```json
{
  "status": "error",
  "message": "Support users cannot raise tickets.",
  "data": null
}
```

HTTP 403.

Unassigned `OPEN` pickup is unchanged:

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_ID/actions/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{ "action": "pickup" }'
```

---

## 5. Auto-close (no client call)

A Celery job (`support.tasks.auto_close_resolved_and_referred_tickets`, every 15 minutes) closes tickets whose `status` is `RESOLVED` or `REFERRED_BACK` and whose `status_entered_at` is at least 24 hours ago.

After it runs, detail looks like:

```json
{
  "status": "CLOSED",
  "auto_closed": true,
  "closed_at": "2026-11-26T22:40:00+05:30",
  "status_entered_at": "2026-11-26T22:40:00+05:30"
}
```

Activity action: `AUTO_CLOSED`. There is no notification and no email for this event. Reopening and later resolving or referring back starts a new 24 hour window.

---

## UI hints

- Replace any Invalid chip, filter, or button with **Referred back**.
- On `REFERRED_BACK` or `CLOSED`, show `referred_back_reason` (fall back to `invalidation_reason`).
- Requester actions on `RESOLVED` and `REFERRED_BACK`: **Close** (optional comment/media) and **Reopen**.
- If `can_act` is false, render the ticket read-only (support user looking at someone else’s assignment).
- If `auto_closed` is true, label the closed ticket as auto-closed.
