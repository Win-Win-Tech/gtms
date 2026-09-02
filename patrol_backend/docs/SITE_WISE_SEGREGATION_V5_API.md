# Site-wise Segregation — v5 API Reference

**Status:** Implemented for Users + Foundation (GET/POST my-sites) + Shift assign + Incident + Roll call + Visitor  
**Date:** 21 Aug 2026  
**Auth:** JWT `Authorization: Bearer <access>` unless noted  

Live URLs were **not** changed. Site-wise behaviour is on **v5 copies only**. Old clients keep calling live paths.

Web uses the **header Site** as the global format: lists filter by it, and assign writes send that `site_id`. Header **All** = omit `site_id` (do not post a site).

---

## 1. Wrapper

All v5 auth APIs use the existing wrapper. HTTP status is on the response; it is **not** inside JSON.

```json
{
  "status": "success",
  "message": "Users fetched",
  "data": {}
}
```

| `status` | Meaning |
|----------|---------|
| `success` | Request OK |
| `error` | Validation, permission, or server error |

`data` is an object, an array, or `null`.

---

## 2. Live vs v5

| Action | Live (unchanged) | v5 |
|--------|------------------|-----|
| Create user | `POST /auth/users/` | `POST /auth/v5/users/` |
| Update user | `PATCH /auth/users/<id>/` | `PATCH /auth/v5/users/<id>/` |
| User detail | `GET /auth/users/<id>/detail/` | `GET /auth/v5/users/<id>/detail/` |
| User list | `GET /auth/users/list/` | `GET /auth/v5/users/list/` |
| Users by role | `GET /users/by-role/` | `GET /users/v5/by-role/` |
| My sites | — | `GET /auth/v5/my-sites/` |
| Delete user | `DELETE /auth/users/<id>/delete/` | same live path |
| Toggle active | `PATCH /auth/users/<id>/toggle-active/` | same live path |
| Login | `POST /auth/login/` | **No v5.** Live login stays. After login, call `GET /auth/v5/my-sites/` |
| Refresh token | `POST /auth/refresh-token/` | **No v5.** Live refresh stays. After refresh, call `GET /auth/v5/my-sites/` if sites must be reloaded |
| Select / switch site | — | `POST /auth/v5/my-sites/` |
| Create assignment | `POST /scheduler/assignments/` | `POST /scheduler/v5/assignments/` |
| Bulk assign | `POST /scheduler/assignments/bulk-create/` | `POST /scheduler/v5/assignments/bulk-create/` |
| Update assignment | `PATCH/PUT /scheduler/assignments/<id>/` | `PATCH/PUT /scheduler/v5/assignments/<id>/` |
| List assignments | `GET /scheduler/assignments/` | `GET /scheduler/v5/assignments/` |
| Assignments by guard | `GET /scheduler/assignments/by-guard/<id>/` | `GET /scheduler/v5/assignments/by-guard/<id>/` |
| Create assignment (attendance) | `POST /dashboard/attendance/create_assignment/` | `POST /dashboard/attendance/create_assignment_v5/` |
| Today’s shift (mobile) | `GET /dashboard/attendance/shift_today_v3/` | `GET /dashboard/attendance/shift_today_v5/` |
| Check-in (mobile) | `POST /dashboard/attendance/checkin_v4/` | `POST /dashboard/attendance/checkin_v5/` — see [ATTENDANCE_PUNCH_V5_API.md](./ATTENDANCE_PUNCH_V5_API.md) |
| Check-out (mobile) | `POST /dashboard/attendance/checkout_v4/` | `POST /dashboard/attendance/checkout_v5/` — see [ATTENDANCE_PUNCH_V5_API.md](./ATTENDANCE_PUNCH_V5_API.md) |
| List default shifts (mobile) | `GET /dashboard/attendance/list_default_shifts/` | same live path |
| Attach checkpoint template (mobile) | `POST /dashboard/attendance/assign-checkpoint-template/` | same live path |
| Report incident | `POST /incident/report/` | `POST /incident/v5/report/` — **`site_id` required** |
| Incident list | `GET /incident/dashboard/filter/` | `GET /incident/v5/dashboard/filter/` |
| Incident Excel | `GET /incident/dashboard/export-excel/` | `GET /incident/v5/dashboard/export-excel/` |
| Incident PDF | `GET /incident/dashboard/export-pdf/` | `GET /incident/v5/dashboard/export-pdf/` |
| My tickets Excel | `GET /incident/mytickets/export-excel/` | `GET /incident/v5/mytickets/export-excel/` |
| Assign incident | `POST /incident/assign/<ticket>/` | `POST /incident/v5/assign/<ticket>/` |
| Resolve incident | `POST /incident/resolve/<ticket>/` | `POST /incident/v5/resolve/<ticket>/` |
| Start roll call | `POST /rollcall/sessions/start/` | `POST /rollcall/v5/sessions/start/` — **`site_id` required** |
| List roll call | `GET /rollcall/sessions/` | `GET /rollcall/v5/sessions/` |
| End roll call | `POST /rollcall/sessions/<id>/end/` | `POST /rollcall/v5/sessions/<id>/end/` — **`site_id` required** |
| Roll call Excel | `GET /rollcall/sessions/export/` | `GET /rollcall/v5/sessions/export/` |
| Roll call PDF | `GET /rollcall/sessions/export-pdf/` | `GET /rollcall/v5/sessions/export-pdf/` |
| Roll call dashboard filter | `GET /rollcall/dashboard/filter/` | `GET /rollcall/v5/dashboard/filter/` |
| Walk-in check-in | `POST /visitors/entries/checkin/` | `POST /visitors/v5/entries/checkin/` — **`site_id` required** |
| Invite create | `POST /visitors/entries/invite/` | `POST /visitors/v5/entries/invite/` — **`site_id` required** |
| Complete invite | `POST /visitors/entries/<id>/complete-invite/` | `POST /visitors/v5/entries/<id>/complete-invite/` |
| Visitor list | `GET /visitors/entries/` | `GET /visitors/v5/entries/` |
| Visitor detail | `GET /visitors/entries/<id>/` | `GET /visitors/v5/entries/<id>/` |
| Visitor Excel / PDF | `GET /visitors/entries/export/` … | `GET /visitors/v5/entries/export/` … |
| Visitor search | `GET /visitors/search/` | `GET /visitors/v5/search/` |
| QR scan | `POST /visitors/qr-scan/` | `POST /visitors/v5/qr-scan/` |
| Approve / revert / cancel / reschedule / checkout / pass | live `/visitors/entries/<id>/…` | `/visitors/v5/entries/<id>/…` |
| Vehicle movement | `GET /visitors/reports/vehicle-movement/` | `GET /visitors/v5/reports/vehicle-movement/` |

