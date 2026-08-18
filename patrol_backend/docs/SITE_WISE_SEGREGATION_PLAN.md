# Site-wise Segregation — Scope of Work & Implementation Plan

**Status:** Planning (overview)  
**Date:** 14 Aug 2026  

This document is a **high-level overview**. Detailed plans:

- Backend: [`SITE_WISE_SEGREGATION_BACKEND_PLAN.md`](./SITE_WISE_SEGREGATION_BACKEND_PLAN.md)
- Frontend (web): [`SITE_WISE_SEGREGATION_FRONTEND_PLAN.md`](./SITE_WISE_SEGREGATION_FRONTEND_PLAN.md)
- Implemented v5 APIs (params + responses): [`SITE_WISE_SEGREGATION_V5_API.md`](./SITE_WISE_SEGREGATION_V5_API.md)

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

## 3. Modules — build order (dependency)

Build in this order. Each step needs the one above.

| # | Module | Depends on | Why this order |
|---|--------|------------|----------------|
| 1 | **Users** | — | `UserSite` / `all_org_sites` on create, update, list. Nothing else can know who may use which site |
| 2 | **Foundation** | Users | **No login/refresh v5.** Live login + `GET/POST /auth/v5/my-sites/`, `current_site` helper (cache then daily). Header / selected site |
| 3 | **Shift assign** | Users + Foundation | `AssignmentDailySite` on assign; select/switch before attendance updates daily; after attendance → `GuardSiteCache` |
| 4 | **Incident** | Foundation | Create/list/export with `site_id` from `current_site` |
| 5 | **Visitor** | Foundation | Create/list/export with `site_id` from `current_site` |
| 6 | **Payslip** | Users | Employee lists by `UserSite` (no daily-site table) |
| 7 | **Dashboard** | Shift assign | Attendance punch, scan, lists, roll call, monthly — **last** (needs posted / current site) |

**Not in this work:** Live Tracking, Settings, Role Management.

**Live vs v5:** same server is used by live users. **Do not change existing URLs.** Copy the API and register a **v5** path. Live keeps working. New site-wise clients call v5 only. Details in the backend plan.

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

**Web and mobile** both call **`GET /auth/v5/my-sites/`** (JWT) after live login for assigned sites and **today’s current site** (cache then `AssignmentDailySite`). Visitor SiteSetting stays on live login.

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

**Login / refresh:** **do not change.** Visitor SiteSetting keys stay on live login.

**Sites for web and mobile:** **`GET /auth/v5/my-sites/`** after login:

- `assigned_sites`: `[{ id, name }]`
- `all_org_sites`: boolean
- `current_site` = latest `GuardSiteCache` else `AssignmentDailySite` else `null`

Web may persist header `selectedSiteId` in session after that.

### 6.2 Existing tables — add nullable `site` FK → `LocationSite`

| Table | App | Today | Change |
|-------|-----|--------|--------|
| `Assignment` | scheduler | location + shift, date range, no site | **No `site` column.** Daily posted site in new table |
| `AssignmentDailySite` (new) | scheduler | — | guard + date + site (roster) |
| `GuardSiteCache` (new) | scheduler | — | site switches **after attendance** only; many rows OK; return **latest** |
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
- Helper used by all modules: **resolve allowed site IDs for `request.user`**; reject `site_id` if not allowed.
- **Do not change login or refresh-token.** Web and mobile call **`GET /auth/v5/my-sites/`** after login for `assigned_sites`, `all_org_sites`, `current_site`.

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

- Load sites from **`GET /auth/v5/my-sites/`** (not from login).
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

**Do not change login or refresh-token.** Visitor keys stay on live login as today. After login (and on app open if already logged in), call **`GET /auth/v5/my-sites/`**.

### 9.1 After login — `GET /auth/v5/my-sites/`

| Field | Meaning |
|-------|---------|
| `assigned_sites` | `[{ "id": "...", "name": "..." }]` — sites this user may use |
| `all_org_sites` | `true` = all sites in org |
| `current_site` | `{ id, name, date }` — latest `GuardSiteCache` for today, else `AssignmentDailySite`, else `null` (null until shift-assign tables exist) |

Visitor SiteSetting keys stay on **live login** (`is_host_approve_enabled`, `visitor_default_purpose_of_visit`, `visitor_default_remarks`).

If `assigned_sites` is empty and `all_org_sites` is false → treat as no site access (show message).

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

Same order as modules. **v5 APIs only** — live paths unchanged.

| Phase | Module | Work | Depends on |
|-------|--------|------|------------|
| **P0** | Users | `UserSite` + `all_org_sites`; user create/edit/list **v5** | — |
| **P1** | Foundation | **No login/refresh v5.** `GET/POST /auth/v5/my-sites/`; header Site dropdown | P0 |
| **P2** | Shift assign | `AssignmentDailySite` + `GuardSiteCache`; assign **v5**; `shift_today_v5` | P0–P1 |
| **P3** | Incident | Create + filter/export **v5** | P1 |
| **P4** | Visitor | Create + list/export **v5** | P1 |
| **P5** | Payslip | Employee scope **v5** | P0 |
| **P6** | Dashboard | Punch/scan/list/export **v5**; roll call **v5** | P2 |
| **P7** | PDF | Site line = real site name on v5 PDFs | P6 |

Live builds keep calling old URLs. New builds call **v5**.

---

## 11. Summary

| Layer | Change |
|-------|--------|
| **Access** | User assigned to 1 / many / all sites; dropdown = those sites only |
| **Org scope** | Admin = **their org only**. Super Admin = **all orgs** |
| **Users list** | Selected site users. Admin = their org. Super Admin = all orgs |
| **Header (web)** | Global Site select; drives Dashboard, Users, Payslip, Incident, Visitor |
| **DB** | User–site access; `AssignmentDailySite` (roster); `GuardSiteCache` (latest selected); `site` on **`CheckIn`** (scan), Incident, VisitorEntry; attendance/`CheckInLog` already has site. **Not** on Assignment, Shift, or Checkpoint |
| **Backend** | Live APIs unchanged. Site-wise on **v5** copies. Enforce `site_id` on v5 list + create |
| **Web** | GET my-sites → header dropdown + default site; module filters/creates |
| **Mobile** | Same GET my-sites; send `site_id` on create and lists |

Implementation starts at **P0 (Users)** then **P1 (Foundation)**. Live login/refresh stay; new clients use **v5** for site-wise APIs and **`GET /auth/v5/my-sites/`** after login.
