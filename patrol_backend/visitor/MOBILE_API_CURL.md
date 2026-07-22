# Visitor Management — Mobile / Guard API (curl)

Auth: `Authorization: Bearer <TOKEN>`

Base: `http://localhost:8000/visitors/`

## Statuses

`pending_approval` | `reverted` | `scheduled` | `checked_in` | `checked_out` | `cancelled`

**Reject = Cancel** (`cancelled`, QR expired).

## Flows

| Flow | Steps |
|------|--------|
| **Manual entry** | Submit → `pending_approval` + QR → host **Approve** / **Reject(Cancel)** / **Revert** / **Reschedule** |
| **Resubmit** | After `reverted`, POST checkin with `entry_id` → `pending_approval` |
| **Reschedule** | Update arrival + out (+ visit_date). Same day → `pending_approval`. Other day → `scheduled`. Never auto check-in. |
| **Invitation** (Iter 3) | Create → `scheduled`. Visit-day QR scan → `pending_approval` + host approval (not direct check-in). |
| **Checkout** | While `checked_in` — **`exit_photo` required** → `checked_out`, `qr_expired=true` |

### QR scan actions

| Entry status | Response `action` |
|--------------|-------------------|
| `pending_approval` | `awaiting_approval` |
| `reverted` | `reverted` |
| `scheduled` + visit day | → `pending_approval` then `awaiting_approval` |
| `scheduled` + future day | `too_early` |
| `checked_in` | `checkout` |
| `checked_out` / `cancelled` / `qr_expired` | `expired` |

---

## Search visitor by IC

Returns visitor profile + `last_entry` (type, host, purpose, vehicle, remarks) for form prefill.
Images and expected times are **not** included — capture fresh on each visit.

```bash
curl -X GET "http://localhost:8000/visitors/search/?ic_number=900101145678&location_id=<LOCATION_UUID>" \
  -H "Authorization: Bearer <TOKEN>"
```

---

## Manual entry submit (→ `pending_approval` + QR)

`visit_date` is set automatically to **today**. Expected arrival/out (if sent) must also be **today**.

Response includes `qr_image_url` (raw QR) and `pass_image_url` (ID-card pass: org, name, visit date, host, QR). Pass/QR regenerate on create, resubmit, and reschedule; old files are deleted.

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "phone_number=0123456789" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "purpose_of_visit=Meeting" \
  -F "vehicle_number=TN58B8050" \
  -F "remarks=Just for testing" \
  -F "expected_arrival_time=2026-07-22T10:00" \
  -F "expected_out_time=2026-07-22T18:00" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg" \
  -F "additional_images=@/path/to/extra1.jpg"
```

---

## Resubmit after revert

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "entry_id=<REVERTED_ENTRY_UUID>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "expected_arrival_time=2026-07-22T11:00" \
  -F "expected_out_time=2026-07-22T18:00" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg"
```

---

## Host approve (= check-in)

Only from `pending_approval`.

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/approve/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"expected_out_time":"2026-07-22T18:00"}'
```

---

## Host revert

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/revert/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"revert_reason":"Wrong IC number"}'
```

---

## Reject / Cancel (same status)

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/cancel/" \
  -H "Authorization: Bearer <TOKEN>"
```

---

## Host reschedule

Requires `expected_arrival_time` + `expected_out_time`. Optional `visit_date` (else derived from arrival local date).

Same day → stays/moves to `pending_approval`. Future day → `scheduled`.

```bash
# Same day, different time
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/reschedule/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_arrival_time":"2026-07-22T15:00",
    "expected_out_time":"2026-07-22T19:00",
    "visit_date":"2026-07-22"
  }'

# Another day
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/reschedule/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_arrival_time":"2026-07-25T10:00",
    "expected_out_time":"2026-07-25T17:00",
    "visit_date":"2026-07-25"
  }'
```

---

## QR scan

```bash
curl -X POST "http://localhost:8000/visitors/qr-scan/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"qr_token":"<QR_TOKEN>"}'
```

---

## Download visitor pass

```bash
curl -X GET "http://localhost:8000/visitors/entries/<ENTRY_UUID>/pass/" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor-pass.png
```

Authenticated pass download (ID-card PNG). Prefer this over fetching `pass_image_url` directly from `/media/` (avoids CORS).

---

## AI extract (ID / vehicle) — Iteration 2

Full setup (Windows / Ubuntu), package list, and code explanation:

→ **`visitor/docs/VISITOR_AI_OCR.md`**

```bash
# type=id — extract IC / passport / Aadhaar-like number
curl -X POST "http://localhost:8000/visitors/ai/extract/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "type=id" \
  -F "image=@/path/to/id.jpg"

# type=vehicle — detect vehicle + extract plate
curl -X POST "http://localhost:8000/visitors/ai/extract/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "type=vehicle" \
  -F "image=@/path/to/car.jpg"
```

---

## Checkout

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/checkout/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "exit_photo=@/path/to/exit.jpg"
```

---

## List / export

`date_filter` matches **`visit_date`** (day the visit is for). If `visit_date` is null, falls back to `check_in_time`, then `created_on`.

Values: `today` | `upcoming` (visit_date after today) | `this_week` | `this_month` | `custom` | `all`

```bash
curl -X GET "http://localhost:8000/visitors/entries/?date_filter=today&status=pending_approval&mine=true" \
  -H "Authorization: Bearer <TOKEN>"

curl -X GET "http://localhost:8000/visitors/entries/?date_filter=upcoming&status=scheduled" \
  -H "Authorization: Bearer <TOKEN>"

curl -X GET "http://localhost:8000/visitors/entries/export/?date_filter=today" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor_entries.xlsx
```