---

## 3. Site assignment fields

Used on user create / update / detail / list.

| Field | Type | In | Out | Meaning |
|-------|------|----|-----|---------|
| `site_ids` | UUID array | write | yes | Sites this user may use. Empty when `all_org_sites` is true. |
| `all_org_sites` | boolean | write | yes | `true` = every site in the user’s organisation. `UserSite` rows are cleared. |
| `assigned_sites` | `[{ "id", "name" }]` | — | yes | Resolved list for UI. If `all_org_sites` is true, this is every active site in the org. |

**Write rules**

- Sites must belong to the user’s organisation (`locationId`).
- Unknown / inactive site UUID → 400 `site_ids`.
- `all_org_sites: true` → ignore / clear `site_ids`.
- Create: if `all_org_sites` is omitted and `role` is `admin`, it defaults to `true`. Otherwise it defaults to `false`.
- Update: omit both fields to leave assignment unchanged. Send `site_ids` and/or `all_org_sites` to change it.

**How a caller is allowed to use a site**

| Caller | Allowed sites |
|--------|----------------|
| Super Admin | Any org’s sites (pass `location_id` on my-sites / list) |
| Admin | All sites in **own org** |
| Other + `all_org_sites` | All sites in own org |
| Other + `UserSite` rows | Those sites only |

---

## 4. `POST /auth/v5/users/`

Create a user with site assignment.

**Auth:** same as live create (`AllowAny` inherited; typically used while logged in from web).  
**Content-Type:** `multipart/form-data` or `application/json`

### Body

Same as live create, plus site fields.

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `email` | string | yes | Unique |
| `password` | string | yes | Min 6 on web; sent as form field |
| `name` | string | yes | |
| `phone_no` | string | yes | |
| `aadhar_no` | string | yes | |
| `employee_code` | string | yes | Unique per org |
| `role` | string | yes | e.g. `guard`, `fo`, `so`, `admin` |
| `locationId` | UUID | yes | Organisation (`Location`) |
| `timezone` | string | no | Used for `admin` |
| `face_photo` | file | no | |
| `remove_face_photo` | boolean | no | Create: unused |
| `all_org_sites` | boolean | no | Default: `true` if role is `admin`, else `false` |
| `site_ids` | UUID[] | no | Repeat field, JSON array string, or comma-separated |

**Multipart examples for sites**

```
site_ids=<uuid>
site_ids=<uuid>
all_org_sites=false
```

or

```
site_ids=["<uuid>","<uuid>"]
all_org_sites=false
```

or all org sites:

```
all_org_sites=true
```

### Success — `201`

```json
{
  "status": "success",
  "message": "User created successfully",
  "data": {
    "id": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
    "email": "guard1@example.com",
    "role": "guard",
    "is_active": true,
    "created_on": "2026-08-18T10:00:00Z",
    "modified_on": "2026-08-18T10:00:00Z",
    "name": "Guard One",
    "phone_no": "9876543210",
    "aadhar_no": "123412341234",
    "employee_code": "G001",
    "timezone": "Asia/Kolkata",
    "face_photo": null,
    "location": {
      "id": "11111111-2222-3333-4444-555555555555",
      "name": "Test",
      "address": "...",
      "latitude": "0.0",
      "longitude": "0.0",
      "is_qr_scan_enable": true,
      "is_face_attendance_enabled": false,
      "is_ai_extraction_enabled": false,
      "created_on": "...",
      "modified_on": "...",
      "is_deleted": false,
      "sites": [
        { "id": "...", "name": "Babu Home", "latitude": "...", "longitude": "..." }
      ]
    },
    "all_org_sites": false,
    "site_ids": ["66666666-7777-8888-9999-000000000000"],
    "assigned_sites": [
      { "id": "66666666-7777-8888-9999-000000000000", "name": "Babu Home" }
    ]
  }
}
```

`password` is never returned.

### Errors

| HTTP | When |
|------|------|
| 400 | Validation (`email`, `site_ids`, duplicate employee code, etc.). `data` is the field error map. |
| 500 | Unexpected server error |

---

## 5. `PATCH /auth/v5/users/<uuid:id>/`

Update a user. Partial update. Same body fields as create (all optional).

**Auth:** JWT  
**Content-Type:** `multipart/form-data` or `application/json`

### Path

| Param | Type |
|-------|------|
| `id` | User UUID |

### Body (site part)

| Field | Effect |
|-------|--------|
| omit `site_ids` and `all_org_sites` | Assignment unchanged |
| `all_org_sites=true` | All org sites; `UserSite` rows deleted |
| `all_org_sites=false` + `site_ids=[...]` | Replace assignment with those sites |
| `site_ids=[]` and `all_org_sites=false` | No site access |

Also accepts live fields: `name`, `email`, `phone_no`, `role`, `locationId`, `timezone`, `face_photo`, `remove_face_photo`, etc.

### Success — `200`

Same `data` shape as create.

```json
{
  "status": "success",
  "message": "User updated successfully",
  "data": { }
}
```

### Errors

| HTTP | When |
|------|------|
| 400 | Validation |
| 404 | User not found |
| 500 | Server error |

---

## 6. `GET /auth/v5/users/<uuid:id>/detail/`

**Auth:** JWT

### Path

| Param | Type |
|-------|------|
| `id` | User UUID |

### Query

None.

### Success — `200`

