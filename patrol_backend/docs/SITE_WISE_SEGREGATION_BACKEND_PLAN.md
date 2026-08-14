# Site-wise Segregation — Backend Plan

**Status:** Planning  
**Date:** 14 Aug 2026  

Backend only. Data model + APIs. No UI. No implementation code.

---

## Read this first

A user belongs to **one organisation** (`Location`). Under that org there are **sites** (`LocationSite`).

Site is used in **three different ways**. Do not mix them.

| What | Table | Meaning |
|------|--------|---------|
| **Who may use which sites** | `UserSite` + flag `all_org_sites` | Access for lists, reports, login |
| **Where the guard is posted that day** | **`AssignmentDailySite`** (new) | One row per guard per date |
| **Where an event happened** | Scan log (`CheckIn`), attendance (`CheckInLog`), incident, visitor | Stored **when the event is marked**, not on master Checkpoint / Assignment |

**No `site_id` on:** `Assignment`, `Shift`, or master **`Checkpoint`**.

Site is saved only on:

1. **`AssignmentDailySite`** — when shift is assigned with a site (posted site per day)
2. **`CheckIn`** — when the user marks a checkpoint as scanned
3. **`CheckInLog`** (already has `site`) — when the user marks attendance check-in / check-out

Plus incident / visitor rows on create.

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

That is the whole design.

- Create / bulk assign with `site_id` → fill daily rows for every date in the range.
- PATCH with `site_id` **and** `date` → update **that one day**.
- PATCH with `site_id` **without** `date` → update **all days** in that assignment range.

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

Written from **existing assign-shift APIs** when `site_id` is sent. Not a new assign flow.

### 2.3 Where `site` is stored (and where it is not)

| Table | Change |
|--------|--------|
| `Assignment` | **No `site`** |
| `Shift` | **No `site`** |
| `Checkpoint` (master) | **No `site`** — create / assign checkpoint unchanged |
| **`AssignmentDailySite`** (new) | Posted site per guard per day |
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

## 4. APIs

Shared helper: allowed site IDs for `request.user`.

Where the wrapper already exists, keep `{ status, message, data }`.

### 4.1 Auth / users

| Method | Path | What changes |
|--------|------|----------------|
| POST | `/auth/login/` | `data` adds `assigned_sites: [{ id, name }]`, `all_org_sites`. Visitor keys stay as today: `is_host_approve_enabled`, `visitor_default_purpose_of_visit`, `visitor_default_remarks`. **No posted site here** |
| POST | `/auth/refresh-token/` | Same as login |
| GET | `/auth/my-sites/` | **New.** Assigned sites + posted site for that date + visitor keys (below) |
| POST | `/auth/users/` | Body: `site_ids[]`, `all_org_sites` |
| PATCH | `/auth/users/<uuid:id>/` | Same |
| GET | `/auth/users/<uuid:id>/detail/` | Return `site_ids`, `all_org_sites`, `assigned_sites` |
| GET | `/auth/users/list/` | Query `site_id`. Super Admin: all orgs. Admin: own org. Others: users of that site only |
| GET | `/users/by-role/` | Query `site_id` (same rules) |

**GET `/auth/my-sites/`** (JWT)

- Query: `date` optional (`YYYY-MM-DD`). Default = today (user timezone).
- `data.assigned_sites` — from `UserSite` / `all_org_sites`
- `data.all_org_sites`
- `data.current_site` — from `AssignmentDailySite` for this user + date → `{ id, name, date }` or `null`
- Visitor SiteSetting (org, same as login): `is_host_approve_enabled`, `visitor_default_purpose_of_visit`, `visitor_default_remarks`

No daily row, or site no longer allowed → `current_site` is `null`.

**GET `/scheduler/locations/<id>/sites/`** — already exists. List physical sites of that org. Restrict to allowed sites for non-admin.

---

### 4.2 Assign shift (existing APIs — add `site_id`)

`site_id` is **not** saved on Assignment. It writes **`AssignmentDailySite`**.

| Method | Path | What happens |
|--------|------|----------------|
| POST | `/scheduler/assignments/` | Create Assignment as today. If `site_id` sent → one daily row **per date** from `start_date` to `end_date` |
| POST | `/scheduler/assignments/bulk-create/` | Same per item |
| PATCH/PUT | `/scheduler/assignments/<id>/` | `site_id` + **`date`** → update **that day only**. `site_id` with no `date` → update **all days** in the assignment range |
| POST | `/dashboard/attendance/create_assignment/` | Today-only assign. If `site_id` sent → **one** daily row for today. Response includes `site_id`, `site_name` |
| GET | `/scheduler/assignments/` | May include today’s `site_id` / `site_name` from the daily table |
| GET | `/scheduler/assignments/by-guard/<guard_id>/` | Same |

