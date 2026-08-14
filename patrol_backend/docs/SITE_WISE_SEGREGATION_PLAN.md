# Site-wise Segregation — Scope of Work & Implementation Plan

**Status:** Planning (overview)  
**Date:** 14 Aug 2026  

This document is a **high-level overview**. Detailed plans:

- Backend: [`SITE_WISE_SEGREGATION_BACKEND_PLAN.md`](./SITE_WISE_SEGREGATION_BACKEND_PLAN.md)
- Frontend (web): [`SITE_WISE_SEGREGATION_FRONTEND_PLAN.md`](./SITE_WISE_SEGREGATION_FRONTEND_PLAN.md)

This is a **plan only**. It is not API curl or implementation code.

---

## 1. Goal

Today GTMS is **organisation (location) scoped**. A user belongs to one org. Lists and reports show **all data for that org**.

We will add **site-wise segregation** on top of org:

- A user still belongs to **one org**.
- A user is also assigned **sites** in that org: **one site**, **multiple sites**, or **all sites**.
- The web **global Site dropdown** shows **only that user’s assigned sites**.
- After a site is selected, Dashboard, Users, Payslip, Incident, Visitor (and related reports) show **only that site’s data**.
- Mobile create/list APIs must send and respect `site_id` the same way.

**Existing today:** `LocationSite` exists. Attendance already stores `site` (bulk attendance / geofence). Check-in, incident, visitor, assignment, and checkpoint **do not** store site yet. User has only `location` (org), **no site assignment**.

---

## 2. Hierarchy

```
Organisation (Location)
    └── Site (LocationSite)          ← already exists
            └── Users assigned to site(s)
            └── Shift assignments
            └── Checkpoints
            └── Attendance / check-in
            └── Incidents
            └── Visitors
```

- **Org** = company / location (unchanged).
- **Site** = physical site under that org.
- A user can work on **1 site / many sites / all sites** of their org.

---

## 3. Modules in this work

| # | Module | What we will do |
|---|--------|-----------------|
| 1 | **Foundation** | User ↔ site assignment; global Site dropdown; login/refresh returns assigned sites |
| 2 | **Shift assign** | **Do not** store site on Assignment / Shift / Checkpoint. Daily posted site in **`AssignmentDailySite`**. Scan saves site on **`CheckIn`**; attendance already on **`CheckInLog`** |
| 3 | **Dashboard** | Attendance / check-in / monthly / roll call lists **selected site only** |
| 4 | **Users** | List users of the **selected site**. **Admin** sees only **their org**. **Super Admin** can see **all orgs**. Other roles see only users of their assigned / selected site |
| 5 | **Payslip** | Show data only for sites the logged-in user can access |
| 6 | **Incident** | Site on incident row; list/create/export site-wise; mobile must send `site_id` |
| 7 | **Visitor** | Site on visitor entry; history / registration / vehicle movement; mobile must send `site_id` |

---

## 4. Access rules

### 4.1 Org scope (unchanged principle)

| Role | Org data |
|------|----------|
| **Super Admin** | Can see **all organisations** (picks org on screens as today) |
| **Admin** | Can see **only their own org** |
| **Other roles** | Can see **only their own org**, further limited by assigned sites |

### 4.2 Who can see which sites (dropdown)

| User type | Site dropdown |
|-----------|----------------|
| **Super Admin** | All sites of the org they are viewing |
| **Admin** | All sites in **their org** |
| **Other roles** (SO, FO, HR, Guard, etc.) | **Only assigned sites** |

Assignment types for a user:

- **Single site** → dropdown has that one site (can auto-select).
- **Multiple sites** → dropdown lists those sites; user picks one.
- **All sites in org** → dropdown lists all org sites (same as Admin for that org).

### 4.3 Who can see which users (Users screen)

| Role | User list |
|------|-----------|
| **Super Admin** | Users across **all orgs** (org picker as today), then **selected site** users |
| **Admin** | Users in **their org only**, for the **selected site** |
| **Other roles** | Only users **assigned to the currently selected site** (and that site must be one they are assigned to) |

