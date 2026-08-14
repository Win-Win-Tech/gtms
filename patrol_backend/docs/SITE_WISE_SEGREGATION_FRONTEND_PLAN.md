# Site-wise Segregation — Frontend (Web) Plan

**Status:** Planning  
**Date:** 14 Aug 2026  
**Related:** Backend plan — `SITE_WISE_SEGREGATION_BACKEND_PLAN.md`

This document covers **web UI only**. Mobile uses the same APIs (see backend plan). No implementation code.

---

## 1. Goal

- Global **Site** dropdown in the header.
- Dropdown shows **only sites this user may access**.
- Selected site drives Dashboard, Users, Payslip, Incident, Visitor (lists, create, export).
- **Admin** sees **their org only**. **Super Admin** can pick **any org** (as today), then that org’s sites.
- Shift assignment UI does **not** save site on the long assignment. Daily posted site is a **separate** day-wise UI.

---

## 2. Access (what the UI shows)

| Role | Org picker | Site dropdown | Users list |
|------|------------|---------------|------------|
| **Super Admin** | Yes (all orgs) | Sites of selected org | That org + selected site |
| **Admin** | No (own org) | All sites in own org | Own org + selected site |
| **Others** | No | Only assigned sites | Users of selected site only |

User site access types (from login):

- One site → dropdown one value (can auto-select).
- Many sites → user picks one.
- All org sites → dropdown all sites of that org.

---

## 3. Global Site dropdown

**Where:** `DashboardLayout` header (next to notifications / avatar).

**Behaviour:**

- Call **`GET /auth/my-sites/`** (same API as mobile). Fill dropdown from `assigned_sites`.
- If `current_site` is set, default the header to that site.
- Persist `selectedSiteId` in session (same idea as `locationId`). User can still change the header to another assigned site.
- On change: in-scope pages refetch with `site_id`.
- Super Admin: org filter on the page (as today) reloads the site list for that org.

Do not keep a second Site dropdown on every report page once the header exists. Attendance may keep a local Site field until it is switched to the header.

---

## 4. Screens to change

### 4.1 Users

- Create/edit: site multi-select + “All sites in this organisation”.
- List: filtered by header site.
- Super Admin: org picker + site. Admin: own org + site. Others: assigned site only.

### 4.2 Dashboard

- Attendance, check-in, monthly attendance, monthly location, roll call: pass header `site_id` into existing APIs.
- Align bulk attendance Site with header (same selected site).

### 4.3 Shift assign — no site on Assignment

Long assignment (today → next year) has **no site field**.

**Daily posted site** (new UI, same idea as bulk attendance date + site):

- Pick **date** (or date range that **writes one row per day**).
- Pick **site**.
- Save to daily-site API, not Assignment create.

Screens:

- Normal shift assign (`AssignShift`) — assignment as today; daily site separately.
- Bulk assign (`BulkAssignTab`) — same.
- Org shift tab — assignment unchanged.

**Checkpoint** screens: **no Site field** on create/edit. Site is stored only when a checkpoint is **scanned** (`CheckIn`) and when attendance is marked (`CheckInLog`). Posted site for the day comes from shift assign → `AssignmentDailySite`.

### 4.4 Incident

- Filters + export: header `site_id`.
- Create (if any on web): selected site.

### 4.5 Visitor

- History, manual entry, vehicle movement: `site_id` on list and create.
- Export uses same site.

### 4.6 Payslip

- Lists and reports: header `site_id` (employees of that site).

---

## 5. Login / session

After login and refresh, also call **`GET /auth/my-sites/`** (shared with mobile). Store:

- `assigned_sites`
- `all_org_sites`
- `current_site` (today’s posted site, or null)
- visitor SiteSetting keys (`is_host_approve_enabled`, `visitor_default_purpose_of_visit`, `visitor_default_remarks`)
- `selectedSiteId` — prefer `current_site.id`, else the only assigned site, else last session value if still allowed

If none and not all-org-sites, show no site access.

Do not change login JSON wrapper (`status`, `message`, `data`).

---

## 6. Frontend rollout

| Phase | Work |
|-------|------|
| **F0** | Users create/edit site assignment; list by site (needs B0) |
| **F1** | Header Site dropdown + session; load from **GET `/auth/my-sites/`** (same as mobile) |
| **F2** | Daily posted-site UI (assign screens); no Site on checkpoint master |
| **F3** | Dashboard reports use header `site_id` |
| **F4** | Incident filters/create/export |
| **F5** | Visitor history / entry / vehicle movement |
| **F6** | Payslip |

---

## 7. Frontend summary

| Do | Do not |
|----|--------|
| Header Site = access context | Put Site on the long Assignment form as a single field for the whole date range |
| Daily site picker per date (or range that expands to days) | Assume one site for a year-long shift assign |
| Pass `site_id` on list/create/export | Filter only in the browser without sending `site_id` |
| Admin = own org; Super Admin = all orgs | Show Admin all organisations |