```json
{
  "status": "success",
  "message": "User details fetched",
  "data": {
    "id": "...",
    "email": "...",
    "role": "guard",
    "is_active": true,
    "name": "...",
    "phone_no": "...",
    "aadhar_no": "...",
    "employee_code": "...",
    "timezone": "Asia/Kolkata",
    "face_photo": "https://.../media/...",
    "location": { },
    "all_org_sites": false,
    "site_ids": ["..."],
    "assigned_sites": [{ "id": "...", "name": "Babu Home" }]
  }
}
```

If `all_org_sites` is `true`: `site_ids` is `[]` and `assigned_sites` lists every active site in that org.

### Errors

| HTTP | When |
|------|------|
| 404 | User not found / deleted |
| 401 | Missing or invalid token |

---

## 7. `GET /auth/v5/users/list/`

**Auth:** JWT

### Query

| Param | Type | Required | Notes |
|-------|------|----------|--------|
| `search` | string | no | Matches `name`, `email`, `employee_code` |
| `role` | string | no | Case-insensitive. Omit or `all` = no role filter |
| `location_id` | UUID | no | Organisation filter |
| `site_id` | UUID | no | Users who may use this site |
| `user_id` / `id` / `guard` | UUID | no | Single user (same as live list) |

Web header **All** = omit `site_id`.  
Web header a specific site = pass that UUID as `site_id`.

### Who is returned

Inherited from live list, then site rules:

1. Not deleted.
2. Non–Super Admin callers never see `role=admin`.
3. **Super Admin:** all orgs, unless `location_id` is sent.
4. **Admin:** own org only.
5. **Others:** own org only.

Then site:

| `site_id` | Super Admin / Admin | Other roles |
|-----------|---------------------|-------------|
| **Set** | Users assigned to that site **or** `all_org_sites` in that site’s org. Caller must be allowed to use the site. | Same, but 403 if the site is not assigned to the caller |
| **Omitted (All)** | Super Admin: all matching users (optionally one org). Admin: all users in own org | Users on **the caller’s assigned sites**, plus org users with `all_org_sites` |

It does **not** mean every site in the organisation for a 1-site user. For that user, All = their assigned sites only (plus people marked All sites, because they belong to those sites too).

Wrong org site for a non–superuser → **403**. Unknown site → **400**.

### Success — `200`

`data` is an array.

```json
{
  "status": "success",
  "message": "Users fetched",
  "data": [
    {
      "id": "...",
      "email": "ravi@example.com",
      "role": "so",
      "is_active": true,
      "created_on": "...",
      "modified_on": "...",
      "name": "Ravi",
      "phone_no": "...",
      "aadhar_no": "...",
      "employee_code": "...",
      "timezone": "Asia/Kolkata",
      "location": { "id": "...", "name": "Test", "sites": [] },
      "all_org_sites": true,
      "site_ids": [],
      "assigned_sites": [
        { "id": "...", "name": "Babu Home" },
        { "id": "...", "name": "Main Boundary" }
      ]
    }
  ]
}
```

List does **not** include `face_photo` (same as live).

### Errors

| HTTP | When |
|------|------|
| 400 | Invalid `site_id` |
| 403 | Caller cannot use that site |
| 401 | Not authenticated |

---

## 8. `GET /users/v5/by-role/`

Users filtered by role name(s), with the same site rules as list. Live `GET /users/by-role/` is unchanged.

**Auth:** JWT

### Query

| Param | Type | Required | Notes |
|-------|------|----------|--------|
| `roles` | string, repeatable | yes | e.g. `roles=so&roles=fo` |
| `location_id` | UUID | no | |
| `site_id` | UUID | no | Same meaning as user list |

### Success — `200`

`data` is a smaller object than full user list.

```json
{
  "status": "success",
  "message": "Users fetched",
  "data": [
    {
      "id": "...",
      "email": "...",
      "name": "...",
      "role": "so",
      "phone_no": "...",
      "employee_code": "...",
      "all_org_sites": false,
      "site_ids": ["..."]
    }
  ]
}
```

No `assigned_sites` on this endpoint.

### Errors

| HTTP | When |
|------|------|
| 400 | Missing `roles`, or invalid `site_id` |
| 403 | Caller cannot use `site_id` |

---

## 9. `GET /auth/v5/my-sites/`

Sites the **logged-in user** may pick (web header and mobile).

**Auth:** JWT  

**Locked decision:** do **not** add `/auth/v5/login/` or `/auth/v5/refresh-token/`. Web and mobile keep live login/refresh. After a successful login (and whenever the site list must refresh), both clients call this GET.

**POST** selects / switches the posted site for that day (see §9.1). Web header Site is view context; this POST is for the logged-in user’s own posted site (mobile, or web if you persist it).

### Client flow

```
POST /auth/login/                  (live — unchanged)
        ↓ JWT
GET  /auth/v5/my-sites/            sites[], assigned_site_*, last_selected_site_*, site-setting defaults
        ↓
Web: fill header Location + Site
Mobile: fill site picker / default site
```

### Query

| Param | Type | Required | Notes |
|-------|------|----------|--------|
| `location_id` | UUID | Super Admin: yes to get sites. Others: optional | Super Admin: sites of that org. Others: must be their own org or omitted |
| `date` | `YYYY-MM-DD` | no | Default: caller’s today. Used to resolve `assigned_site_*` and `last_selected_site_*` |

### What `sites` contains

| Caller | Result |
|--------|--------|
| Super Admin + `location_id` | All active sites of that org |
| Super Admin, no `location_id` | `[]` |
| Admin | All active sites in own org |
| Other + `all_org_sites` | All active sites in own org |
| Other + specific `UserSite` | Those sites only |

`all_org_sites` in this response is true if the caller is Super Admin, org Admin, or the user flag is true. It describes **the caller**, not people in the user list.

### Success — `200`

```json
{
  "status": "success",
  "message": "Sites fetched",
  "data": {
    "sites": [
      { "id": "66666666-7777-8888-9999-000000000000", "name": "Babu Home" }
    ],
    "all_org_sites": false,
    "assigned_site_id": "66666666-7777-8888-9999-000000000000",
    "assigned_site_name": "Babu Home",
    "last_selected_site_id": "66666666-7777-8888-9999-000000000000",
    "last_selected_site_name": "Babu Home",
    "is_qr_scan_enabled": true,
    "is_host_approve_enabled": true,
    "visitor_default_purpose_of_visit": "",
    "visitor_default_remarks": "",
    "visitor_expected_out_hours": ""
  }
}
```

