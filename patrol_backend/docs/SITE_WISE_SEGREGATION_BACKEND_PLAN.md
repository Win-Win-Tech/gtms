# Site-wise Segregation — Backend Plan

**Status:** Planning  
**Date:** 14 Aug 2026  

Backend only. Data model + APIs. No UI. No implementation code.

---

## Read this first

A user belongs to **one organisation** (`Location`). Under that org there are **sites** (`LocationSite`).

Site is used in **four different ways**. Do not mix them.

| What | Table | Meaning |
|------|--------|---------|
| **Who may use which sites** | `UserSite` + flag `all_org_sites` | Access for lists, reports, login |
| **Where the guard is posted that day** | **`AssignmentDailySite`** (new) | Roster: one row per guard per date. Written on **assign**, and on **select/switch before attendance** |
| **Site switches after attendance** | **`GuardSiteCache`** (new, temp/cache) | Every switch that day **after** attendance is marked. Return **latest** |
| **Where an event happened** | Scan log (`CheckIn`), attendance (`CheckInLog`), incident, visitor | Stored **when the event is marked**, not on master Checkpoint / Assignment |

**No `site_id` on:** `Assignment`, `Shift`, or master **`Checkpoint`**.

Site is saved on:

1. **`AssignmentDailySite`** — on shift assign; also when the guard selects a site and there is no daily row, or switches **before** attendance
2. **`GuardSiteCache`** — only when the guard **switches site after attendance** is already marked that day
3. **`CheckIn`** — when the user marks a checkpoint as scanned
4. **`CheckInLog`** (already has `site`) — when the user marks attendance check-in / check-out

Plus incident / visitor rows on create.

**Return `current_site` in this order (always latest cache first):**

1. **`GuardSiteCache`** — latest row for that guard + date (`created_on` DESC). Multiple rows that day are allowed.
2. Else **`AssignmentDailySite`** for that guard + date.
3. Else `null`.

---

## Example: assign a full month, then change one day

**Step 1 — Assign Guard X for August, shift Morning, site = Site A**

| Table | What is saved |
|--------|----------------|
| `Assignment` | **1 row**: Guard X, Morning shift, 1 Aug → 31 Aug. **No site.** |
| `AssignmentDailySite` | **31 rows**: 1 Aug Site A, 2 Aug Site A, … 31 Aug Site A |

**Step 2 — Change 15 Aug to Site B**

| Table | What happens |
|--------|----------------|
| `Assignment` | **Unchanged** (still 1 Aug → 31 Aug, Morning) |
| `AssignmentDailySite` | **Only 15 Aug** updated to Site B. Other 30 days stay Site A |

That is the roster design.

- Create / bulk assign with `site_id` → fill daily rows for every date in the range.
- PATCH with `site_id` **and** `date` → update **that one day**.
- PATCH with `site_id` **without** `date` → update **all days** in that assignment range.

## Example: guard selects / switches site that day

`POST /auth/v5/my-sites/` with `site_id`. Check `AssignmentDailySite` for that guard + date, then whether attendance is marked.

| Situation | Where to store |
|-----------|----------------|
| **No** `AssignmentDailySite` row | **Insert** `AssignmentDailySite` with the selected site |
| Daily row **exists**, attendance **not** marked | **Update** that `AssignmentDailySite` row (switch stays on roster) |
| Daily row **exists**, attendance **already** marked | **Insert** `GuardSiteCache` (every switch that day). Do **not** change `AssignmentDailySite` |

**Return `current_site`:** latest `GuardSiteCache` → else `AssignmentDailySite` → else `null`.

Example — Guard X, 15 Aug, assigned Site B:

| What happens | `AssignmentDailySite` | `GuardSiteCache` | `current_site` |
|--------------|----------------------|------------------|----------------|
| Assigned Site B | Site B | (empty) | **B** |
| Switches to C, **no** attendance yet | **updated to C** | (empty) | **C** |
| Marks attendance (still at C) | C (unchanged) | (empty) | **C** |
| Switches to D **after** attendance | C (unchanged) | new row D | **D** (latest cache) |
| Switches to E later | C (unchanged) | another row E | **E** (latest) |

