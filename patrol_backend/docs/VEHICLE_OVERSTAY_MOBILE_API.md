# Vehicle Overstay — Mobile API Guide

Share this with the mobile team. Prefer **`/visitors/v5/...`** paths (site-scoped + access checks). Legacy `/visitors/...` (without `v5`) exists for the same resources where noted.

| | |
|---|---|
| **Auth** | `Authorization: Bearer <JWT>` |
| **Base** | `{API_BASE_URL}` (e.g. `http://host:8001`) |
| **Content-Type** | `application/json` (except file downloads) |

**Module overview**

1. Overstay alert **roles** per site (who receives SOS)
2. **Whitelist** (org-wide plates skipped by SOS; optional on report)
3. **Push + inbox** notifications (`type = vehicle_overstay`)
4. **Overstay report** JSON / Excel / PDF

---

## 1. Roles list (for alert recipient picker)

Used to populate the role multi-select before saving overstay alert recipients.

### `GET /auth/roles/`

| Query | Required | Notes |
|-------|----------|--------|
| `location_id` | Recommended | Org UUID. Omit / `all` only for superadmin global templates. |

**Response** (array of roles):

```json
[
  {
    "id": 1,
    "name": "so",
    "location": "...",
    "is_default": false,
    "is_allow_webapp": true,
    "is_allow_edit": true,
    "is_allow_create": true,
    "pages": []
  }
]
```

> **Note:** Org **Admin** users always receive overstay SOS even if Admin is not selected here. Do not require Admin in `role_ids` for delivery — Admin is implicit on the server.

---

## 2. Overstay alert recipients (role selection)

Per-**site** list of roles that receive vehicle-overstay push + inbox notifications.

### `GET /visitors/v5/overstay-alert-recipients/?site_id={SITE_UUID}`

**Query**

| Param | Required |
|-------|----------|
| `site_id` | Yes |

**200 response**

```json
{
  "site_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "site_name": "Ravee Home",
  "role_ids": ["1", "2"],
  "results": [
    {
      "id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "site_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
      "site_name": "Ravee Home",
      "recipient_role_id": 1,
      "recipient_role_name": "so"
    }
  ]
}
```

### `PUT /visitors/v5/overstay-alert-recipients/`

Replaces the full role list for that site.

**Body**

```json
{
  "site_id": "xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx",
  "role_ids": [1, 2]
}
```

| Field | Type | Notes |
|-------|------|--------|
| `site_id` | UUID string | Required |
| `role_ids` | `number[]` or `string[]` | Required. Empty array `[]` clears all configured roles (SOS then goes to **Admin only** until roles are set again). |

**200 response** — same shape as GET.

**Errors**

| Status | Example |
|--------|---------|
| 400 | `site_id is required`, `role_ids is required (array)`, `Unknown role(s) for this organisation: ...` |
| 403 | Site / location forbidden |

---

## 3. Whitelist (optional for mobile)

Org-wide plates **skipped by Celery SOS**. Report can still include them when `include_whitelist=true`.

| Method | Endpoint |
|--------|----------|
| `GET` | `/visitors/v5/overstay-whitelist/?location_id=&search=&page=&page_size=` |
| `POST` | `/visitors/v5/overstay-whitelist/` |
| `DELETE` | `/visitors/v5/overstay-whitelist/{whitelist_id}/` |
| `GET` | `/visitors/v5/overstay-whitelist/vehicle-suggestions/?location_id=&site_id=&q=&limit=` |

### POST body

```json
{
  "location_id": "ORG_UUID",
  "vehicle_number": "TN58BM9080",
  "notes": "optional"
}
```

### GET list response

```json
{
  "results": [
    {
      "id": "...",
      "location_id": "...",
      "vehicle_number": "TN58BM9080",
      "notes": "",
      "created_by": "...",
      "created_by_name": "admin",
      "created_on": "2026-09-23T10:00:00+05:30"
    }
  ],
  "page": 1,
  "page_size": 25,
  "total": 1,
  "total_pages": 1
}
```

### DELETE response

```json
{ "ok": true }
```

---

## 4. Device token (required for FCM)

Register before overstay (or any) pushes can reach the device.

### `POST /notifications/device-token/`

```json
{
  "token": "<FCM_DEVICE_TOKEN>",
  "device_type": "android",
  "device_id": "optional-stable-device-id",
  "app_version": "1.0.0"
}
```

| Field | Values |
|-------|--------|
| `device_type` | `android` \| `ios` \| `web` |

**200 / 201** — device token object.

### `DELETE /notifications/device-token/`

Body or query: `token=<FCM_DEVICE_TOKEN>`