`assigned_site_id` / `assigned_site_name` is the **roster** (`AssignmentDailySite` only).  
`last_selected_site_id` / `last_selected_site_name` is cache if present, else the roster (same meaning as `shift_today_v5`). Either id/name pair is `null` when that source has no row for the date.

GET and POST return the same `data` shape. There is no `assigned_site` or `current_site` object.

| Field | Meaning |
|-------|---------|
| `sites` | Access list for the dropdown |
| `all_org_sites` | Caller can use every org site |
| `assigned_site_id` / `assigned_site_name` | Posted roster site for that date |
| `last_selected_site_id` / `last_selected_site_name` | Last selected site for that date (cache, else roster) |
| `is_qr_scan_enabled` | Same as live login |
| `is_host_approve_enabled` | Same as live login (SiteSetting) |
| `visitor_default_purpose_of_visit` | Same as live login |
| `visitor_default_remarks` | Same as live login |
| `visitor_expected_out_hours` | Same as live login |

### Errors

| HTTP | When |
|------|------|
| 403 | Non–superuser passed another org’s `location_id` |
| 401 | Not authenticated |

---

## 9.1 `POST /auth/v5/my-sites/`

Select or switch the **logged-in user’s** posted site for a calendar date. Does **not** write `Assignment`. Assign v5 never writes `GuardSiteCache`; only this POST does (rule 3).

**Auth:** JWT  
**Content-Type:** `application/json`

### Body

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID | yes | Must be an allowed site for the caller |
| `date` | `YYYY-MM-DD` | no | Default: caller’s today |
| `location_id` | UUID | no | Super Admin: used to fill `sites` in the response. If omitted, taken from the selected site’s org |

### Write rules

1. No `AssignmentDailySite` for this user + date → **insert** daily row (`assignment` may be null).
2. Daily row exists and attendance is **not** marked that day → **update** the daily row.
3. Daily row exists and attendance **is** marked → **insert** `GuardSiteCache` (leave the daily row unchanged).

Attendance marked = `AttendanceCheckin` for that `shift_date` with a check-in, or a `CheckInLog` type `checkin` overlapping that date.

Assign v5 does **not** use these rules. Assign always writes / updates `AssignmentDailySite` only.

### Success — `200`

Same `data` shape as GET. `message` is `Site selected`. `last_selected_site_id` / `last_selected_site_name` reflect the new choice.

### Errors

| HTTP | When |
|------|------|
| 400 | Missing `site_id`, invalid `date`, unknown / inactive site |
| 403 | Site not allowed, or site is another org (non–superuser) |
| 401 | Not authenticated |

---

## 10. Web header behaviour (not an API)

The UI adds **All** on top of `sites`. Header Site is both **list filter** and (on web) the site sent when assigning.

| Header Site | List / write |
|-------------|--------------|
| **All** | Omit `site_id`. Do not post a site on assign. |
| One site | Pass that UUID as `site_id` on lists and on assign v5 |

Example — users:

| Header Site | Call |
|-------------|------|
| **All** | `GET /auth/v5/users/list/?location_id=<org>` |
| One site | `GET /auth/v5/users/list/?location_id=<org>&site_id=<site>` |

Example — assignments:

| Header Site | Call |
|-------------|------|
| **All** | `GET /scheduler/v5/assignments/` |
| One site | `GET /scheduler/v5/assignments/?site_id=<site>` |

For a user with one assigned site, **All** and that site return the same people (assigned to that site, plus org users with `all_org_sites`).

---

## 11. Unchanged live endpoints

These still exist and were **not** given site fields:

- `POST /auth/login/`
- `POST /auth/refresh-token/`
- `POST /auth/users/`
- `GET /auth/users/list/`
- `GET /auth/users/<id>/detail/`
- `PATCH /auth/users/<id>/`
- `DELETE /auth/users/<id>/delete/`
- `PATCH /auth/users/<id>/toggle-active/`
- `GET /users/by-role/`
- `GET /scheduler/locations/<id>/sites/` — used by create/edit to fill the site multi-select
- `POST /scheduler/assignments/` and other live assignment / `create_assignment` paths

---

## 12. Shift assign v5

`site_id` is **not** stored on `Assignment`. It writes **`AssignmentDailySite`** (one row per guard per date). `GuardSiteCache` is never written by these endpoints.

Checkpoints stay optional on the backend (`checkpoints` may be `[]`). Master Checkpoint APIs are unchanged (no v5).

### Shared write field

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID or `null` | no | One site: write daily rows. `null` (header **All**): do not post a site. On **update**, `null` also **clears** existing daily rows for that assignment. Omit the field on PATCH to leave daily rows unchanged. |
| `date` | `YYYY-MM-DD` | PATCH only | With `site_id`: that day only. With `site_id: null`: clear that day only. Without `date`: every day in `start_date`…`end_date` |

Site must belong to the assignment organisation. Guard must be allowed that site. Caller must be allowed that site.

---

## 13. `POST /scheduler/v5/assignments/`

Same body as live create, plus optional `site_id`.

If `site_id` is sent, one `AssignmentDailySite` row is written for **each date** from `start_date` to `end_date`.

### Success — `201`

Live assignment JSON plus:

```json
{
  "posted_site_id": "66666666-7777-8888-9999-000000000000",
  "posted_site_name": "Babu Home"
}
```

`posted_site_id` / `posted_site_name` come from **this assignment’s** `AssignmentDailySite` rows (not “today for the guard”). Empty when header **All** did not post a site, or after daily rows were cleared.

`site_id` / `date` are write-only and are not echoed.

### Errors

Same as live (overlap, missing guard/dates), plus 400 if the site is invalid / wrong org / not assigned to the guard, 403 if the caller cannot use the site.

---

## 14. `POST /scheduler/v5/assignments/bulk-create/`

Body: JSON **array** of the same objects as create (each item may have its own `site_id`).

### Success — `201`

```json
{ "created": [ { } ] }
```

### Partial — `207`