---

## 1. Access rules (server must enforce)

| Role | Org | Sites they may use |
|------|-----|---------------------|
| Super Admin | All orgs | Sites of the org in the request |
| Admin | Own org only | All sites in that org |
| Other roles | Own org only | Only sites in `UserSite` (or all org sites if `all_org_sites` is true) |

Every `site_id` in body or query: site must belong to the org **and** the caller must be allowed that site. Else 400/403.

---

## 2. Data model

### 2.1 Access — who may use which sites

- **`UserSite`**: `user` + `site`. Unique `(user, site)`.
- **`User.all_org_sites`**: if true, user can use every site in their org (no need to insert every row).

User create/update body: `site_ids[]` and/or `all_org_sites`.

### 2.2 Posted site per day — `AssignmentDailySite` (new)

| Field | Meaning |
|--------|--------|
| `guard` | User |
| `date` | Calendar / shift date |
| `site` | Posted site that day |
| `location` | Org |
| `assignment` | Optional FK to the long Assignment |

**Unique:** one posted site per guard per date (or per guard + date + assignment if two shifts that day).

Written from:

- **Assign-shift APIs** when `site_id` is sent (one row per date in the range)
- **Select-site API** (`POST /auth/v5/my-sites/`): insert if no row; **update** if a row exists and attendance is **not** marked yet

After attendance is marked, this row is **not** updated by site switches (those go to `GuardSiteCache`).

### 2.3 Site cache after attendance — `GuardSiteCache` (new)

Used only **after** the guard has marked attendance that day. Each later site switch is a new cache row. Roster (`AssignmentDailySite`) stays as it was at punch time.

| Field | Meaning |
|--------|--------|
| `guard` | User |
| `date` | Calendar / shift date |
| `site` | Site they switched to |
| `location` | Org |
| `created_on` | When this switch was stored — used to pick **latest** |

**Not unique** on `(guard, date)`. Several switches the same day are OK. **Return always the latest row** (`created_on` DESC, then id DESC).

**`POST /auth/v5/my-sites/` write rules**

| That day | Action |
|----------|--------|
| No `AssignmentDailySite` for this guard | **Insert** `AssignmentDailySite` |
| `AssignmentDailySite` exists, **no** attendance yet | **Update** `AssignmentDailySite.site` |
| `AssignmentDailySite` exists, attendance **already** marked | **Insert** `GuardSiteCache` (do not touch daily row) |

Do **not** write cache from shift-assign. Assign only writes `AssignmentDailySite`.
Do **not** write cache from the attendance punch itself. Punch saves `CheckInLog` only. Cache starts on the **next switch** after punch.

**When to read (`current_site`)**

Same helper for `GET /auth/v5/my-sites/` and `shift_today_v5`:

1. Latest `GuardSiteCache` for guard + date
2. Else `AssignmentDailySite` for guard + date
3. Else `null`

If the latest cache site is no longer allowed for that user → skip it and fall through to daily roster, then `null`.

### 2.4 Where `site` is stored (and where it is not)

| Table | Change |
|--------|--------|
| `Assignment` | **No `site`** |
| `Shift` | **No `site`** |
| `Checkpoint` (master) | **No `site`** — create / assign checkpoint unchanged |
| **`AssignmentDailySite`** (new) | Posted site per guard per day (roster) |
| **`GuardSiteCache`** (new) | Switches **after** attendance only; many rows OK; return latest |
| **`CheckIn`** (checkpoint scan log) | **Add `site`** — set when user marks checkpoint scanned |
| **`CheckInLog`** / `AttendanceCheckin` | Already have `site` — set when user marks attendance |
| `incidentreport` | Add `site` on create |
| `VisitorEntry` | Add `site` on create |

Nullable first so old rows still work.

Payslip: no new column. Filter employees using `UserSite` for the selected site.

---

## 3. How `site_id` is chosen on create

