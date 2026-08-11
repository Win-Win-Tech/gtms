# Visitor API — New Fields & Query Params

New client-facing keys only. Fields set automatically by the backend (from auth token) are not listed.

---

## POST — new body fields

Applies to:

| Method | Endpoint |
|--------|----------|
| `POST` | `/visitors/entries/checkin/` |
| `POST` | `/visitors/entries/invite/` |
| `POST` / `PATCH` | `/visitors/entries/<entry_id>/complete-invite/` (when updating invite / completing verification) |

| Field | Type | Required | Allowed values | Notes |
|-------|------|----------|----------------|-------|
| `vehicle_type` | string | No | `car`, `motorcycle`, `van`, `truck`, `bus`, `other`, or empty | Omit or send `""` for none. Invalid value → `400`. |
| `company_name` | string | No | any text | Blank allowed. Max length 255. |

### Example (multipart / form)

```text
vehicle_type=car
company_name=XYZ
```

### Example (JSON)

```json
{
  "vehicle_type": "car",
  "company_name": "XYZ"
}
```

---

## GET — new query params

Applies to list and export endpoints that use the same filters:

| Method | Endpoint |
|--------|----------|
| `GET` | `/visitors/entries/` |
| `GET` | `/visitors/entries/export/` |
| `GET` | `/visitors/entries/export-pdf/` |

| Query param | Type | Required | Allowed values | Notes |
|-------------|------|----------|----------------|-------|
| `vehicle_type` | string | No | `car`, `motorcycle`, `van`, `truck`, `bus`, `other` | Filters entries by vehicle type. Omit or use with existing filters (`date_filter`, `status`, etc.). Invalid value → `400`. |

### Example

```text
GET /visitors/entries/?date_filter=today&vehicle_type=car
```
