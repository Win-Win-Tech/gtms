# Vehicle overstay — Phase 2 (threshold + whitelist + worker)

Org-wide overstay detection for **still checked-in** vehicles. SOS delivery to roles is **Phase 3**.

| | |
|---|---|
| **Auth** | `Authorization: Bearer <JWT>` |
| **Preferred paths** | `/visitors/v5/...` |

---

## Setting

| Key | Default | Unit | Scope |
|-----|---------|------|-------|
| `vehicle_overstay_hours` | `4` | `h` | Org `SiteSetting` (editable under **Settings**) |

If a vehicle stays **checked in** longer than this many hours → candidate for overstay SOS (unless whitelisted).

---

## Whitelist APIs (org-wide)

| Method | Endpoint | Use |
|--------|----------|-----|
| `GET` | `/visitors/v5/overstay-whitelist/?location_id=&search=&page=&page_size=` | List (paginated) |
| `POST` | `/visitors/v5/overstay-whitelist/` | Add plate |
| `DELETE` | `/visitors/v5/overstay-whitelist/<id>/` | Soft-delete |

### POST body

```json
{
  "location_id": "ORG_UUID",
  "vehicle_number": "TN58BM9080",
  "notes": "optional"
}
```

Plate is normalized (uppercase, alphanumeric) like ANPR.

### List response

```json
{
  "results": [
    {
      "id": "...",
      "location_id": "...",
      "vehicle_number": "TN58BM9080",
      "notes": "",
      "created_by": "...",
      "created_by_name": "Admin",
      "created_on": "2026-09-22T10:00:00+05:30"
    }
  ],
  "page": 1,
  "page_size": 25,
  "total": 1,
  "total_pages": 1
}
```

### Suggest vehicle numbers (for whitelist add form)

```http
GET /visitors/v5/overstay-whitelist/vehicle-suggestions/?location_id=&site_id=&q=TN&limit=15
```

- `site_id` — from header site dropdown (omit / `all` = all sites in the org)
- `q` — typed keys; matches `VisitorEntry.vehicle_number`
- Returns `{ "results": ["TN58BM9080", ...] }` (distinct, normalized)

---

## Worker

Celery Beat task (every 5 minutes):

- Name: `visitor.tasks.check_vehicle_overstay`
- Selects: `status=checked_in`, non-empty `vehicle_number`, `overstay_alert_sent_at` null, check-in older than org hours
- Skips org whitelist plates
- **Phase 2:** notify stub returns false → does **not** set `overstay_alert_sent_at` (so Phase 3 can still alert once recipients exist)
- **Phase 3:** real TrackingAlert + mark `overstay_alert_sent_at` (once per visit)

---

## DB fields

- `VisitorEntry.overstay_alert_sent_at` — set when SOS is actually sent
- `VehicleOverstayWhitelist` — org (`location`) + `vehicle_number`