```json
{ "deactivated": true }
```

---

## 5. Notifications — list / read (inbox)

Overstay alerts appear in the same visitor notification inbox as approve/cancel.

### `GET /notifications/`

| Query | Default | Notes |
|-------|---------|--------|
| `unread` | — | `true` / `1` / `yes` → unread only |
| `limit` | `50` | Max `200` |
| `offset` | `0` | |

**200 response**

```json
{
  "count": 12,
  "limit": 50,
  "offset": 0,
  "unread_count": 3,
  "results": [
    {
      "id": "notif-uuid",
      "type": "vehicle_overstay",
      "title": "Vehicle overstay",
      "body": "Vehicle TN59E0316 overstay at Ravee Home. Check-in: ...",
      "data": {
        "type": "vehicle_overstay",
        "entry_id": "entry-uuid",
        "visitor_name": "",
        "ic_passport_number": "",
        "status": "checked_in",
        "host_id": "",
        "location_id": "org-uuid",
        "entry_source": "cctv",
        "vehicle_number": "TN59E0316",
        "site_id": "site-uuid",
        "site_name": "Ravee Home",
        "check_in_time": "2026-09-22T07:34:00+00:00"
      },
      "related_entry": "entry-uuid",
      "channel": "push",
      "delivery_status": "sent",
      "sent_at": "2026-09-23T12:00:00+05:30",
      "read_at": null,
      "is_read": false,
      "created_on": "2026-09-23T12:00:00+05:30"
    }
  ]
}
```

### Filter overstay only (client-side)

```text
results.filter(n => n.type === "vehicle_overstay")
```

There is no server `type=` query param today — filter on the client, or request one if needed.

### `POST /notifications/{notification_id}/read/`

Marks one notification read. **200** → same notification object with `read_at` / `is_read: true`.

### `POST /notifications/read-all/`

```json
{ "marked_read": 5 }
```

---

## 6. Notification / FCM payload (`vehicle_overstay`)

### When it fires

Celery beat runs `visitor.tasks.check_vehicle_overstay`:

- Entry still `checked_in`, has `vehicle_number`
- Stay duration ≥ org setting `vehicle_overstay_hours` (default **4**)
- Plate **not** on org whitelist
- **Once per visit** (not repeated for the same entry)
- Recipients = users with selected site roles **+ always Admin** for that org

### Push data (FCM `data` — all string values)

Same keys as `NotificationLog.data`:

| Key | Description |
|-----|-------------|
| `type` | Always `vehicle_overstay` |
| `entry_id` | `VisitorEntry` UUID |
| `visitor_name` | Person name (may be empty if ANPR plate-as-name) |
| `ic_passport_number` | |
| `status` | e.g. `checked_in` |
| `host_id` | |
| `location_id` | Org UUID |
| `entry_source` | e.g. `cctv`, `manual` |
| `vehicle_number` | Normalized plate |
| `site_id` | Site UUID |
| `site_name` | Site display name |
| `check_in_time` | ISO datetime (UTC from server) |

### Title / body

| Field | Example |
|-------|---------|
| `title` | `Vehicle overstay` |
| `body` | `Vehicle TN59E0316 overstay at Ravee Home. Visitor: Vimal. Check-in: ...` |

### Mobile deep-link suggestion

```
type == "vehicle_overstay"  →  open visitor entry by data.entry_id
                             or overstay report filtered by vehicle_number / site_id
```

---

## 7. Overstay report APIs

All report endpoints share the same query params. Prefer **v5**.

| Method | Endpoint | Response |
|--------|----------|----------|
| `GET` | `/visitors/v5/reports/vehicle-overstay/` | JSON |
| `GET` | `/visitors/v5/reports/vehicle-overstay/export/` | Excel (`.xlsx`) |
| `GET` | `/visitors/v5/reports/vehicle-overstay/export-pdf/` | PDF |

### Query parameters

| Param | Required | Values / notes |
|-------|----------|----------------|
| `location_id` | Yes | Org UUID |
| `site_id` | No | Site UUID (v5 enforces site access). Omit / `all` = all sites in org |
| `date_filter` | No | `today` (default), `this_week`, `this_month`, `custom` |
| `start_date` | If custom | `YYYY-MM-DD` |
| `end_date` | If custom | `YYYY-MM-DD` |
| `include_whitelist` | No | `true` / `false` (default **false**) |
| `search` | No | Matches visitor name, phone, or vehicle number |
| `status` | No | `all` (default), `still_inside`, `checked_out` |
| `vehicle_type` | No | Lookup code (omit / `all` = no filter) |