If assign is sent **without** `site_id`: Assignment is created, daily table stays empty until a later PATCH with `site_id`.

---

### 4.3 Checkpoint create / assign — **no `site_id`**

Master Checkpoint and checkpoint-template assign stay as today. **Do not** add `site` on Checkpoint. **Do not** send `site_id` on these APIs.

| Method | Path | Change |
|--------|------|--------|
| POST | `/scheduler/checkpoints/` | **No change** (no `site_id`) |
| PATCH/PUT | `/scheduler/checkpoints/<id>/` | **No change** |
| GET | `/scheduler/checkpoints/` | **No change** |
| GET | `/scheduler/checkpoints/by-location/<location_id>/` | **No change** |
| POST | `/dashboard/attendance/assign-checkpoint-template/` | **No change** (no `site_id`; does not write daily site) |

Posted site for the day comes only from **assign shift + `AssignmentDailySite`** (see §4.2).

---

### 4.4 Today’s shift — return posted site

Use **`shift_today_v3`**.

If `has_shift` is true: look up `AssignmentDailySite` for this user + that shift date.

- Row found → add `site_id`, `site_name`
- No row → `site_id`: null, `site_name`: null
- Rest of the payload unchanged

| Method | Path |
|--------|------|
| GET | `/dashboard/attendance/shift_today_v3/` |
| GET | `/dashboard/attendance/shift_today_v2/` (same extra fields if still used) |
| GET | `/dashboard/attendance/shift_today/` (same if still used) |
| GET | `/scheduler/assignments/upcoming-checkpoints/<user_id>/` — optional posted `site_id` for the day from daily table (not from Checkpoint) |

---

### 4.5 Checkpoint scan — save `site_id` on **`CheckIn`** (scan log)

Site is **not** on the Checkpoint master. It is saved when the user marks the checkpoint scanned.

| Method | Path |
|--------|------|
| POST | `/checkin/checkins/` |
| POST | `/checkin/checkins/scan_v2/` |
| POST | `/checkin/checkins/submit_checklist_v2/` |

**Pick site:** request `site_id` → else that day’s `AssignmentDailySite`. Save on **`CheckIn.site`**. If neither → 400.
---

### 4.6 Attendance punch — save `site_id` on **`CheckInLog`** (already exists)

Latest: **`checkin_v4` / `checkout_v4`**. Same rule on v3.

**Pick site:** request `site_id` → else `get_site_within_proximity(lat, lon, location_id)` (existing, radius = SiteSetting `attendance_distance`). If still none → 400. Save on **`CheckInLog.site`** and `AttendanceCheckin.site`.

| Method | Path | Note |
|--------|------|------|
| POST | `/dashboard/attendance/checkin_v4/` | `site_id` optional |
| POST | `/dashboard/attendance/checkout_v4/` | Same |
| POST | `/dashboard/attendance/checkin_v3/` | Same |
| POST | `/dashboard/attendance/checkout_v3/` | Same |
| POST | `/dashboard/attendance/face_attendance/` | Same |
| POST | `/dashboard/attendance/force-checkout_v3/` | Optional `site_id` |
| POST | `/dashboard/attendance/add-punch_v3/` | Already has `site_id` — enforce allowed |
| POST | `/dashboard/attendance/edit-punch_v3/` | Same |
| POST | `/dashboard/attendance/edit-boundary_v3/` | Same |
| POST | `/dashboard/attendance/bulk-entry/` | Already has `site_id` — enforce allowed |
| POST | `/dashboard/attendance/bulk-weekoff/` | Same |
| POST | `/dashboard/attendance/monthly-cell-action/` | Same |

---

### 4.7 Lists / Excel / PDF — query `site_id`

Filter to that site. Enforce allowed. PDF header **Site** = real site name.

| Method | Path |
|--------|------|
| GET | `/dashboard/api/attendance_v4/` |
| GET | `/dashboard/api/attendance/export_v4/` |
| GET | `/dashboard/api/attendance/export_v4_pdf/` |
| GET | `/dashboard/api/attendance_v3/` |
| GET | `/dashboard/api/attendance/export_v3/` |
| GET | `/dashboard/dashboard-checkin-report/v2/` |
| GET | `/dashboard/dashboard-checkin-report-excel/v2/` |
| GET | `/dashboard/dashboard-checkin-report-pdf/v2/` |
| GET | `/dashboard/attendance-summary-v2/` |
| GET | `/dashboard/attendance-excel-v2/` |
| GET | `/scheduler/assignments/v2/monthly-location-summary/<location_id>/<year>/<month>/` |
| GET | `/scheduler/assignments/v2/monthly-location-summary-excel/<location_id>/<year>/<month>/` |
| GET | `/dashboard/attendance/bulk-entry-candidates/` |
| GET | `/dashboard/attendance/v2/bulk-entry-candidates/` |
| GET | `/rollcall/sessions/` |
| GET | `/rollcall/dashboard/filter/` |
| POST | `/rollcall/sessions/start/` (body `site_id`) |
| GET | `/rollcall/sessions/export/` |
| GET | `/rollcall/sessions/export-pdf/` |