| API | If `site_id` is sent | If `site_id` is not sent |
|-----|----------------------|---------------------------|
| Assign shift (with site) | Write **`AssignmentDailySite`** rows only | Assignment created; **no** daily site rows |
| Create / update / assign checkpoint | **Ignored — no site on Checkpoint** | Unchanged |
| Checkpoint scan | Save on **`CheckIn.site`** | Use that day’s `AssignmentDailySite` (if none → 400) |
| Attendance check-in / check-out | Save on **`CheckInLog.site`** (and attendance) | Existing geofence: `get_site_within_proximity(lat, lon, location_id)` |
| Visitor create | **Required** on `VisitorEntry` | 400 |
| Incident create | **Required** on `incidentreport` | 400 |

Illegal `site_id` → 400/403. Do not invent a site if nothing matches.

---

## 4. APIs — **v5 copies only** (live URLs stay)

Live and development share the same server. **Do not change existing endpoints.** Copy the live API and register a **v5** path. Old clients keep calling the live URL. New site-wise clients call **v5** only.

**How to name v5**

| Live style | v5 style |
|------------|----------|
| Already versioned (`_v3`, `_v4`, `/v2/`) | New sibling `_v5` or `/v5/` |
| Never versioned | Add `/v5/` after the app prefix, or `_v5` on the action |
| Brand new (my-sites) | `/auth/v5/my-sites/` only — no live copy |

Live login, list, punch, visitor, incident, assign — **unchanged**.

Shared helper (v5 only): allowed site IDs for `request.user`. Wrapper stays `{ status, message, data }` where it already exists.

Module order for this section: **Users → Foundation → Shift assign → Incident → Visitor → Payslip → Dashboard (last)**.

### 4.1 Users (first)

| Method | Live (do not change) | v5 |
|--------|----------------------|-----|
| POST | `/auth/users/` | `/auth/v5/users/` — body `site_ids[]`, `all_org_sites` |
| PATCH | `/auth/users/<uuid:id>/` | `/auth/v5/users/<uuid:id>/` — same |
| GET | `/auth/users/<uuid:id>/detail/` | `/auth/v5/users/<uuid:id>/detail/` — return `site_ids`, `all_org_sites`, `assigned_sites` |
| GET | `/auth/users/list/` | `/auth/v5/users/list/` — query `site_id`. Super Admin: all orgs. Admin: own org. Others: users of that site only |
| GET | `/users/by-role/` | `/users/v5/by-role/` — query `site_id` (same rules) |

---

### 4.2 Foundation (auth / current site)

| Method | Live (do not change) | v5 |
|--------|----------------------|-----|
| POST | `/auth/login/` | `/auth/v5/login/` — `data` adds `assigned_sites: [{ id, name }]`, `all_org_sites`. Visitor keys stay as today. **No** `current_site` |
| POST | `/auth/refresh-token/` | `/auth/v5/refresh-token/` — same as login v5 |
| GET | — | `/auth/v5/my-sites/` — **new** |
| POST | — | `/auth/v5/my-sites/` — **new** (select / switch) |

**GET `/auth/v5/my-sites/`** (JWT)

- Query: `date` optional (`YYYY-MM-DD`). Default = today (user timezone).
- `data.assigned_sites` — from `UserSite` / `all_org_sites`
- `data.all_org_sites`
- `data.current_site` — **latest `GuardSiteCache`** for this user + date, else `AssignmentDailySite`, else `null` → `{ id, name, date }` or `null`
- Visitor SiteSetting (org, same as login): `is_host_approve_enabled`, `visitor_default_purpose_of_visit`, `visitor_default_remarks`

If latest cache site is not allowed, skip it and use daily roster, then `null`.

**POST `/auth/v5/my-sites/`** (JWT) — select / switch site for that day

- Body: `site_id` required. `date` optional (default today).
- Validate allowed site.
- Then:
  1. No `AssignmentDailySite` for this user + date → **insert** daily row
  2. Daily row exists, **no** attendance that day → **update** daily row
  3. Daily row exists, attendance **already** marked → **insert** `GuardSiteCache` (leave daily row)
- Return same `data` shape as GET.

**GET `/scheduler/locations/<id>/sites/`** — already exists. Keep live. Optional v5: `/scheduler/v5/locations/<id>/sites/` filtered to allowed sites for non-admin.

---

### 4.3 Assign shift (v5)

`site_id` is **not** saved on Assignment. It writes **`AssignmentDailySite`**.