### 4.4 Data screens (Dashboard, Payslip, Incident, Visitor)

Whatever is selected in the **global Site dropdown**:

- Lists, reports, Excel/PDF export → **that site only**.
- Create (incident, visitor, assignment) → save **that `site_id`**.

When a specific site is selected, show only rows with that `site_id`.

---

## 5. End-to-end flow

```
Login / Refresh token
    → Backend returns org + assigned_sites[] + flags (all_sites / admin)
    → Web stores sites + selectedSiteId (session)
    → Header Site dropdown filled from assigned_sites only

User changes Site in header
    → Persist selectedSiteId
    → All in-scope pages refetch with site_id

Create (web or mobile)
    → Must send site_id
    → Backend validates: site belongs to org AND user is allowed that site
    → Row saved with location_id + site_id

List / Report / Export
    → Query includes location_id (existing) + site_id (new)
    → Backend also enforces user cannot request a site they are not assigned to
```

**Web and mobile** both call **`GET /auth/my-sites/`** (JWT) for assigned sites, **today’s posted site** (`AssignmentDailySite` → `current_site`), and visitor SiteSetting (host approval, default purpose, default remarks).

- Web: fill header dropdown from `assigned_sites`; default select `current_site` when set.
- Mobile: no global header — use `current_site.id` on create/list when set.
- If one assigned site and no daily row → client may use that site.
- If many and `current_site` is null → picker (or wait until daily site is set).
- Send `site_id` on create and on list/filter APIs.

---

## 6. Data model changes (backend)

### 6.1 New: User ↔ Site assignment

A user can be on many sites, or “all sites in org”.

- Table **`UserSite`**: `user`, `site` (FK to `LocationSite`).
- Flag on User: **`all_org_sites = true/false`**.  
  If true → user can access every site in their org (no need to insert every site row).  
  If false → only rows in `UserSite`.

**User create / edit (web Users screen):**

- Pick sites: one / many / “All sites in this organisation”.
- Admin of the org and Super Admin can assign sites to users.

**Login / refresh / profile payload:** add

- `assigned_sites`: `[{ id, name }]`
- `all_org_sites`: boolean
- Visitor SiteSetting keys stay as today (`is_host_approve_enabled`, `visitor_default_purpose_of_visit`, `visitor_default_remarks`)

**Posted / selected site:** not on login. **Web and mobile** use **`GET /auth/my-sites/`** — `current_site` from `AssignmentDailySite` for today, plus assigned sites + visitor SiteSetting (see backend plan §5.8). Web may still persist header `selectedSiteId` in session after that.

### 6.2 Existing tables — add nullable `site` FK → `LocationSite`

| Table | App | Today | Change |
|-------|-----|--------|--------|
| `Assignment` | scheduler | location + shift, date range, no site | **No `site` column.** Daily posted site in new table |
| `AssignmentDailySite` (new) | scheduler | — | guard + date + site (+ optional assignment FK) |
| `Checkpoint` | scheduler | location only | **No `site`** — create / assign unchanged |
| `CheckIn` (scan log) | checkin | no site | **Add `site`** when user marks checkpoint scanned |
| `incidentreport` | incident | location only | Add `site` |
| `VisitorEntry` | visitor | location only | Add `site` |
| `AttendanceCheckin` / `CheckInLog` | dashboard | **already has site** | Set on attendance punch (sent `site_id` or geofence) |

Nullable first so old data still works. New creates should send `site_id`.

**Payslip:** employee is already tied to org. Site filter = employees **assigned to that site** (via `UserSite`).

---

## 7. Backend changes (by module)

### 7.1 Auth / Users

- User create/update APIs: accept site assignment (`site_ids[]` and/or `all_org_sites`).
- User list API:  
  - Super Admin: all orgs; filter by selected org + `site_id`.  
  - Admin: users in **their org** for the requested `site_id`.  
  - Others: users assigned to that site; caller must be allowed that site.
