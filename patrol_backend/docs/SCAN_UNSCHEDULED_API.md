# Unscheduled checkpoint scan API

Separate from scheduled `POST /checkin/checkins/` and `POST /checkin/checkins/scan_v2/`.  
Those APIs are **unchanged**.

## When to use

| Assignment | API |
|------------|-----|
| Has scheduled checkpoints (`[{checkpoint_id, time, ...}, ...]`) | Existing `scan` / `scan_v2` |
| Has shift assignment with **empty** `checkpoints` | **`scan_unscheduled`** |

## Endpoint

`POST /checkin/checkins/scan_unscheduled/`  
Auth: Bearer token (same as other check-in APIs)

### Body

```json
{
  "guard": "<uuid>",
  "shift": "<uuid>",
  "assign_id": "<uuid>",
  "checkpoint": "<uuid>",
  "latitude": 12.9716,
  "longitude": 77.5946,
  "data": "<qr payload matching checkpoint.data>",
  "timestamp": "<optional ISO datetime>"
}
```

### Rules (kept from scheduled scan, except slot timing)

- Guard + assignment + shift must match
- Assignment must **not** have scheduled checkpoint slots
- Checkpoint must belong to the assignment/guard **organisation** (checkpoints have no site)
- QR `data` must match checkpoint
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
| Assignment has scheduled checkpoints | `400` — use scheduled scan API |
| Outside shift window | `400` |
| Same CP scanned within 1 minute | `400` |
| QR mismatch | `400` |
| Too far | `403` |
| Checkpoint not in organisation | `403` |
| No matching assignment | `403` |