| Method | Live (do not change) | v5 |
|--------|----------------------|-----|
| POST | `/scheduler/assignments/` | `/scheduler/v5/assignments/` — if `site_id` sent → one daily row **per date** from `start_date` to `end_date` |
| POST | `/scheduler/assignments/bulk-create/` | `/scheduler/v5/assignments/bulk-create/` — same per item |
| PATCH/PUT | `/scheduler/assignments/<id>/` | `/scheduler/v5/assignments/<id>/` — `site_id` + **`date`** → that day only; no `date` → all days in range |
| POST | `/dashboard/attendance/create_assignment/` | `/dashboard/attendance/create_assignment_v5/` — today-only; one daily row if `site_id` sent |
| GET | `/scheduler/assignments/` | `/scheduler/v5/assignments/` — may include today’s `site_id` / `site_name` from daily table |
| GET | `/scheduler/assignments/by-guard/<guard_id>/` | `/scheduler/v5/assignments/by-guard/<guard_id>/` — same |

If assign v5 is sent **without** `site_id`: Assignment is created, daily table stays empty until a later PATCH v5 with `site_id`.

---

### 4.4 Checkpoint create / assign — **no v5** (no `site_id`)

Master Checkpoint stays as today. **Do not** add `site` on Checkpoint. Live checkpoint APIs unchanged. No v5 copy needed.

| Method | Path | Change |
|--------|------|--------|
| POST | `/scheduler/checkpoints/` | **No change** |
| PATCH/PUT | `/scheduler/checkpoints/<id>/` | **No change** |
| GET | `/scheduler/checkpoints/` | **No change** |
| GET | `/scheduler/checkpoints/by-location/<location_id>/` | **No change** |
| POST | `/dashboard/attendance/assign-checkpoint-template/` | **No change** |

Posted site comes from **assign v5** and from **select/switch before attendance**. After attendance, switches go to `GuardSiteCache`.

---

### 4.5 Today’s shift — `shift_today_v5`

Live `shift_today` / `_v2` / `_v3` **unchanged**.

**GET `/dashboard/attendance/shift_today_v5/`** — copy of v3 plus:

If `has_shift` is true: same helper as my-sites.

- Latest `GuardSiteCache` → `site_id`, `site_name`
- Else `AssignmentDailySite` → `site_id`, `site_name`
- Else `site_id`: null, `site_name`: null
- Rest of payload same as v3

**GET `/scheduler/v5/assignments/upcoming-checkpoints/<user_id>/`** — copy of live; optional current `site_id` (cache then daily). Live upcoming-checkpoints unchanged.

---

### 4.6 Checkpoint scan — `scan_v5`

Live `/checkin/checkins/`, `scan_v2`, `submit_checklist_v2` **unchanged**.

| Method | v5 |
|--------|-----|
| POST | `/checkin/checkins/scan_v5/` |
| POST | `/checkin/checkins/submit_checklist_v5/` |

**Pick site:** request `site_id` → else latest `GuardSiteCache` → else that day’s `AssignmentDailySite`. Save on **`CheckIn.site`**. If none → 400.

---

### 4.7 Attendance punch — `checkin_v5` / `checkout_v5`

Live `checkin_v4` / `checkout_v4` / v3 **unchanged**.

**Pick site:** request `site_id` → else `get_site_within_proximity`. If still none → 400. Save on **`CheckInLog.site`** and `AttendanceCheckin.site`. Do **not** write `GuardSiteCache` on punch. Later switches: `POST /auth/v5/my-sites/` → cache.

