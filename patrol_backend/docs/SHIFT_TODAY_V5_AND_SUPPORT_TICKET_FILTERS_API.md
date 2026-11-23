# shift_today_v5 + Support ticket list filters

Combined API notes for:

1. New keys on `GET /dashboard/attendance/shift_today_v5/`
2. Filters on My Tickets and Ticket queue list APIs

**Auth:** JWT Bearer  
**Related:** [SCAN_UNSCHEDULED_API.md](./SCAN_UNSCHEDULED_API.md) · [SITE_WISE_SEGREGATION_V5_API.md](./SITE_WISE_SEGREGATION_V5_API.md) · [support_ticketing_api.md](./support_ticketing_api.md)

---

# Part 1 — `GET /dashboard/attendance/shift_today_v5/`

Mobile today’s-shift API (site-wise v5). Same payload as live `shift_today_v3`, plus posted-site fields and **checkpoint scan hints**.

## Endpoint

```http
GET /dashboard/attendance/shift_today_v5/
Authorization: Bearer <access_token>
```

No query params required. “Today” is the caller’s calendar day in their user timezone.

### cURL

```bash
curl -X GET "http://127.0.0.1:8000/dashboard/attendance/shift_today_v5/" \
  -H "Authorization: Bearer <access_token>"
```

## New / scan-related keys

| Field | Type | When set | Meaning |
|-------|------|----------|---------|
| `assignment_id` | UUID string \| `null` | Always present | Today’s `Assignment` id. Send as `assign_id` on scan / `scan_unscheduled`. `null` when `has_shift` is false. |
| `allow_unschedule_scan` | boolean | Always present | `true` when there is a shift today **and** the assignment has **no** scheduled checkpoint slots → use `POST /checkin/checkins/scan_unscheduled/`. Otherwise `false` (use scheduled `scan` / `scan_v2`). |

### How `allow_unschedule_scan` is decided

| Condition | `allow_unschedule_scan` |
|-----------|-------------------------|
| No shift today (`has_shift: false`) | `false` |
| Shift today + assignment `checkpoints` has scheduled slots | `false` → scheduled scan APIs |
| Shift today + empty / no scheduled checkpoints | `true` → unscheduled scan API |

`assignment_id` is resolved from the same today-assignment logic as `shift_today_v3` (including overnight / closest covering assignment fallback).

## Site fields (existing v5)

| Field | Source | Meaning |
|-------|--------|---------|
| `assigned_site_id` | `AssignmentDailySite` | Roster / posted site for today |
| `assigned_site_name` | same | |
| `last_selected_site_id` | `GuardSiteCache` latest, else daily site | Site the guard is working at now |
| `last_selected_site_name` | same | |

## Core fields (same as `shift_today_v3`)

Includes (non-exhaustive): `has_shift`, `show_checkin`, `show_checkout`, `message`, `shift_id`, `shift_name`, `shift_start`, `shift_end`, `location_name`, `last_checkin`, `last_checkout`, `duration_minutes`, `checkin_image`, `checkout_image`, etc.

## Example — shift with unscheduled checkpoints

```json
{
  "has_shift": true,
  "show_checkin": true,
  "show_checkout": false,
  "message": "You can check in",
  "shift_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "shift_name": "Day",
  "shift_start": "09:00:00",
  "shift_end": "18:00:00",
  "location_name": "Test Org",
  "assigned_site_id": "11111111-2222-3333-4444-555555555555",
  "assigned_site_name": "Main Gate",
  "last_selected_site_id": "11111111-2222-3333-4444-555555555555",
  "last_selected_site_name": "Main Gate",
  "assignment_id": "99999999-8888-7777-6666-555555555555",
  "allow_unschedule_scan": true
}
```

## Example — no shift today

```json
{
  "has_shift": false,
  "show_checkin": false,
  "show_checkout": false,
  "message": "No shifts today",
  "shift_name": null,
  "shift_start": null,
  "shift_end": null,
  "location_name": null,
  "last_checkin": null,
  "last_checkout": null,
  "duration_minutes": null,
  "checkin_image": null,
  "checkout_image": null,
  "assigned_site_id": null,
  "assigned_site_name": null,
  "last_selected_site_id": null,
  "last_selected_site_name": null,
  "assignment_id": null,
  "allow_unschedule_scan": false
}
```

## Mobile usage

| Goal | Use |
|------|-----|
| Punch attendance | `checkin_v5` / `checkout_v5` with `site_id` = `last_selected_site_id` |
| Scheduled QR scan | `scan` / `scan_v2` with `assign_id` = `assignment_id`, `shift` = `shift_id` |
| Unscheduled QR scan | Only when `allow_unschedule_scan === true` → `scan_unscheduled` with same `assign_id` / `shift` |

Live `GET .../shift_today_v3/` is unchanged (no `assignment_id` / `allow_unschedule_scan`).

---

# Part 2 — Support ticket list filters (My Tickets & Ticket queue)

Filter query params for the web **My Tickets** and **Tickets** tabs.

**Envelope:** `{ "status": "success"|"error", "message": "...", "data": [...], "pagination": {...} }`

Date presets and custom ranges are evaluated in the **viewer’s timezone** (same TZ used when serializing `created_on` / `updated_on` / `due_at`), then converted to UTC bounds for the query. Filtering on UTC calendar `__date` alone is **not** used — that caused “Yesterday” to include early-morning “today” rows in local time.

## Endpoints

| Tab | Method / path | Who |
|-----|---------------|-----|
| My Tickets | `GET /support/tickets/my/` | Any authenticated user (tickets they created) |
| Tickets (queue) | `GET /support/support/tickets/` | SUPPORT / Super Admin only |

