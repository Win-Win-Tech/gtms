# Roll Call API (mobile) — cURL examples

Base path: `/rollcall/`

All endpoints require:
```
Authorization: Bearer <ACCESS_TOKEN>
```

Sessions are scoped by **location + shift**. Multiple open sessions are allowed for the same location+shift. Any authenticated user at that location can upload the **end** photo for an open session.

Overnight shifts: `shift_date` is the logical shift start day (same rule as attendance).

---

## 1) List shifts for my location (existing scheduler API)

Use the existing endpoint — do **not** use a rollcall-specific shifts API:

```bash
curl -s "http://localhost:8000/scheduler/shifts/by-location/<LOCATION_UUID>/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

`LOCATION_UUID` comes from the logged-in user’s profile / `location_id`.

---

## 2) List sessions (open, closed, or all)

Single list endpoint for mobile and admin:

```bash
curl -s "http://localhost:8000/rollcall/sessions/?shift_id=<SHIFT_UUID>&status=open" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

### Query params

| Param | Values | Notes |
|-------|--------|-------|
| `status` | `open` \| `closed` \| `all` | Default `all` |
| `shift_id` | UUID | Required on mobile shift screen |
| `location_id` | UUID | Auto from JWT for guards; superadmin can pass |
| `date_filter` | `today` \| `week` \| `month` \| `custom` | Filters on `shift_date` |
| `start_date` / `end_date` | YYYY-MM-DD | With `date_filter=custom` |
| `user_id` | UUID | Sessions where user **started** OR **ended** |
| `filter_open_by_date` | `true` \| `false` | When `status=open`, apply `date_filter` (default: skip) |

### Examples

**Open sessions** (all open for shift — includes stale from prior days):

```bash
curl -s "http://localhost:8000/rollcall/sessions/?shift_id=<SHIFT_UUID>&status=open" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

**Closed history (today):**

```bash
curl -s "http://localhost:8000/rollcall/sessions/?shift_id=<SHIFT_UUID>&status=closed&date_filter=today" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

**My sessions only:**

```bash
curl -s "http://localhost:8000/rollcall/sessions/?shift_id=<SHIFT_UUID>&status=all&user_id=<USER_UUID>&date_filter=week" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

**Admin list** (same endpoint):

```bash
curl -s "http://localhost:8000/rollcall/sessions/?date_filter=today&status=open&location_id=<LOCATION_UUID>&filter_open_by_date=true" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN"
```

Legacy alias (still works): `GET /rollcall/dashboard/filter/`

---

## 3) Start a new session (upload start / group photo)

```bash
curl -X POST "http://localhost:8000/rollcall/sessions/start/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -F "shift_id=<SHIFT_UUID>" \
  -F "start_photo=@/path/to/group_start.jpg"
```

Creates an **open** session. Returns the full session object.

---

## 4) End an open session (upload end photo)

Any user at the same location can end:

```bash
curl -X POST "http://localhost:8000/rollcall/sessions/<SESSION_UUID>/end/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -F "end_photo=@/path/to/group_end.jpg"
```

Sets status to **closed** and returns the session with paired start + end photos and both uploader details.

---

## Example session payload (GET)

```json
{
  "id": "...",
  "status": "closed",
  "shift_date": "2026-07-16",
  "location_id": "...",
  "location_name": "Idaman Perdana",
  "shift_id": "...",
  "shift_name": "Day",
  "shift_start_time": "07:00:00",
  "shift_end_time": "19:00:00",
  "shift_time": "07:00:00–19:00:00",
  "started_at": "2026-07-16T07:05:00+08:00",
  "ended_at": "2026-07-16T19:02:00+08:00",
  "start_photo": "https://.../media/roll_call_starts/....jpg",
  "end_photo": "https://.../media/roll_call_ends/....jpg",
  "started_by": "...",
  "started_by_name": "User A",
  "started_by_employee_code": "1001",
  "started_by_role": "guard",
  "ended_by": "...",
  "ended_by_name": "User B",
  "ended_by_employee_code": "1002",
  "ended_by_role": "guard"
}
```

---

## Mobile UI flow

1. Load shifts → `GET /scheduler/shifts/by-location/<location_id>/`
2. Select shift → `GET /rollcall/sessions/?shift_id=...&status=open`
3. Show each open session with **Upload end**
4. Show **New session** → `POST /rollcall/sessions/start/`
5. History → `GET /rollcall/sessions/?shift_id=...&status=closed&date_filter=today`