| Method | Live (do not change) | v5 |
|--------|----------------------|-----|
| POST | `/dashboard/attendance/checkin_v4/` | `/dashboard/attendance/checkin_v5/` |
| POST | `/dashboard/attendance/checkout_v4/` | `/dashboard/attendance/checkout_v5/` |
| POST | `/dashboard/attendance/face_attendance/` | `/dashboard/attendance/face_attendance_v5/` |
| POST | `/dashboard/attendance/force-checkout_v3/` | `/dashboard/attendance/force-checkout_v5/` |
| POST | `/dashboard/attendance/add-punch_v3/` | `/dashboard/attendance/add-punch_v5/` |
| POST | `/dashboard/attendance/edit-punch_v3/` | `/dashboard/attendance/edit-punch_v5/` |
| POST | `/dashboard/attendance/edit-boundary_v3/` | `/dashboard/attendance/edit-boundary_v5/` |
| POST | `/dashboard/attendance/bulk-entry/` | `/dashboard/attendance/bulk-entry_v5/` |
| POST | `/dashboard/attendance/bulk-weekoff/` | `/dashboard/attendance/bulk-weekoff_v5/` |
| POST | `/dashboard/attendance/monthly-cell-action/` | `/dashboard/attendance/monthly-cell-action_v5/` |

---

### 4.8 Dashboard lists / Excel / PDF — v5

Filter to that site. Enforce allowed. PDF header **Site** = real site name. Live list/export URLs **unchanged**.

| Live | v5 |
|------|-----|
| `/dashboard/api/attendance_v4/` | `/dashboard/api/attendance_v5/` |
| `/dashboard/api/attendance/export_v4/` | `/dashboard/api/attendance/export_v5/` |
| `/dashboard/api/attendance/export_v4_pdf/` | `/dashboard/api/attendance/export_v5_pdf/` |
| `/dashboard/dashboard-checkin-report/v2/` | `/dashboard/dashboard-checkin-report/v5/` |
| `/dashboard/dashboard-checkin-report-excel/v2/` | `/dashboard/dashboard-checkin-report-excel/v5/` |
| `/dashboard/dashboard-checkin-report-pdf/v2/` | `/dashboard/dashboard-checkin-report-pdf/v5/` |
| `/dashboard/attendance-summary-v2/` | `/dashboard/attendance-summary-v5/` |
| `/dashboard/attendance-excel-v2/` | `/dashboard/attendance-excel-v5/` |
| `/scheduler/assignments/v2/monthly-location-summary/...` | `/scheduler/assignments/v5/monthly-location-summary/...` |
| `/scheduler/assignments/v2/monthly-location-summary-excel/...` | `/scheduler/assignments/v5/monthly-location-summary-excel/...` |
| `/dashboard/attendance/bulk-entry-candidates/` | `/dashboard/attendance/bulk-entry-candidates_v5/` |
| `/dashboard/attendance/v2/bulk-entry-candidates/` | `/dashboard/attendance/v5/bulk-entry-candidates/` |
| `/rollcall/sessions/` | `/rollcall/v5/sessions/` |
| `/rollcall/dashboard/filter/` | `/rollcall/v5/dashboard/filter/` |
| `/rollcall/sessions/start/` | `/rollcall/v5/sessions/start/` |
| `/rollcall/sessions/export/` | `/rollcall/v5/sessions/export/` |
| `/rollcall/sessions/export-pdf/` | `/rollcall/v5/sessions/export-pdf/` |

---

### 4.9 Incident — v5

| Method | Live (do not change) | v5 |
|--------|----------------------|-----|
| POST | `/incident/report/` | `/incident/v5/report/` — **`site_id` required** |
| GET | `/incident/dashboard/filter/` | `/incident/v5/dashboard/filter/` — query `site_id` |
| GET | `/incident/dashboard/export-excel/` | `/incident/v5/dashboard/export-excel/` |
| GET | `/incident/dashboard/export-pdf/` | `/incident/v5/dashboard/export-pdf/` |
| GET | `/incident/mytickets/export-excel/` | `/incident/v5/mytickets/export-excel/` |
| POST | `/incident/assign/<ticket_number>/` | Live OK (no new field) or `/incident/v5/assign/<ticket_number>/` |
| POST | `/incident/resolve/<ticket_number>/` | Same |

---

### 4.10 Visitor — v5

