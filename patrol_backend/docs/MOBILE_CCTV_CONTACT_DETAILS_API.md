# CCTV contact details — Mobile API

Add visitor **name** and **phone** on CCTV ANPR rows (those entries are created with the plate as name and an empty phone).

| | |
|---|---|
| **Auth** | `Authorization: Bearer <JWT>` |
| **Base URL** | `http://localhost:8000` (replace with your server) |
| **Preferred paths** | `/visitors/v5/...` (site-wise). Live `/visitors/...` mirrors the same contract without `site_id` helpers. |
| **Errors** | `{ "error": "<message>" }` with HTTP 4xx/5xx |

Related: full visitor mobile contract → [`MOBILE_VISITOR_NOTIFICATIONS_API.md`](./MOBILE_VISITOR_NOTIFICATIONS_API.md)

---

## API index

| Method | Endpoint | Use |
|--------|----------|-----|
| `GET` | `/visitors/v5/entries/` | List entries — each row includes `needs_details` |
| `GET` | `/visitors/v5/entries/<ENTRY_UUID>/` | Detail — same `needs_details` field |
| `PATCH` | `/visitors/v5/entries/<ENTRY_UUID>/contact-details/` | Update name + phone (CCTV only) |
| `POST` | `/visitors/v5/entries/<ENTRY_UUID>/contact-details/` | Same as PATCH (alias for clients that prefer POST) |

Live (non-v5) equivalents:

- `GET /visitors/entries/`
- `GET /visitors/entries/<ENTRY_UUID>/`
- `PATCH|POST /visitors/entries/<ENTRY_UUID>/contact-details/`

---

## 1. `needs_details` (list / detail)

Boolean on every entry object. **Default `false`.**

| Value | When |
|-------|------|
| `true` | `entry_source` is `cctv` **and** contact details are still missing |
| `false` | Non-CCTV, **or** CCTV with real name **and** phone already filled |

### What counts as “missing”

ANPR stores the **vehicle number as `visitor_name`** and leaves `phone_number` empty. So:

- Name missing → blank **or** equal to `vehicle_number` (plate placeholder)
- Phone missing → blank / whitespace
- `needs_details` is `true` if **either** name or phone is still missing (for CCTV)

After a successful contact-details update (real person name + phone), list/detail return `needs_details: false`.

### UI rule (mobile)

- If `needs_details === true` → show **Add** (name + phone form)
- If `needs_details === false` → hide that action

---

## 2. Update contact details

### Request

```http
PATCH /visitors/v5/entries/<ENTRY_UUID>/contact-details/
Authorization: Bearer <JWT>
Content-Type: application/json
```

| Field | Type | Required | Notes |
|-------|------|----------|-------|
| `visitor_name` | string | **yes** | Real person name (not the plate) |
| `phone_number` | string | **yes** | Digits only, min 7, max 15 on web (API accepts up to 32) |

`POST` with the same body is also accepted.

### Success (200)

Returns the full entry object (same shape as list/detail), including updated `visitor_name`, `phone_number`, and `needs_details: false`.

### Errors

| HTTP | `error` |
|------|---------|
| 400 | `visitor_name is required` |
| 400 | `phone_number is required` |
| 400 | `phone_number must be digits only (min 7)` |
| 400 | `visitor_name must be the person name, not the vehicle number` |
| 400 | `Contact details can only be updated for CCTV entries` |
| 403 | `Forbidden` |
| 404 | `Entry not found` / `Visitor entry not found` |

---

## 3. cURL examples

### List (check the flag)

```bash
curl -sS 'http://HOST:8001/visitors/v5/entries/?date_filter=today&location_id=LOCATION_UUID' \
  -H 'Authorization: Bearer ACCESS_TOKEN' \
  -H 'Accept: application/json'
```

Relevant fields on each item:

```json
{
  "id": "f822e85b-7278-4135-88fe-0c03f9e66cad",
  "entry_source": "cctv",
  "vehicle_number": "TN60S2542",
  "visitor_name": "TN60S2542",
  "phone_number": "",
  "needs_details": true
}
```

After details are filled:

```json
{
  "entry_source": "cctv",
  "vehicle_number": "TN60S2542",
  "visitor_name": "Ravi Kumar",
  "phone_number": "9876543210",
  "needs_details": false
}
```

### Update name + phone

```bash
curl -sS -X PATCH 'http://HOST:8001/visitors/v5/entries/ENTRY_UUID/contact-details/' \
  -H 'Authorization: Bearer ACCESS_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "visitor_name": "Ravi Kumar",
    "phone_number": "9876543210"
  }'
```

`POST` alias:

```bash
curl -sS -X POST 'http://HOST:8001/visitors/v5/entries/ENTRY_UUID/contact-details/' \
  -H 'Authorization: Bearer ACCESS_TOKEN' \
  -H 'Content-Type: application/json' \
  -d '{
    "visitor_name": "Ravi Kumar",
    "phone_number": "9876543210"
  }'
```

---

## 4. Notes for mobile

1. Name and phone live on the **Visitor** person record linked to the entry (not separate entry columns). List/detail already flatten them as `visitor_name` / `phone_number`.
2. Only **CCTV** (`entry_source: "cctv"`) may call contact-details.
3. Prefer **`needs_details`** over inventing client-side empty checks (plate-as-name is not a real name).
4. After save, refresh the list or apply the returned entry object so the Add button disappears.
