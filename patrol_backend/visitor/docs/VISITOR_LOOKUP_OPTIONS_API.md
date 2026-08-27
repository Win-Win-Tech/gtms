# Visitor Lookup Options API — Visitor Types & Vehicle Types

Dynamic dropdown options for visitor type and vehicle type (same pattern as Roles: global templates + org-scoped copies).

**Base path:** `/visitors/lookup-options/`  
**Auth:** `Authorization: Bearer <access_token>` (required)

---

## Concepts

| Concept | Meaning |
|---------|---------|
| `kind` | `visitor_type` or `vehicle_type` |
| `code` | Stored slug on visitor entries (e.g. `guest`, `car`). Auto-generated from `label` if omitted on create. Immutable after create. |
| `label` | Display name in UI dropdowns |
| `location` | `null` = global template (superadmin). Set = org-specific row. |
| `is_default` | `true` for seeded system types. Org custom types are `false`. Defaults cannot be **deleted**. |
| `sort_order` | Dropdown / report display order (0-based). If the position is already taken, the other row is **swapped** (edit) or moved to the end (create). |
| `is_active` | Soft-disable; inactive options are hidden from entry dropdowns but historical entries keep their codes. |

### Resolution for list / validation

1. If the org has any rows for that location → use org rows.  
2. Else fall back to global templates (`location = null`).

Seeded defaults (codes preserved for existing entries):

| kind | codes |
|------|-------|
| `visitor_type` | `guest`, `contractor`, `client`, `delivery`, `other` |
| `vehicle_type` | `car`, `truck`, `van`, `motorcycle`, `bus`, `other` |

Empty vehicle on an entry is still stored as `""` (UI may show `none`). That is **not** a lookup-option row.

---

## Permissions

| Action | Who |
|--------|-----|
| `GET` list / retrieve | Any authenticated user |
| `POST` / `PATCH` / `PUT` / `DELETE` | `admin` or superadmin only |
| Create global template (`location = null`) | Superadmin |
| Create org-only type | Org admin (scoped to their location) |
| Edit org-scoped row (including system defaults’ label / sort / active) | Org admin for their org; superadmin for globals |
| Edit global template | Superadmin only |
| Delete | Admin/superadmin; **blocked** if `is_default=true` or code is in use on visitor entries |

---

## Object shape

```json
{
  "id": "8f8ed4a4-74a1-4c4e-bc1b-9ef5fd9fc6a5",
  "kind": "visitor_type",
  "code": "test_visitor",
  "label": "Test Visitor",
  "location": "259ea9c6-9e14-44b8-ad8a-52d4385060de",
  "is_default": false,
  "sort_order": 5,
  "is_active": true
}
```

| Field | Type | Create | Update | Notes |
|-------|------|--------|--------|-------|
| `id` | UUID | read-only | read-only | |
| `kind` | string | required | — | `visitor_type` \| `vehicle_type` |
| `code` | string | optional | ignored | Max 32. From label if omitted (`Test Visitor` → `test_visitor`). |
| `label` | string | required | optional | Display name |
| `location` | UUID \| null | read-only | read-only | Set by server from auth |
| `is_default` | boolean | read-only | read-only | |
| `sort_order` | int | optional | optional | Defaults to next free if omitted on create |
| `is_active` | boolean | optional | optional | Default `true` |

---

## Endpoints

### 1. List options

```http
GET /visitors/lookup-options/
GET /visitors/lookup-options/?location_id=<uuid>
GET /visitors/lookup-options/?location_id=<uuid>&kind=visitor_type
GET /visitors/lookup-options/?location_id=<uuid>&kind=vehicle_type
```

| Query | Required | Description |
|-------|----------|-------------|
| `location_id` | Recommended | Org to list for. Non-superadmins may only use their own location. |
| `kind` | No | Filter to `visitor_type` or `vehicle_type`. Omit to return both. |

**Response:** `200` — array (or paginated `{ "results": [...] }` if pagination is enabled).