```json
{ "created": [ { } ], "errors": [ { "index": 1, "errors": {}, "data": {} } ] }
```

---

## 15. `PATCH` / `PUT /scheduler/v5/assignments/<id>/`

Same as live update, plus:

| Body | Effect on daily rows |
|------|----------------------|
| `site_id` + `date` | That calendar day only (`date` must fall inside the assignment range) |
| `site_id` without `date` | Every day from `start_date` to `end_date` |
| `site_id: null` | Clear posted site for this assignment (all days, or `date` only if sent) |
| field omitted | Assignment fields only; daily rows unchanged |

Partial PATCH may send only `site_id` (and optional `date`).

---

## 16. `GET /scheduler/v5/assignments/`

Live list plus `posted_site_id` / `posted_site_name`.

### Query

| Param | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID | no | Header one site: only assignments whose guard has an `AssignmentDailySite` for that site on `date` (or today). Header **All**: omit |
| `date` | `YYYY-MM-DD` | no | Date used **only** for the `site_id` list filter. Default: caller’s today. `posted_site_*` on each row is that assignment’s own daily sites. |

---

## 17. `GET /scheduler/v5/assignments/by-guard/<guard_id>/`

Same extra fields and the same optional `site_id` as list.

Latest start date is first. Pagination is on the server:

| Query | Required | Meaning |
|-------|----------|---------|
| `site_id` | no | Header one site: filter posted site. Header **All**: omit |
| `limit` | no | Page size. Default `25`. Max `250` |
| `offset` | no | Skip this many rows. Default `0` |

### Success

```json
{
  "count": 40,
  "limit": 25,
  "offset": 0,
  "results": []
}
```

`count` is the full matching total (for the pager). `results` is one page.

---

## 18. `POST /dashboard/attendance/create_assignment_v5/`

**Mobile self-assign.** Copy of live `POST /dashboard/attendance/create_assignment/` when the guard picks their own shift for today.

Live body: `guard_id`, `shift_id`, `checkpoint_template_id` (optional).  
v5 adds optional `site_id`.

### Site write rules (same as `POST /auth/v5/my-sites/`)

1. Guard must be allowed that site (`UserSite` / `all_org_sites`).
2. No daily row for that date → insert `AssignmentDailySite` (linked to the new assignment).
3. Daily row exists, attendance **not** marked → update daily row.
4. Daily row exists, attendance **marked** → insert `GuardSiteCache` (daily row unchanged).

Typical flow: guard has no shift yet, so rule 1 or 2 applies. Rule 3–4 matter if a site was already posted earlier the same day.

### Success — `201`

Live payload plus when `site_id` was sent:

```json
{
  "site_id": "...",
  "site_name": "Babu Home",
  "assigned_site_id": "...",
  "assigned_site_name": "Babu Home",
  "last_selected_site_id": "...",
  "last_selected_site_name": "Babu Home"
}
```

---

## 19. `GET /dashboard/attendance/shift_today_v5/`

**Mobile today’s shift.** Same as live `shift_today_v3` plus posted-site fields for the caller’s calendar today.

**Auth:** JWT (guard token)

### Extra response fields

| Field | Source | Meaning |
|-------|--------|---------|
| `assigned_site_id` | `AssignmentDailySite` only | Roster / posted site for today (ignores cache) |
| `assigned_site_name` | same | |
| `last_selected_site_id` | `GuardSiteCache` latest, else `AssignmentDailySite` | Site the guard is working at now |
| `last_selected_site_name` | same | |

When the guard has not switched after attendance, `assigned_site_id` and `last_selected_site_id` are usually the same. After a post-attendance switch, `last_selected_site_id` comes from cache while `assigned_site_id` stays the original daily roster.

All other fields (`has_shift`, `show_checkin`, `shift_name`, etc.) are identical to `shift_today_v3`.

---

## 20. Mobile site flow (summary)

```
POST /auth/login/                              (live)
GET  /auth/v5/my-sites/                        sites[], assigned_site_*, last_selected_site_*
GET  /dashboard/attendance/shift_today_v5/     today's shift + assigned_site_* + last_selected_site_*
```

**No shift today:**

```
GET  /dashboard/attendance/list_default_shifts/?guard_id=<self>
POST /dashboard/attendance/create_assignment_v5/
     { guard_id, shift_id, checkpoint_template_id?, site_id }
```

**Switch site any time (including after attendance):**

```
POST /auth/v5/my-sites/
     { "site_id": "<uuid>", "date": "YYYY-MM-DD" }   // date optional, default today
```

Response `data.last_selected_site_id` / `last_selected_site_name` match the same fields on `shift_today_v5`.

Live `shift_today_v3` and `create_assignment` stay unchanged for old app builds.

**Attendance punch (v5):** use `checkin_v5` / `checkout_v5` with `site_id` from `last_selected_site_id`. Full request/response and error scenarios: [ATTENDANCE_PUNCH_V5_API.md](./ATTENDANCE_PUNCH_V5_API.md).

---

## 22. Incident — v5

Live `/incident/...` URLs are unchanged. Site is stored on `incidentreport.site` (nullable FK to `LocationSite`). Old tickets have `site = null`.

List/create/assign/resolve **v5 responses are the incident object (or array of objects)**, same shape as live, **plus** `site_id` and `site_name`. They do **not** use the auth `{ status, message, data }` wrapper.

Twilio WhatsApp + voice and Cloudinary upload on create are the same as live. If Twilio fails, the ticket is still created (`201`).

### Client flow

```
POST /auth/login/                         (live)
GET  /auth/v5/my-sites/                   sites[], last_selected_site_id
        ↓
Web:  header Location + Site
      GET /incident/v5/dashboard/filter/?location_id=<org>&site_id=<site|omit>
Mobile create:
      POST /incident/v5/report/            site_id required
```

Mobile should send `last_selected_site_id` from my-sites as `site_id` when reporting.

---

### 22.1 `POST /incident/v5/report/`

Create an incident at a site. Same multipart rules as live, plus **`site_id` required**.

**Auth:** JWT  
**Content-Type:** `multipart/form-data`