- Login + refresh-token: include `assigned_sites` + `all_org_sites` (same wrapper as today: `status`, `message`, `data`).
- Helper used by all modules: **resolve allowed site IDs for `request.user`**; reject `site_id` if not allowed.

### 7.2 Scheduler (shift assign + checkpoints)

- Assignment create / update / bulk-create: if `site_id` sent → write **`AssignmentDailySite`** only. **No `site` on Assignment.**
- Checkpoint create / update / assign-template: **no `site_id`**. Master Checkpoint unchanged.
- Scan APIs: save `site` on **`CheckIn`**.
- Attendance punch: save `site` on **`CheckInLog`** (existing).

**Places that must send `site_id` (for daily posted site):**

- Normal shift assign (`AssignShift`)
- Bulk assign (`BulkAssignTab` / bulk-assign route)
- Org shift tab (when posting site with the assign)

Checkpoint create / assign-template: **do not** send `site_id`.

Same idea as **bulk attendance already assigning site**.

### 7.3 Dashboard (attendance + check-in reports)

- Attendance list / v3 / v4 / Excel / PDF: apply `site_id` from query **and** allowed sites.
- Check-in report list + Excel/PDF: filter by site via **`CheckIn.site`** (scan log) / daily posted site.
- Monthly attendance / monthly location summary / roll call: same `site_id` filter.
- Bulk attendance already has site — keep it; later align with global header instead of a second local dropdown.

### 7.4 Incident

- Create (web + mobile): accept `site_id`, validate, save.
- Filter / dashboard / my-tickets / Excel / PDF: `site_id`.
- Serializer: return `site_id` / `site_name`.

### 7.5 Visitor

- Create walk-in / invite / check-in: accept `site_id`.
- Entry list, export Excel/PDF, vehicle movement report: filter by `site_id`.
- Serializer: return `site_id` / `site_name`.
- Mobile visitor APIs: same.

### 7.6 Payslip

- Employee list / generate / reports: restrict to users accessible for selected site (and user’s allowed sites).

### 7.7 PDF headers

- Reports already show **Site** in the header.  
- Fill with **real site name** when `site_id` is present.

---

## 8. Frontend (web) changes

### 8.1 Global Site dropdown (header)

**Where:** `DashboardLayout` top bar (next to notifications / avatar).

**Behaviour:**

- Load sites from login/refresh payload (or sites API filtered by assignment).
- Show **only assigned sites**.
- Persist `selectedSiteId` in session (like `locationId`).
- On change: pages refetch. Do not keep a second conflicting Site dropdown on every page (attendance may keep local Site until we switch it to global).

**Super Admin:** still picks Organisation on page filters where they do today; Site list depends on that org.

### 8.2 Users

- Create/edit user: site multi-select + “All sites in org”.
- List follows selected site.
- **Super Admin:** all orgs (org picker as today) + selected site users.
- **Admin:** **their org only** + selected site users.
- **Others:** only users of the selected assigned site.

### 8.3 Dashboard pages

- Attendance report, check-in report, monthly attendance, monthly location, roll call: pass global `site_id` into existing APIs.
- Remove or sync page-level Site filter with header (avoid two sources of truth).

### 8.4 Shift / checkpoint / bulk assign

- Site on assign / bulk assign → writes **`AssignmentDailySite`** (not Assignment / Checkpoint).
- Checkpoint create/edit: **no Site field**.
- Checkpoint list: no site on master Checkpoint.

### 8.5 Incident

- Filters + export use global `site_id`.
- Any web create uses selected site.

### 8.6 Visitor

- History, manual entry, vehicle movement: `site_id` on list and create.
- Export uses same site.

### 8.7 Payslip

- All payslip lists/reports: `site_id` / assigned-site scope.

---

## 9. Mobile app changes

Login and **refresh-token** already return the same flat `data` shape. We will **add** site fields; we will **not** change the wrapper (`status`, `message`, `data`).

### 9.1 After login / refresh — consume new fields

