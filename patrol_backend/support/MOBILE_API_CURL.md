# Support Ticketing — Mobile API (curl)

Canonical **requester** flows for SMART PATROL Help & Support.  
Full reference (including SUPPORT console): [`docs/support_ticketing_api.md`](../docs/support_ticketing_api.md)  
Screen / FCM contract: [`docs/MOBILE_SUPPORT_HELP_CONTRACT.md`](../docs/MOBILE_SUPPORT_HELP_CONTRACT.md)

| | |
|---|---|
| **Auth** | `Authorization: Bearer <JWT>` |
| **Base** | `http://127.0.0.1:8000/support/` (replace host) |
| **Envelope** | `{ "status": "success\|error", "message": "...", "data": ... }` |

---

## API index (mobile)

| Method | Endpoint | Use |
|--------|----------|-----|
| `GET` | `/support/categories/` | Raise Ticket category picker |
| `POST` | `/support/tickets/` | Raise Ticket (JSON or multipart + assets/audio) |
| `GET` | `/support/tickets/my/` | My Tickets list |
| `GET` | `/support/tickets/<id>/` | Ticket detail |
| `GET` | `/support/tickets/<id>/activity/` | Public activity timeline |
| `POST` | `/support/tickets/<id>/comments/` | Public comment |
| `POST` | `/support/tickets/<id>/attachments/` | Upload file |
| `GET` | `/support/tickets/<id>/attachments/<aid>/download/` | Download file |
| `POST` | `/support/tickets/<id>/reopen/` | Creator: `RESOLVED`; SUPPORT: also `CLOSED`/`INVALID` (+ comment) |

Site context (existing): `GET /auth/v5/my-sites/`

---

## 1. Categories

```bash
curl -sS "http://127.0.0.1:8000/support/categories/" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

---

## 2. Raise Ticket (JSON)

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "subject": "GPS not updating",
    "description": "Check-in works but live map is stale.",
    "category_id": "CATEGORY_UUID",
    "priority": "HIGH",
    "site_id": "SITE_UUID"
  }'
```

---

## 3. Raise Ticket with media (multipart — Incident parity)

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -F "subject=GPS not updating" \
  -F "description=Photos and voice note from the live map screen." \
  -F "category_id=CATEGORY_UUID" \
  -F "priority=HIGH" \
  -F "site_id=SITE_UUID" \
  -F "assets=@/path/to/screenshot.png" \
  -F "assets=@/path/to/second.png" \
  -F "audio=@/path/to/note.mp3"
```

Aliases: `photo` / `video` / `images` for assets; `description_audio` for audio; legacy `file` / `attachment` still accepted.

---

## 4. My Tickets

```bash
curl -sS "http://127.0.0.1:8000/support/tickets/my/?page=1&page_size=25" \
  -H "Authorization: Bearer ACCESS_TOKEN"

# Optional status filter
curl -sS "http://127.0.0.1:8000/support/tickets/my/?status=OPEN" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

---

## 5. Detail / activity

```bash
curl -sS "http://127.0.0.1:8000/support/tickets/TICKET_UUID/" \
  -H "Authorization: Bearer ACCESS_TOKEN"

curl -sS "http://127.0.0.1:8000/support/tickets/TICKET_UUID/activity/" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

---

## 6. Comment

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_UUID/comments/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"body": "Still happening after relaunch"}'
```

Creators must not send `visibility=INTERNAL` (403).

---

## 7. Attachment upload / download

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_UUID/attachments/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -F "file=@/path/to/log.txt"

curl -sS "http://127.0.0.1:8000/support/tickets/TICKET_UUID/attachments/ATTACHMENT_UUID/download/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -o downloaded.bin
```

---

## 8. Reopen (RESOLVED only)

```bash
curl -sS -X POST "http://127.0.0.1:8000/support/tickets/TICKET_UUID/reopen/" \
  -H "Authorization: Bearer ACCESS_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"comment": "Issue came back after the update"}'
```

---

## 9. Notifications inbox (deep link)

```bash
curl -sS "http://127.0.0.1:8000/notifications/" \
  -H "Authorization: Bearer ACCESS_TOKEN"
```

Support rows use `type` values such as `support_ticket_resolved`.  
FCM `data` includes `click_action=FLUTTER_NOTIFICATION_CLICK`, `screen=support_ticket_detail`, `ticket_id`, `route`.