`location` on the row is taken from the selected site’s organisation (not from the caller’s `location` when that would be wrong, e.g. Super Admin).

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID | **yes** | Must be allowed for the caller. Inactive / unknown → 400 |
| `severity` | string | no | `High` / `Medium` / `Low`. Default `Medium` |
| `incident_description` | string | one of | Text **or** `description_audio` (or both) |
| `description_audio` | file | one of | Voice note |
| `photo` | file | no | |
| `video` | file | no | |
| `checkpoint` | UUID | no | Master checkpoint (no site on Checkpoint) |

```bash
curl -X POST "https://your-domain.com/incident/v5/report/" \
  -H "Authorization: Bearer ACCESS" \
  -F "site_id=<site-uuid>" \
  -F "severity=High" \
  -F "incident_description=Fire alarm near Gate 2" \
  -F "checkpoint=<checkpoint-uuid-optional>" \
  -F "photo=@/path/to/photo.jpg"
```

Audio-only:

```bash
curl -X POST "https://your-domain.com/incident/v5/report/" \
  -H "Authorization: Bearer ACCESS" \
  -F "site_id=<site-uuid>" \
  -F "severity=Medium" \
  -F "incident_description=" \
  -F "description_audio=@/path/to/voice_note.m4a"
```

### Success — `201`

Incident object. Extra vs live:

| Field | Meaning |
|-------|---------|
| `site_id` | UUID of posted site |
| `site_name` | Site name |
| `location` | Org UUID (from the site) |
| `location_name` | Org name |
| `ticket_number` | Generated |
| `status` | `Open` on create |
| `created_on` | Caller / org timezone ISO |

Live `POST /incident/report/` does **not** require or save `site_id`.

### Errors

| HTTP | When |
|------|------|
| 400 | Missing `site_id`; unknown / inactive site; no text and no audio |
| 403 | Caller not allowed that site |
| 401 | Not authenticated |

Missing `site_id`:

```json
{ "site_id": ["This field is required."] }
```

---

### 22.2 `GET /incident/v5/dashboard/filter/`

List incidents. Same filters as live, plus `site_id`. Used by web Incidents and My Tickets (`assigned_to=<self>`).

**Auth:** JWT

| Query | Type | Required | Notes |
|-------|------|----------|--------|
| `location_id` | UUID | Super Admin: yes (header org). Others: optional | Org filter |
| `site_id` | UUID | no | Header one site. **Omit** when header is **All** (`all` is treated as omit) |
| `date_filter` | string | no | See date table. Default: no date range if omitted / unknown |
| `start_date` | `YYYY-MM-DD` | with custom | Inclusive |
| `end_date` | `YYYY-MM-DD` | with custom | Inclusive |
| `status` | string | no | `Open` / `In-Progress` / `Closed` (web may send `open` / `inprogress` / `close`) |
| `severity` | string | no | `Low` / `Medium` / `High` |
| `assigned_to` | UUID | no | My Tickets: current user |
| `created_by` | UUID | no | |
| `checkpoint_id` | UUID | no | |

**`date_filter` (v5 only — live still ignores yesterday / last_week / last_month)**

| Value | Range (caller / org timezone) |
|-------|-------------------------------|
| `today` | Today |
| `yesterday` | Yesterday |
| `this_week` | Mon–Sun of this week |
| `last_week` | Previous Mon–Sun |
| `this_month` | 1st–last day of this month |
| `last_month` | Previous calendar month |
| `custom` | `start_date` + `end_date` required |

### Site filter (header)

| Header Site | Query | Result |
|-------------|-------|--------|
| One site | `site_id=<uuid>` | Only that site. Old tickets with `site = null` are **hidden** |
| **All** | omit `site_id` | Super Admin / Admin / `all_org_sites`: **all tickets in that org**, including `site = null`. Other roles: allowed sites **or** `site` null, same org |

```
GET /incident/v5/dashboard/filter/?date_filter=last_month&location_id=<org>
GET /incident/v5/dashboard/filter/?date_filter=today&location_id=<org>&site_id=<site>&status=Open
GET /incident/v5/dashboard/filter/?date_filter=custom&start_date=2025-01-19&end_date=2026-08-17&location_id=<org>
GET /incident/v5/dashboard/filter/?date_filter=today&assigned_to=<user-uuid>
```

### Success — `200`

JSON **array** of incident objects (`site_id`, `site_name` may be `null` on old rows).

### Errors

| HTTP | When |
|------|------|
| 400 | Invalid `site_id`, custom dates not `YYYY-MM-DD`, site not in that org |
| 403 | Site not allowed |
| 401 | Not authenticated |

---

### 22.3 Excel / PDF

Same query as list. v5 Excel/PDF add a **Site** column. PDF header **Site** is the real site name when `site_id` is present; **All** uses org name.

| Method | v5 |
|--------|-----|
| GET | `/incident/v5/dashboard/export-excel/` |
| GET | `/incident/v5/dashboard/export-pdf/` |
| GET | `/incident/v5/mytickets/export-excel/` — same filters, plus assigned to the caller |

My Tickets export also applies the header `site_id` / All rule above.

---

### 22.4 `POST /incident/v5/assign/<ticket_number>/`

Same as live. No new body field. Response includes `site_id` / `site_name`.

**Auth:** JWT  
**Content-Type:** `application/json`

| Field | Type | Required |
|-------|------|----------|
| `assigned_to` | UUID | yes |

```bash
curl -X POST "https://your-domain.com/incident/v5/assign/A1B2C3D4E5F6/" \
  -H "Authorization: Bearer ACCESS" \
  -H "Content-Type: application/json" \
  -d '{"assigned_to": "<user-uuid>"}'
```

`200` incident object (`status` → `In-Progress`). `404` if ticket unknown.

Assign user picker on web: `GET /users/v5/by-role/?roles=so&roles=fo&location_id=<org>&site_id=<site|omit>`.

---

### 22.5 `POST /incident/v5/resolve/<ticket_number>/`

Same as live. No new body field. Response includes `site_id` / `site_name`.

**Auth:** JWT  
**Content-Type:** `application/json`

| Field | Type | Required |
|-------|------|----------|
| `closure_description` | string | no |