Default `page_size`: **25** (my) · **50** (queue). Max `page_size`: **100**.

## Shared filter query params

Both list endpoints use `filter_support_tickets_queryset`.

| Param | Type | Notes |
|-------|------|-------|
| `status` | string | `OPEN` \| `IN_PROGRESS` \| `RESOLVED` \| `CLOSED` \| `INVALID`. Omit or `all` = no status filter. |
| `date_filter` | string | Preset (see below). Omit or `all` = no date filter. |
| `date_from` / `start_date` | `YYYY-MM-DD` | Custom range start (viewer calendar day). Used with `date_filter=custom` or alone. |
| `date_to` / `end_date` | `YYYY-MM-DD` | Custom range end (inclusive). |
| `priority` | string | `LOW` \| `MEDIUM` \| `HIGH` \| `CRITICAL` |
| `overdue` | string | `true` \| `1` \| `yes` — active tickets past `due_at` |
| `page` | int | default `1` |
| `page_size` | int | see defaults above |

### Date presets (`date_filter`)

Computed with **`get_user_today(viewer_tz)`**, then converted via `convert_date_range_to_utc`.

| Value | Range (viewer TZ) |
|-------|-------------------|
| `today` | Today only |
| `yesterday` | Yesterday only |
| `this_week` | Mon–Sun of current week |
| `last_week` | Previous Mon–Sun |
| `this_month` | 1st–last day of current month |
| `last_month` | Previous calendar month |
| `custom` | Requires `date_from` + `date_to` (or `start_date` / `end_date`) |
| `all` / empty | No date filter |

Date filter applies to ticket **`created_on`** (raise time), not `updated_on` or `due_at`.

### Queue-only (Tickets tab) scope params

| Param | Type | Notes |
|-------|------|-------|
| `organisation_id` / `location_id` | UUID | Org filter. `all` / `null` / empty ignored. |
| `site_id` | UUID | Site filter. `all` / `null` / empty ignored. |
| `assignee` | string | `me` \| `unassigned` (or omit) |
| `assignee_id` | UUID | Exact assignee |

Web header Location/Site switcher maps to `organisation_id` / `site_id` on the queue only.

## My Tickets

```http
GET /support/tickets/my/?status=OPEN&date_filter=today&page=1&page_size=25
Authorization: Bearer <access_token>
```

### cURL

```bash
# Today + OPEN
curl -G "http://127.0.0.1:8000/support/tickets/my/" \
  -H "Authorization: Bearer <access_token>" \
  --data-urlencode "status=OPEN" \
  --data-urlencode "date_filter=today"

# Custom range (viewer TZ calendar days)
curl -G "http://127.0.0.1:8000/support/tickets/my/" \
  -H "Authorization: Bearer <access_token>" \
  --data-urlencode "date_filter=custom" \
  --data-urlencode "date_from=2026-11-01" \
  --data-urlencode "date_to=2026-11-22"

# Yesterday
curl -G "http://127.0.0.1:8000/support/tickets/my/" \
  -H "Authorization: Bearer <access_token>" \
  --data-urlencode "date_filter=yesterday"
```

## Ticket queue (SUPPORT)

```http
GET /support/support/tickets/?status=IN_PROGRESS&date_filter=today&assignee=me&page=1&page_size=50
Authorization: Bearer <support_access_token>
```

### cURL

```bash
curl -G "http://127.0.0.1:8000/support/support/tickets/" \
  -H "Authorization: Bearer <support_access_token>" \
  --data-urlencode "status=OPEN" \
  --data-urlencode "date_filter=yesterday" \
  --data-urlencode "organisation_id=<org_uuid>" \
  --data-urlencode "site_id=<site_uuid>"

# Unassigned + overdue
curl -G "http://127.0.0.1:8000/support/support/tickets/" \
  -H "Authorization: Bearer <support_access_token>" \
  --data-urlencode "assignee=unassigned" \
  --data-urlencode "overdue=true"
```

Non-SUPPORT → **403**.

## Success (`200`) — list item shape

`data` is the current page array. Datetimes are ISO strings **in the viewer’s timezone** (offset included).

```json
{
  "status": "success",
  "message": "My tickets fetched",
  "data": [
    {
      "id": "11111111-2222-3333-4444-555555555555",
      "ticket_number": "SP-2026-00001",
      "subject": "GPS not updating",
      "status": "OPEN",
      "priority": "HIGH",
      "category": "...",
      "category_name": "Attendance / GPS",
      "organisation": "...",
      "organisation_name": "Test",
      "site": null,
      "site_name": null,
      "created_by": "...",
      "created_by_name": "Guard One",
      "current_assignee": null,
      "assignee_name": null,
      "due_at": "2026-11-23T03:57:00+05:30",
      "is_overdue": false,
      "sla_hours": 8,
      "created_on": "2026-11-22T03:57:00+05:30",
      "updated_on": "2026-11-22T03:57:00+05:30"
    }
  ],
  "pagination": {
    "page": 1,
    "page_size": 25,
    "total": 1,
    "total_pages": 1
  }
}
```

Queue success message: `"Support ticket queue fetched"`.

## Web UI defaults

| Control | Default |
|---------|---------|
| Ticket Status | All Status (`status` omitted) |
| Priority | All Priority (`priority` omitted) |
| Overdue | All (`overdue` omitted); **Overdue only** → `overdue=true` |
| Date | **Today** (`date_filter=today`) |
| Custom range | API called only after both Start and End dates are set |

## Notes

- Same filters apply to `GET /support/reports/` (and Excel/PDF exports) via the shared helper.
- Combining `status` + `date_filter` + org/site is AND logic.