> Custom range is **date-only** (full days). Do not send `start_time` / `end_time` for this report.

**Semantics — `date_filter=today`:** includes vehicles still inside that **checked in on a past date** and are still overstaying as of now. That is intentional for overstay.

### Example

```http
GET /visitors/v5/reports/vehicle-overstay/?location_id={ORG}&site_id={SITE}&date_filter=today&include_whitelist=false&status=all&search=
Authorization: Bearer {JWT}
```

### JSON response (`GET .../vehicle-overstay/`)

```json
{
  "location_id": "org-uuid",
  "location_name": "Test Org",
  "site_id": "site-uuid-or-null",
  "site_name": "Ravee Home",
  "overstay_hours": 4,
  "include_whitelist": false,
  "search": "",
  "status_filter": "all",
  "vehicle_type": null,
  "date_filter": "today",
  "date_range_display": "23/09/2026",
  "start_date": "2026-09-23",
  "end_date": "2026-09-23",
  "show_time": false,
  "total": 20,
  "total_still_inside": 18,
  "total_checked_out": 2,
  "rows": [
    {
      "id": "entry-uuid",
      "vehicle_number": "TN59E0316",
      "vehicle_type": "motorcycle",
      "vehicle_type_label": "Motorcycle",
      "visitor_name": "",
      "phone_number": "",
      "site_id": "site-uuid",
      "site_name": "Ravee Home",
      "visit_date": "2026-09-23",
      "visit_date_display": "23/09/2026",
      "check_in_time": "2026-09-23T07:26:00+00:00",
      "check_out_time": null,
      "check_in_display": "23/09/2026 12:56",
      "check_out_display": "—",
      "duration_hours": 4.09,
      "status": "still_inside",
      "status_label": "Still inside",
      "whitelisted": false
    }
  ]
}
```

| Row field | Notes |
|-----------|--------|
| `visitor_name` | Empty when missing or when name is only the plate (ANPR placeholder) |
| `status` | `still_inside` \| `checked_out` |
| `whitelisted` | Present in JSON; UI may hide column; Excel/PDF omit Whitelisted column |
| `duration_hours` | Float hours past threshold stay |

### Excel export

```http
GET /visitors/v5/reports/vehicle-overstay/export/?{same query params}
```

- `Content-Type`: `application/vnd.openxmlformats-officedocument.spreadsheetml.sheet`
- Filename: `vehicle_overstay_{YYYYMMDD}.xlsx`
- Columns: **S.No**, Visit Date, Vehicle Number, Vehicle Type, Visitor Name, Phone, Site, Check In, Check Out, Duration (h), Status

### PDF export

```http
GET /visitors/v5/reports/vehicle-overstay/export-pdf/?{same query params}
```

- `Content-Type`: `application/pdf`
- Filename: `vehicle_overstay_{YYYYMMDD}.pdf`
- Same columns as Excel (including **S.No**)

Mobile: download as blob / file; do not parse as JSON unless error (`application/json` error body on failure).

---

## 8. Quick reference (mobile checklist)

| Feature | Method | Path |
|---------|--------|------|
| List roles | `GET` | `/auth/roles/?location_id=` |
| Get alert roles | `GET` | `/visitors/v5/overstay-alert-recipients/?site_id=` |
| Update alert roles | `PUT` | `/visitors/v5/overstay-alert-recipients/` |
| Register FCM | `POST` | `/notifications/device-token/` |
| Notification inbox | `GET` | `/notifications/` |
| Mark one read | `POST` | `/notifications/{id}/read/` |
| Mark all read | `POST` | `/notifications/read-all/` |
| Report JSON | `GET` | `/visitors/v5/reports/vehicle-overstay/` |
| Report Excel | `GET` | `/visitors/v5/reports/vehicle-overstay/export/` |
| Report PDF | `GET` | `/visitors/v5/reports/vehicle-overstay/export-pdf/` |
| Whitelist list | `GET` | `/visitors/v5/overstay-whitelist/?location_id=` |
| Whitelist add | `POST` | `/visitors/v5/overstay-whitelist/` |
| Whitelist delete | `DELETE` | `/visitors/v5/overstay-whitelist/{id}/` |

**Push type string to handle:** `vehicle_overstay`

---

## Related internal docs

- [`VEHICLE_OVERSTAY_PHASE2.md`](./VEHICLE_OVERSTAY_PHASE2.md) — threshold + whitelist + worker
- [`VEHICLE_OVERSTAY_PHASE3.md`](./VEHICLE_OVERSTAY_PHASE3.md) — recipients + NotificationLog delivery
