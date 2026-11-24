# Unscheduled checkpoint scan API

Separate from scheduled `POST /checkin/checkins/` and `POST /checkin/checkins/scan_v2/`.  
Those APIs are **unchanged**.

## When to use

| Assignment | API |
|------------|-----|
| Has scheduled checkpoints (`[{checkpoint_id, time, ...}, ...]`) | Existing `scan` / `scan_v2` |
| Has shift assignment with **empty** `checkpoints` | **`scan_unscheduled`** |

## Endpoints

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/checkin/checkins/scan_unscheduled/` | Create an unscheduled scan |
| `GET` | `/checkin/checkins/unscheduled_scanned/` | List checkpoints **already scanned** in the unscheduled shift window |

Auth: Bearer token (same as other check-in APIs)

There is **no upcoming list** for unscheduled (any org checkpoint may be scanned). Use this GET instead of `GET /scheduler/assignments/upcoming-checkpoints/<user_id>/` when `allow_unschedule_scan` is true.

---

## Scan — `POST /checkin/checkins/scan_unscheduled/`

### Body

```json
{
  "guard": "<uuid>",
  "shift": "<uuid>",
  "assign_id": "<uuid>",
  "latitude": 12.9716,
  "longitude": 77.5946,
  "data": "<qr payload>",
  "timestamp": "<optional ISO datetime>"
}
```

| Field | Required | Notes |
|-------|----------|-------|
| `guard` | yes | Guard UUID |
| `shift` | yes | Shift UUID |
| `assign_id` | yes | Today’s assignment UUID (`assignment_id` from `shift_today_v5`) |
| `data` | yes | Scanned QR payload — used to **look up** the org checkpoint (`Checkpoint.data`) |
| `latitude` / `longitude` | yes | Guard scan location |
| `timestamp` | no | ISO datetime; defaults to now |
| `checkpoint` / `checkpoint_id` | **ignored** | Do not send; checkpoint is resolved from `data` only |

### Rules (kept from scheduled scan, except slot timing)

- Guard + assignment + shift must match
- Assignment must **not** have scheduled checkpoint slots
- Checkpoint is resolved by QR `data` within the assignment/guard **organisation**
- Distance ≤ org `distance` site setting (default 150 m)
- Scan time must fall inside the **shift window** (overnight-aware: yesterday’s overnight still open, or today’s instance)
- No checklist path on this API
- Same checkpoint may be scanned again, but **not within 1 minute** of a previous scan by the same guard

### Not applied (scheduled-only)

- Matching an assigned checkpoint time slot
- `+-` delay / grace against that slot (`SiteSetting` key `time`)

### Mobile hint — `shift_today_v5`

`GET /dashboard/attendance/shift_today_v5/` includes:

| Key | Meaning |
|-----|---------|
| `has_shift` | Guard has a shift today |
| `assignment_id` | Today's `Assignment` UUID (or `null` if no shift) — send as `assign_id` on scan APIs |
| `shift_id` | Shift UUID — send as `shift` on scan APIs |
| `allow_unschedule_scan` | `true` when shift today **and** no scheduled checkpoints → use `scan_unscheduled`; otherwise `false` |

### QR patrol report

Unscheduled scans appear in check-in / QR patrol reports with status **`Scanned`** (not On Time / Missed). Scheduled column is empty.

### Success (`201`)

Same `CheckIn` row as scheduled scans (`has_checklist=false`), plus:

```json
{
  "scan_mode": "unscheduled",
  "requires_checklist": false,
  "success": true,
  "message": "Checkpoint Successfully Scanned",
  "distance_from_checkpoint_m": 12.3,
  "shift_start_date": "2026-11-20"
}
```

### Common errors

| Case | Status |
|------|--------|
| Missing / empty QR `data` | `400` — QR data is required |
| No org checkpoint for that QR `data` | `400` — Checkpoint not found for this QR code |
| Assignment has scheduled checkpoints | `400` — use scheduled scan API |
| Outside shift window | `400` |
| Same CP scanned within 1 minute | `400` |
| Too far | `403` |
| No matching assignment | `403` |

---

## Scanned list — `GET /checkin/checkins/unscheduled_scanned/`

Returns checkpoints scanned during the resolved unscheduled **shift window** (same overnight-aware window as scan). Not for scheduled assignments.

### Query params

| Param | Required | Notes |
|-------|----------|-------|
| `assign_id` | yes (for data) | Assignment UUID from `shift_today_v5` (`assignment_id`). Guard + shift are loaded from the assignment — **do not send** `guard` / `shift`. |

Missing / blank / unknown `assign_id` → **200** with empty `scanned` (not an error).

### cURL

```bash
curl -G "http://127.0.0.1:8000/checkin/checkins/unscheduled_scanned/" \
  -H "Authorization: Bearer <access_token>" \
  --data-urlencode "assign_id=99999999-8888-7777-6666-555555555555"
```

### Success (`200`) — with scans

```json
{
  "assign_id": "99999999-8888-7777-6666-555555555555",
  "shift_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
  "shift_start_date": "2026-11-24",
  "shift_start": "2026-11-24T09:00:00+05:30",
  "shift_end": "2026-11-24T18:00:00+05:30",
  "window_active": true,
  "scanned": [
    {
      "checkin_id": "c1c1c1c1-c1c1-c1c1-c1c1-c1c1c1c1c1c1",
      "assign_id": "99999999-8888-7777-6666-555555555555",
      "checkpoint_id": "cp000001-0000-0000-0000-000000000001",
      "label": "Gate A",
      "lat": 12.9716,
      "lon": 77.5946,
      "qr": "QR-GATE-A",
      "shift_id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
      "status": "completed",
      "synced": true,
      "scanned_at": "2026-11-24T10:15:00+05:30",
      "scan_mode": "unscheduled"
    }
  ]
}
```

### Success (`200`) — no `assign_id`

```json
{
  "assign_id": null,
  "shift_id": null,
  "shift_start_date": null,
  "shift_start": null,
  "shift_end": null,
  "window_active": false,
  "scanned": []
}
```

| Field | Meaning |
|-------|---------|
| `window_active` | `true` if now is inside the shift window |
| `scanned` | Ordered by scan time ascending; empty if none yet |
| `scanned[].status` | Always `completed` for physical unscheduled scans |

### Common errors

| Case | Status |
|------|--------|
| Assignment has scheduled checkpoints | `400` — use upcoming-checkpoints |