```bash
curl 'http://localhost:8000/visitors/lookup-options/?location_id=<LOCATION_ID>&kind=visitor_type' \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Accept: application/json'
```

Use this for Manual Entry / Visitors filter dropdowns. Prefer one call without `kind` and split client-side if you need both.

---

### 2. Retrieve one

```http
GET /visitors/lookup-options/<id>/
```

**Response:** `200` — single object.

---

### 3. Create

```http
POST /visitors/lookup-options/
Content-Type: application/json
```

**Body**

```json
{
  "kind": "visitor_type",
  "label": "Test Visitor",
  "sort_order": 4,
  "is_active": true
}
```

Optional: `"code": "test_visitor"` (auto-generated from label if omitted).

**Sort behaviour (create)**  
If `sort_order` is already used by another option in the same org + kind, that other option is moved to the next free sort (end). The new row keeps the requested `sort_order`.

**Response:** `201` — created object.

```bash
curl 'http://localhost:8000/visitors/lookup-options/' \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Content-Type: application/json' \
  --data-raw '{
    "kind": "visitor_type",
    "label": "Test Visitor",
    "sort_order": 5,
    "is_active": true
  }'
```

**Errors**

| Status | When |
|--------|------|
| `403` | Not admin/superadmin |
| `400` | Invalid `kind`, missing/empty label, duplicate `(kind, code, location)` |

---

### 4. Update (partial)

```http
PATCH /visitors/lookup-options/<id>/
Content-Type: application/json
```

**Body (any subset)**

```json
{
  "label": "Test Visitor",
  "sort_order": 4,
  "is_active": true
}
```

`code` and `kind` are not changed on update.  
`location` / `is_default` are read-only.

**Sort behaviour (edit)**  
If the new `sort_order` is taken, the two rows **swap** positions (works for System and Custom rows).

```bash
curl 'http://localhost:8000/visitors/lookup-options/<ID>/' \
  -X PATCH \
  -H 'Authorization: Bearer <TOKEN>' \
  -H 'Content-Type: application/json' \
  --data-raw '{"sort_order": 4}'
```

**Errors**

| Status | When |
|--------|------|
| `403` | Not admin; or org admin editing a global template (`location = null`) |
| `400` | Validation error |
| `404` | Unknown id |

Also supports `PUT` with full body (same rules).

---

### 5. Delete

```http
DELETE /visitors/lookup-options/<id>/
```

**Response:** `204` on success.

**Errors**

| Status | When |
|--------|------|
| `400` | `is_default=true` (“Default system types cannot be deleted.”) |
| `400` | Code is referenced by visitor entries for that org |
| `403` | Not admin; or org admin deleting a global template |

```bash
curl 'http://localhost:8000/visitors/lookup-options/<ID>/' \
  -X DELETE \
  -H 'Authorization: Bearer <TOKEN>'
```

---

## How entry APIs use these options

Check-in / invite / filters no longer use hardcoded Django `choices`. They validate against lookup options for the entry’s `location_id`:

| Field on entry | Lookup `kind` |
|----------------|---------------|
| `visitor_type` | `visitor_type` |
| `vehicle_type` | `vehicle_type` (empty string still allowed for “no vehicle”) |

Invalid code for that org → `400` with allowed codes listed.

Vehicle movement report labels and row order also come from lookup options (`sort_order`).

---

## Frontend usage

| UI | Usage |
|----|--------|
| Visitors list filters | `GET ...?location_id=&kind=` (or both kinds) |
| Visitor Registration form | Same; prepend vehicle UI value `none` → API `""` |
| Type Settings tab | Full CRUD for admins |

Web route: `/visitor/type-settings`

---

## Notes for mobile / other clients

1. Always pass `location_id` when listing options for a known org.  
2. Cache list briefly; invalidate after create/update/delete.  
3. Do not hardcode type lists — use this API.  
4. On create, send `label` (+ optional `sort_order`); let the server generate `code`.  
5. To reorder, `PATCH` with `sort_order` only; swap is server-side.