| Method | Live (do not change) | v5 |
|--------|----------------------|-----|
| POST | `/visitors/entries/checkin/` | `/visitors/v5/entries/checkin/` — **`site_id` required** |
| POST | `/visitors/entries/invite/` | `/visitors/v5/entries/invite/` — **`site_id` required** |
| POST | `/visitors/entries/<id>/complete-invite/` | `/visitors/v5/entries/<id>/complete-invite/` |
| GET | `/visitors/entries/` | `/visitors/v5/entries/` — query `site_id` |
| GET | `/visitors/entries/export/` | `/visitors/v5/entries/export/` |
| GET | `/visitors/entries/export-pdf/` | `/visitors/v5/entries/export-pdf/` |
| GET | `/visitors/search/` | `/visitors/v5/search/` |
| GET | `/visitors/qr-scan/` | `/visitors/v5/qr-scan/` |
| GET | `/visitors/reports/vehicle-movement/` | `/visitors/v5/reports/vehicle-movement/` |
| GET | `/visitors/reports/vehicle-movement/export/` | `/visitors/v5/reports/vehicle-movement/export/` |
| GET | `/visitors/reports/vehicle-movement/export-pdf/` | `/visitors/v5/reports/vehicle-movement/export-pdf/` |
| POST | `/visitors/entries/<id>/approve/` | Live OK or `/visitors/v5/entries/<id>/approve/` |
| POST | `/visitors/entries/<id>/checkout/` | Same pattern |
| POST | `/visitors/entries/<id>/cancel/` | Same |
| POST | `/visitors/entries/<id>/revert/` | Same |
| POST | `/visitors/entries/<id>/reschedule/` | Keep same `site_id` on the row |

---

### 4.11 Payslip — v5

No new column. Query `site_id` → employees allowed for that site. Live payslip URLs **unchanged**.

| Live | v5 |
|------|-----|
| `/payslip/payroll-profiles/` | `/payslip/v5/payroll-profiles/` |
| `/payslip/records/` | `/payslip/v5/records/` |
| `/payslip/reports/hourly-wage-summary/export-excel/` | `/payslip/v5/reports/hourly-wage-summary/export-excel/` |
| `/payslip/reports/hourly-wage-summary/export-pdf/` | `/payslip/v5/reports/hourly-wage-summary/export-pdf/` |
| `/payslip/reports/quick-pay-preview/` | `/payslip/v5/reports/quick-pay-preview/` |
| `/payslip/advances/` | `/payslip/v5/advances/` |
| `/payslip/quick-pay-disbursements/` | `/payslip/v5/quick-pay-disbursements/` |

Templates stay org-level (no site; no v5 required).

---

## 5. Rollout (module order + v5 only)

Live URLs stay. All new behaviour is on **v5**.

| Phase | Module | Work |
|-------|--------|------|
| **B0** | Users | `UserSite` + `all_org_sites`; user create/update/list **v5** |
| **B1** | Foundation | Login/refresh **v5**; `GET/POST /auth/v5/my-sites/` |
| **B2** | Shift assign | `AssignmentDailySite` + `GuardSiteCache`; assign **v5**; `shift_today_v5` |
| **B3** | Incident | Create + filter/export **v5** |
| **B4** | Visitor | Create + list/export **v5** |
| **B5** | Payslip | Employee scope **v5** |
| **B6** | Dashboard | `checkin_v5` / `scan_v5` / list+export **v5**; roll call **v5** |
| **B7** | PDF | Site name on v5 PDFs (with Dashboard) |

New columns nullable. Live clients never required to send `site_id`.

---

## 6. Short rules

1. **Do not change live APIs.** Copy and add **v5**.
2. Build order: **Users → Foundation → Shift assign → Incident → Visitor → Payslip → Dashboard (last)**.
3. **Assignment / Shift / Checkpoint master** = **no `site_id`**.
4. **`AssignmentDailySite`** = assign, or select when no row, or switch **before** attendance.
5. **`GuardSiteCache`** = every site **switch after attendance**. Return **latest**.
6. **`current_site` order:** latest cache → else daily roster → else null.
7. Attendance punch saves `CheckInLog` only — does not write cache.
8. **Checkpoint scan v5** → `CheckIn.site`. Resolve: request → latest cache → daily.
9. **Attendance v5** → `CheckInLog.site`. If no `site_id`, geofence.
10. Current site = `GET /auth/v5/my-sites/` and `shift_today_v5`.
11. Visitor / incident **v5** create: `site_id` required.
12. Lists/exports **v5**: query `site_id` + server access check.
