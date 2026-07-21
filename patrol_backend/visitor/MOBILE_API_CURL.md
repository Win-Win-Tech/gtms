# Visitor Management — Mobile / Guard API (curl)

Auth: `Authorization: Bearer <TOKEN>`

Base: `http://localhost:8000/visitors/`

## Statuses

`pending_approval` | `reverted` | `scheduled` | `checked_in` | `checked_out` | `cancelled`

## Flows

| Flow | Steps |
|------|--------|
| **Manual entry** | Submit → `pending_approval` + QR → host **approve** (= check-in) or **revert** / **cancel** |
| **Resubmit** | After `reverted`, POST checkin again with `entry_id` → back to `pending_approval` |
| **Invitation** (later) | `scheduled` → QR scan (= check-in, no host approval) |
| **Checkout** | QR scan while `checked_in`, or manual checkout — **`exit_photo` required** → `checked_out`, `qr_expired=true` |

### QR scan actions

| Entry status | Response `action` |
|--------------|-------------------|
| `pending_approval` | `awaiting_approval` |
| `reverted` | `reverted` |
| `scheduled` | check-in → `checked_in` |
| `checked_in` | `checkout` (then call checkout with image) |
| `checked_out` / `cancelled` / `qr_expired` | `expired` |

---

## Search visitor by IC

```bash
curl -X GET "http://localhost:8000/visitors/search/?ic_number=900101145678&location_id=<LOCATION_UUID>" \
  -H "Authorization: Bearer <TOKEN>"
```

---

## Manual entry submit (→ `pending_approval` + QR)

- `host_id` required  
- `visitor_photo` + `id_proof` required  
- `visit_date` is set automatically to **today** (location timezone)  
- `expected_out_time` optional (guard can set; host can change on approve)  
- Superuser: pass `location_id`. Non-superuser: uses own location.

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
  -F "expected_arrival_time=2026-07-21T10:00" \
  -F "expected_out_time=2026-07-21T18:00" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg" \
  -F "additional_images=@/path/to/extra1.jpg" \
  -F "additional_images=@/path/to/extra2.jpg"
```

`visitor_type`: `guest` | `contractor` | `client` | `delivery` | `other`

---

## Resubmit after revert

Same checkin endpoint. Only works when entry status is `reverted`. Photos optional if keeping existing assets.

```bash
curl -X POST "http://localhost:8000/visitors/entries/checkin/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "entry_id=<REVERTED_ENTRY_UUID>" \
  -F "location_id=<LOCATION_UUID>" \
  -F "ic_passport_number=900101145678" \
  -F "visitor_name=Ahmad Bin Ali" \
  -F "phone_number=0123456789" \
  -F "host_id=<HOST_USER_UUID>" \
  -F "visitor_type=guest" \
  -F "purpose_of_visit=Meeting (corrected)" \
  -F "expected_arrival_time=2026-07-21T10:00" \
  -F "expected_out_time=2026-07-21T18:00" \
  -F "visitor_photo=@/path/to/photo.jpg" \
  -F "id_proof=@/path/to/id.jpg"
```

---

## Host approve (= check-in)

Only host (or superuser). Entry must be `pending_approval` (or `reverted` in API — prefer resubmit first).  
`expected_out_time` optional — if sent, overwrites entry value; if omitted, keeps existing.

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/approve/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"expected_out_time":"2026-07-21T18:00"}'
```

Without changing out time:

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/approve/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{}'
```

---

## Host revert

Only from `pending_approval`. Guard must resubmit with `entry_id`.

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/revert/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"revert_reason":"Wrong IC number"}'
```

---

## Cancel (QR expires)

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/cancel/" \
  -H "Authorization: Bearer <TOKEN>"
```

---

## QR scan

```bash
curl -X POST "http://localhost:8000/visitors/qr-scan/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{"qr_token":"<QR_TOKEN>"}'
```

Example responses:

- `{"action":"awaiting_approval","message":"...","entry":{...}}`
- `{"action":"reverted","message":"...","entry":{...}}`
- `{"action":"checked_in","entry":{...}}` (invite scan)
- `{"action":"checkout","entry":{...}}` (then call checkout)
- `{"action":"expired",...}` (HTTP 400)

---

## Checkout (image required, QR expires)

```bash
curl -X POST "http://localhost:8000/visitors/entries/<ENTRY_UUID>/checkout/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "exit_photo=@/path/to/exit.jpg"
```

Alias field name also accepted: `checkout_image`.

---

## List entries (history)

```bash
curl -X GET "http://localhost:8000/visitors/entries/?date_filter=today&status=pending_approval" \
  -H "Authorization: Bearer <TOKEN>"
```

Query params:

| Param | Values / notes |
|-------|----------------|
| `date_filter` | `today` (default), `this_week`, `this_month`, `custom`, `all` |
| `start_date` / `end_date` | `YYYY-MM-DD` when `date_filter=custom` |
| `status` | e.g. `pending_approval`, `reverted`, `checked_in`, `checked_out`, `cancelled` |
| `location_id` | filter org (superuser) |
| `search` | name / IC / phone |
| `mine` | `true` — host inbox only |

```bash
curl -X GET "http://localhost:8000/visitors/entries/?date_filter=custom&start_date=2026-07-01&end_date=2026-07-21&search=Ahmad&mine=true" \
  -H "Authorization: Bearer <TOKEN>"
```

---

## Excel export

Same filters as list:

```bash
curl -X GET "http://localhost:8000/visitors/entries/export/?date_filter=today&status=all" \
  -H "Authorization: Bearer <TOKEN>" \
  -o visitor_entries.xlsx
```