Attendance list APIs already accept `site_id` — add the allowed-sites check.

---

### 4.8 Incident — create must send `site_id`

| Method | Path | What happens |
|--------|------|----------------|
| POST | `/incident/report/` | **`site_id` required.** Save on `incidentreport.site` |
| GET | `/incident/dashboard/filter/` | Query `site_id`. Return `site_id`, `site_name` |
| GET | `/incident/dashboard/export-excel/` | Query `site_id` |
| GET | `/incident/dashboard/export-pdf/` | Query `site_id` |
| GET | `/incident/mytickets/export-excel/` | Query `site_id` |
| POST | `/incident/assign/<ticket_number>/` | No new field |
| POST | `/incident/resolve/<ticket_number>/` | No new field |

---

### 4.9 Visitor — create must send `site_id`

| Method | Path | What happens |
|--------|------|----------------|
| POST | `/visitors/entries/checkin/` | **`site_id` required.** Save on `VisitorEntry.site` |
| POST | `/visitors/entries/invite/` | **`site_id` required** |
| POST | `/visitors/entries/<id>/complete-invite/` | `site_id` if not already on the row |
| GET | `/visitors/entries/` | Query `site_id`. Return `site_id`, `site_name` |
| GET | `/visitors/entries/export/` | Query `site_id` |
| GET | `/visitors/entries/export-pdf/` | Query `site_id` |
| GET | `/visitors/search/` | Query `site_id` |
| GET | `/visitors/qr-scan/` | `site_id` so the pass is site-scoped |
| GET | `/visitors/reports/vehicle-movement/` | Query `site_id` |
| GET | `/visitors/reports/vehicle-movement/export/` | Query `site_id` |
| GET | `/visitors/reports/vehicle-movement/export-pdf/` | Query `site_id` |
| POST | `/visitors/entries/<id>/approve/` | No new field |
| POST | `/visitors/entries/<id>/checkout/` | No new field |
| POST | `/visitors/entries/<id>/cancel/` | No new field |
| POST | `/visitors/entries/<id>/revert/` | No new field |
| POST | `/visitors/entries/<id>/reschedule/` | Keep the same `site_id` on the row |

---

### 4.10 Payslip

No new column. Query `site_id` → only employees allowed for that site (`UserSite` / `all_org_sites`).

| Method | Path |
|--------|------|
| GET | `/payslip/payroll-profiles/` |
| GET | `/payslip/records/` |
| GET | `/payslip/reports/hourly-wage-summary/export-excel/` |
| GET | `/payslip/reports/hourly-wage-summary/export-pdf/` |
| GET | `/payslip/reports/quick-pay-preview/` |
| GET/POST | `/payslip/advances/` |
| GET/POST | `/payslip/quick-pay-disbursements/` |

Templates stay org-level (no site required).

---

## 5. Rollout

| Phase | Work |
|-------|------|
| **B0** | `UserSite` + `all_org_sites`; login/refresh; user create/update/list; GET `/auth/my-sites/` (`current_site` null until B1) |
| **B1** | `AssignmentDailySite`; `site_id` on assign-shift APIs only (writes daily rows); `shift_today_v3` returns posted `site_id` |
| **B2** | Add `site` on **`CheckIn`** (scan log); scan APIs accept / resolve `site_id` |
| **B3** | Punch: `site_id` or geofence on **`CheckInLog`**; dashboard / roll call list + export |
| **B4** | Incident create + filter/export |
| **B5** | Visitor create + list/export |
| **B6** | Payslip by `UserSite` |
| **B7** | PDF Site = real site name |

New columns nullable until `site_id` is sent.

---

## 6. Short rules

1. **Assignment / Shift / Checkpoint master** = **no `site_id`**.
2. **Posted site** = `AssignmentDailySite` only (from assign shift when `site_id` is sent).
3. Month assign + one site = one Assignment + **one daily row per day**.
4. Change one day = update **that daily row only**.
5. **Checkpoint scan** → save `site` on **`CheckIn`** (scan log), not on Checkpoint.
6. **Attendance** → save `site` on **`CheckInLog`** (already exists); if no `site_id` sent, use geofence.
7. Login = access sites. Posted site = `GET /auth/my-sites/` and `shift_today_v3`.
8. Visitor / incident create: `site_id` required on those rows.
9. Lists/exports: query `site_id` + server access check.