| Field | Meaning |
|-------|---------|
| `assigned_sites` | `[{ "id": "...", "name": "..." }]` — sites this user may use |
| `all_org_sites` | `true` = all sites in org |
| `is_host_approve_enabled` | Already on login today — keep |
| `visitor_default_purpose_of_visit` | Already on login today — keep |
| `visitor_default_remarks` | Already on login today — keep |

If `assigned_sites` is empty and `all_org_sites` is false → treat as no site access (show message).

Login does **not** include posted site. **Web and mobile** call **`GET /auth/my-sites/`** (JWT) after login (web: layout load; mobile: app open).

| Field | Meaning |
|-------|---------|
| `assigned_sites` / `all_org_sites` | Same as login |
| `current_site` | `{ id, name, date }` from `AssignmentDailySite` for today, or `null` |
| `is_host_approve_enabled` | Host approval SiteSetting |
| `visitor_default_purpose_of_visit` | Default purpose SiteSetting |
| `visitor_default_remarks` | Default remarks SiteSetting |

### 9.2 Site selection in the app

- Prefer **`current_site.id`** (posted today) on visitor / incident APIs.
- **1 assigned site** and no daily row: use that `id`.
- **Many sites** and `current_site` is null: picker, or wait until daily posted site is set.
- Do not rely on a locally stored site as the source of truth; refresh via my-sites.

### 9.3 APIs that must send `site_id` (create)

| Feature | Action |
|---------|--------|
| Shift assign | Create / bulk assign |
| Checkpoint (if created from app) | Create / update — **no `site_id`** |
| Checkpoint scan | **Required / resolved** — save on `CheckIn` |
| Attendance / punch (if not already) | Keep sending `site_id` as today; else geofence → `CheckInLog` |
| Attendance / punch (if not already) | Keep sending `site_id` as today |
| **Incident create** | **Required** |
| **Visitor create / walk-in / invite complete** | **Required** |

### 9.4 APIs that must send `site_id` (list / filter)

Same modules: incident list, visitor list, attendance, check-in, assignments.  
Do **not** request a `site_id` the user is not assigned to (403/400).

---

## 10. Suggested rollout (phases)

| Phase | Work | Depends on |
|-------|------|------------|
| **P0** | `UserSite` + `all_org_sites`; login/refresh sites; user create/edit sites; Users list rules | — |
| **P1** | Web global Site dropdown | P0 |
| **P2** | Daily posted-site table + APIs; **GET `/auth/my-sites/`**; scan saves `site` on `CheckIn`; assign UIs write daily site | P0 |
| **P3** | Dashboard reports filter by `site_id` (attendance already has data) | P1 |
| **P4** | Incident `site` + APIs + web + mobile create | P0–P1 |
| **P5** | Visitor `site` + APIs + web + mobile create | P0–P1 |
| **P6** | Payslip site scope | P0–P1 |
| **P7** | PDF Site line = real site name | After P3–P5 |

**Transition:** new columns nullable. Old mobile builds without `site_id` keep working until create is required.

---

## 11. Summary

| Layer | Change |
|-------|--------|
| **Access** | User assigned to 1 / many / all sites; dropdown = those sites only |
| **Org scope** | Admin = **their org only**. Super Admin = **all orgs** |
| **Users list** | Selected site users. Admin = their org. Super Admin = all orgs |
| **Header (web)** | Global Site select; drives Dashboard, Users, Payslip, Incident, Visitor |
| **DB** | User–site access; `AssignmentDailySite`; `site` on **`CheckIn`** (scan), Incident, VisitorEntry; attendance/`CheckInLog` already has site. **Not** on Assignment, Shift, or Checkpoint |
| **Backend** | Enforce allowed `site_id` on list + create; login/refresh returns assigned sites; GET my-sites returns assigned + posted site + visitor settings |
| **Web** | GET my-sites → header dropdown + default site; module filters/creates |
| **Mobile** | Same GET my-sites; send `site_id` on create and lists |

Implementation starts at **P0 (user–site + login payload)** so web and mobile can integrate against a stable contract.