```bash
curl -X POST "https://your-domain.com/incident/v5/resolve/A1B2C3D4E5F6/" \
  -H "Authorization: Bearer ACCESS" \
  -H "Content-Type: application/json" \
  -d '{"closure_description": "Issue closed"}'
```

`200` incident object (`status` → `Closed`). `404` if ticket unknown.

---

### 22.6 Web

- Header **Location** + **Site**. No in-page Organisation / Site dropdown.
- **All** = omit `site_id` on list and export.
- One site = pass that UUID.
- Custom range uses the same MUI date field as attendance reports (`start_date` / `end_date` as `YYYY-MM-DD`).

---

## 23. Roll call — v5

Live `/rollcall/...` URLs are unchanged. Site is stored on `RollCallSession.site` (nullable FK to `LocationSite`). Old sessions have `site = null`.

Web report uses list + Excel/PDF. Mobile uses start / list / end. **v5 responses are the session object (or array)**, same shape as live, **plus** `site_id` and `site_name`. They do **not** use the auth `{ status, message, data }` wrapper.

### Client flow

```
POST /auth/login/                         (live)
GET  /auth/v5/my-sites/                   sites[], last_selected_site_id
        ↓
Mobile:
  POST /rollcall/v5/sessions/start/       site_id + shift_id + start_photo
  GET  /rollcall/v5/sessions/?site_id=…   list open/closed for site
  POST /rollcall/v5/sessions/<id>/end/    site_id + end_photo
Web report:
  GET /rollcall/v5/sessions/?location_id=<org>&site_id=<site|omit>
  GET /rollcall/v5/sessions/export/       same filters
  GET /rollcall/v5/sessions/export-pdf/
```

Mobile should send `last_selected_site_id` (or the site the guard is working) as `site_id`.

**Site filter (list / export)** — same pattern as Incident:

| Header Site | Behaviour |
|-------------|-----------|
| One site UUID | Only sessions with that `site_id` |
| **All** (omit `site_id`) | Org scope: that location’s sessions, **including** legacy `site = null` |

---

### 23.1 `POST /rollcall/v5/sessions/start/`

Start a roll call at a site. Multipart same as live, plus **`site_id` required**.

**Auth:** JWT  
**Content-Type:** `multipart/form-data`

`location` on the row is taken from the selected site’s organisation. Shift must belong to that organisation.

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID | **yes** | Must be allowed for the caller |
| `shift_id` | UUID | **yes** | Shift in that site’s org |
| `start_photo` | file | **yes** | Also accepts field name `image` |

```bash
curl -X POST "https://your-domain.com/rollcall/v5/sessions/start/" \
  -H "Authorization: Bearer ACCESS" \
  -F "site_id=<site-uuid>" \
  -F "shift_id=<shift-uuid>" \
  -F "start_photo=@start.jpg"
```

`201` session object with `site_id` / `site_name`. `400` if `site_id` / `shift_id` / photo missing. `403` if site not allowed. `404` if shift not in site’s org.

---

### 23.2 `GET /rollcall/v5/sessions/`

List sessions. Same filters as live (`location_id`, `status`, `date_filter`, `start_date`, `end_date`, `shift_id`, …), plus `site_id`.

Also available as `GET /rollcall/v5/dashboard/filter/` (alias).

```bash
curl -G "https://your-domain.com/rollcall/v5/sessions/" \
  -H "Authorization: Bearer ACCESS" \
  -d "location_id=<org-uuid>" \
  -d "site_id=<site-uuid>" \
  -d "date_filter=today" \
  -d "status=all"
```

Omit `site_id` for header **All** (org + null-site rows).

Response: array of sessions with `site_id` / `site_name`.

---

### 23.3 `POST /rollcall/v5/sessions/<session_id>/end/`

Close an open session. Multipart same as live, plus **`site_id` required**.

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID | **yes** | Must match session’s site |
| `end_photo` | file | **yes** | Also accepts field name `image` |

**Rules**

- If session has `site`: posted `site_id` must equal `session.site_id`.
- If session has `site = null` (legacy): site must belong to `session.location`; on success the session’s `site` is set to that site.
- Session must be `open`.

```bash
curl -X POST "https://your-domain.com/rollcall/v5/sessions/<session-uuid>/end/" \
  -H "Authorization: Bearer ACCESS" \
  -F "site_id=<site-uuid>" \
  -F "end_photo=@end.jpg"
```

`200` closed session. `400` already closed / missing photo. `403` site mismatch. `404` unknown session.

---

### 23.4 Excel / PDF

| Method | Path |
|--------|------|
| GET | `/rollcall/v5/sessions/export/` |
| GET | `/rollcall/v5/sessions/export-pdf/` |

Same query params as list (`location_id`, `site_id`, date filters, `status`). Excel/PDF include a **Site** column. PDF header dept line uses the selected site name when `site_id` is set.

```bash
curl -G "https://your-domain.com/rollcall/v5/sessions/export/" \
  -H "Authorization: Bearer ACCESS" \
  -d "location_id=<org-uuid>" \
  -d "site_id=<site-uuid>" \
  -d "date_filter=this_month" \
  -o rollcall.xlsx
```

---

### 23.5 Web / mobile

- **Web (done):** `GTMS_NEw` Roll Call report uses `/rollcall/v5/sessions/` (+ export). Header Location + Site; no in-page org dropdown; **All** = omit `site_id`; table **Site** column.
- **Mobile:** start / list / end via v5 with `site_id` on every write; list with selected site (or omit only if product wants org-wide list).

---

## 24. Visitor — v5

Live `/visitors/...` URLs are unchanged. Site is stored on `VisitorEntry.site` (nullable FK to `LocationSite`). Old entries have `site = null`. The people master `Visitor` stays org-scoped (IC unique per location).

**v5 responses are the entry object (or array / QR action wrapper)**, same shape as live, **plus** `site_id` and `site_name`. They do **not** use the auth `{ status, message, data }` wrapper.

Visitor SiteSetting keys (`is_host_approve_enabled`, defaults) stay on **live login** — not per physical site.

### Client flow

```
POST /auth/login/                         (live)
GET  /auth/v5/my-sites/                   sites[], last_selected_site_id
        ↓
Mobile walk-in:
  POST /visitors/v5/entries/checkin/      site_id + IC + name + photos…
Mobile invite:
  POST /visitors/v5/entries/invite/       site_id + host_id + visit_date…
  POST /visitors/v5/entries/<id>/complete-invite/
  POST /visitors/v5/qr-scan/              { qr_token }
Host:
  GET  /visitors/v5/entries/?mine=true&site_id=…
  POST /visitors/v5/entries/<id>/approve|revert|cancel|reschedule/
Guard checkout:
  POST /visitors/v5/entries/<id>/checkout/
Web:
  GET /visitors/v5/entries/?location_id=<org>&site_id=<site|omit>
  GET /visitors/v5/entries/export/ …
  GET /visitors/v5/reports/vehicle-movement/?site_id=…
```

Mobile should send `last_selected_site_id` (or working site) as `site_id` on create.

**Site filter (list / export / vehicle)** — same pattern as Incident / Roll call:

| Header Site | Behaviour |
|-------------|-----------|
| One site UUID | Only entries with that `site_id` |
| **All** (omit `site_id`) | Org scope: that location’s entries, **including** legacy `site = null` |

---

### 24.1 `POST /visitors/v5/entries/checkin/`

Walk-in create (or resubmit after revert). Same multipart rules as live, plus **`site_id` required**.

**Auth:** JWT · **Content-Type:** `multipart/form-data`

`location` on the row is taken from the selected site’s organisation (client `location_id` is overwritten).

| Field | Type | Required | Notes |
|-------|------|----------|--------|
| `site_id` | UUID | **yes** | Must be allowed for the caller |
| `ic_passport_number` | string | **yes** | Also `ic_number` |
| `visitor_name` | string | **yes** | |
| `host_id` | UUID | if host-approve on | Same location as site’s org |
| `visitor_photo` / `id_proof` | file | **yes*** | Or prior-visit reuse |
| Other | — | no | Same as live (`purpose_of_visit`, vehicle_*, `entry_id` for resubmit, …) |

```bash
curl -X POST "https://your-domain.com/visitors/v5/entries/checkin/" \
  -H "Authorization: Bearer ACCESS" \
  -F "site_id=<site-uuid>" \
  -F "ic_passport_number=S1234567A" \
  -F "visitor_name=Jane Doe" \
  -F "host_id=<host-uuid>" \
  -F "visitor_photo=@face.jpg" \
  -F "id_proof=@ic.jpg"
```

`201` entry object with `site_id` / `site_name` (+ `returning_visitor` flag). `400` missing site / fields. `403` site not allowed.

---

### 24.2 `POST /visitors/v5/entries/invite/`

Pre-registration. **`site_id` required**. Photos optional at invite time.

| Field | Type | Required |
|-------|------|----------|
| `site_id` | UUID | **yes** |
| `ic_passport_number` | string | **yes** |
| `visitor_name` | string | **yes** |
| `host_id` | UUID | **yes** |
| `visit_date` | `YYYY-MM-DD` | **yes** |

`201` scheduled entry with `site_id` / `site_name`.

---

### 24.3 `POST /visitors/v5/entries/<id>/complete-invite/`

Same as live. If entry already has `site`, optional `site_id` must match. If legacy `site = null`, **`site_id` required** and must be in the entry’s org (then saved on the row).

---

### 24.4 `GET /visitors/v5/entries/`

Same query params as live (`location_id`, `date_filter`, `status`, `mine`, `visitor_type`, `search`, `has_vehicle`, …) plus `site_id`.

```bash
curl -G "https://your-domain.com/visitors/v5/entries/" \
  -H "Authorization: Bearer ACCESS" \
  -d "location_id=<org-uuid>" \
  -d "site_id=<site-uuid>" \
  -d "date_filter=today" \
  -d "mine=true"
```

Response: array of entries with `site_id` / `site_name`.

---

### 24.5 Detail / search / QR / lifecycle

| Method | Path | Notes |
|--------|------|-------|
| GET | `/visitors/v5/entries/<id>/` | FCM deep-link; site access (host always OK) |
| GET | `/visitors/v5/search/?ic_number=&site_id=` | Optional `site_id` → search in that site’s org |
| POST | `/visitors/v5/qr-scan/` | Body `{ "qr_token" }`; `entry` includes `site_id` / `site_name` |
| POST | `/visitors/v5/entries/<id>/approve/` | Same as live; response has site fields |
| POST | `/visitors/v5/entries/<id>/revert/` | |
| POST | `/visitors/v5/entries/<id>/cancel/` | |
| POST | `/visitors/v5/entries/<id>/reschedule/` | Keeps existing `site` |
| POST | `/visitors/v5/entries/<id>/checkout/` | multipart `exit_photo` |
| GET | `/visitors/v5/entries/<id>/pass/` | Pass PNG |

---

### 24.6 Excel / PDF / vehicle movement

| Method | Path |
|--------|------|
| GET | `/visitors/v5/entries/export/` |
| GET | `/visitors/v5/entries/export-pdf/` |
| GET | `/visitors/v5/reports/vehicle-movement/` |
| GET | `/visitors/v5/reports/vehicle-movement/export/` |
| GET | `/visitors/v5/reports/vehicle-movement/export-pdf/` |

Same filters as list (+ vehicle report date/time params). Entry exports add a **Site** column. Vehicle report JSON includes `site_id` / `site_name` when filtered.

---

### 24.7 Web / mobile

- **Web (done):** History / Manual entry / Vehicle movement use `/visitors/v5/...`. Header Location + Site; no in-page org dropdown; **All** = omit `site_id` on list/export; registration requires a concrete site (not All); table **Site** column.
- **Mobile:** all create/list/lifecycle paths above; send `site_id` on checkin & invite.

AI OCR stays on live: `POST /visitors/ai/extract-v2/` (no site).

---

## 21. Not built yet

Login and refresh-token **v5 will not be built**. Keep `POST /auth/login/` and `POST /auth/refresh-token/`.

| API | Plan |
|-----|------|
| `scan_v5` | Later (checkpoint scan) |
| Payslip / dashboard list **v5** | Later modules |
