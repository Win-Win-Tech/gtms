# Attendance punch v5 — API reference

**Status:** Implemented  
**Date:** 2 Sep 2026  
**Auth:** JWT `Authorization: Bearer <access>`  
**Content-Type:** `multipart/form-data`  

Live `checkin_v4` / `checkout_v4` are **unchanged**. Mobile apps that enable site-wise attendance should call the v5 endpoints below.

---

## Overview

| Method | URL | Purpose |
|--------|-----|---------|
| POST | `/dashboard/attendance/checkin_v5/` | Guard check-in |
| POST | `/dashboard/attendance/checkout_v5/` | Guard check-out |

### Site resolution (both endpoints)

| `site_id` in request | Behaviour |
|----------------------|-----------|
| **Provided** (non-empty UUID) | Use that site. Must belong to the org, user must have access, GPS must be within org `attendance_distance` of the site centre. Response `site_resolution`: `"request"`. |
| **Empty / omitted** (`null`, `""`, `undefined`) | Pick the **nearest** active site within `attendance_distance` among sites the user may access. Response `site_resolution`: `"nearest"`. |

Geofence radius comes from org Site Setting `attendance_distance` (default **150** metres).

### Checkout-only rule

`checkout_v5` requires the resolved checkout site to match the **open check-in** site (latest check-in log in the shift window with no checkout after it). If they differ → `400` `checkout_site_mismatch`.

### Related mobile APIs

| API | Purpose |
|-----|---------|
| `GET /dashboard/attendance/shift_today_v5/` | Today’s shift + `assigned_site_id` / `last_selected_site_id` |
| `POST /auth/v5/my-sites/` | Switch working site (updates cache; does not punch attendance) |
| `POST /dashboard/attendance/create_assignment_v5/` | Self-assign shift with optional `site_id` |

---

## Request fields (multipart)

| Field | Check-in | Check-out | Required | Notes |
|-------|----------|-----------|----------|-------|
| `latitude` | Yes | Yes | **Yes** | Float |
| `longitude` | Yes | Yes | **Yes** | Float |
| `site_id` | Yes | Yes | No | UUID string. Omit or empty → nearest-site fallback |
| `image` | Yes | Yes | If face attendance enabled | Generic image field |
| `checkin_image` | Yes | — | Alt. to `image` on check-in | |
| `checkout_image` | — | Yes | Alt. to `image` on check-out | |

When the organisation has `is_face_attendance_enabled = true`, a live face image is **required** and must match the logged-in user’s enrolled face (same as v4).

---

## Response shape (success)

Responses use the same base as `checkin_v4` / `checkout_v4`: full `AttendanceCheckin` serializer payload, plus v5 extras.

**v4 fields (unchanged):** `id`, `checkin_time`, `checkout_time`, `shift_date`, `site` (FK UUID), `latitude`, `longitude`, `checkin_count`, `checkout_count`, `duration_minutes`, `status`, `pa_status`, image URLs, etc.

**Added on v5:**

| Field | Type | Example | Meaning |
|-------|------|---------|---------|
| `face_attendance` | boolean | `false` | Org has face attendance enabled |
| `face_verified` | boolean | `false` | Face matched (true when face attendance enabled and image OK) |
| `site_id` | string (UUID) | `"a1b2c3d4-..."` | Site used for this punch |
| `site_name` | string | `"Ravee Home"` | |
| `site_resolution` | string | `"request"` or `"nearest"` | How site was chosen |
| `mode` | string | `"checkin"` or `"checkout"` | Punch type |

---

## `POST /dashboard/attendance/checkin_v5/`

### Success — check-in with explicit `site_id`

**HTTP `201 Created`**

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "guard": "8f3e2a1b-4c5d-6e7f-8a9b-0c1d2e3f4a5b",
  "assignment": "2a3b4c5d-6e7f-8a9b-0c1d-2e3f4a5b6c7d",
  "shift": "1b2c3d4e-5f6a-7b8c-9d0e-1f2a3b4c5d6e",
  "org_location": "9e8d7c6b-5a4f-3e2d-1c0b-9a8f7e6d5c4b",
  "site": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "shift_date": "2026-09-02",
  "checkin_time": "2026-09-02T09:05:12+05:30",
  "checkout_time": null,
  "checkin_count": 1,
  "checkout_count": 0,
  "duration_minutes": null,
  "status": "present",
  "pa_status": null,
  "latitude": 9.973179,
  "longitude": 78.150094,
  "checkin_image": "https://example.com/media/checkin/abc.jpg",
  "checkout_image": null,
  "face_attendance": false,
  "face_verified": false,
  "site_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "site_name": "Ravee Home",
  "site_resolution": "request",
  "mode": "checkin"
}
```

### Success — check-in without `site_id` (nearest fallback)

**HTTP `201 Created`**

Same body as above; `site_resolution` is `"nearest"`.

```json
{
  "site_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "site_name": "Main Boundary",
  "site_resolution": "nearest",
  "mode": "checkin"
}
```

(Other attendance fields omitted for brevity.)

### Error scenarios — check-in

#### No shift today

**HTTP `400 Bad Request`**

```json
{
  "message": "No shifts today"
}
```

#### Missing GPS

**HTTP `400 Bad Request`**

```json
{
  "error": "latitude and longitude are required"
}
```

#### Outside geofence (explicit `site_id`)

**HTTP `400 Bad Request`**

```json
{
  "error": "You are not within the authorized boundary of this site."
}
```

#### Outside geofence (nearest fallback — no site in range)

**HTTP `400 Bad Request`**

```json
{
  "error": "You are not within the authorized boundary of any site for this location."
}
```

#### Invalid or inactive `site_id`

**HTTP `400 Bad Request`**

```json
{
  "site_id": "Site not found or inactive."
}
```

#### `site_id` belongs to another organisation

**HTTP `400 Bad Request`**

```json
{
  "site_id": "Site does not belong to this organisation."
}
```

#### User not assigned to site

**HTTP `403 Forbidden`**

```json
{
  "error": "You are not allowed to use this site."
}
```

#### Check-in too soon after checkout

**HTTP `400 Bad Request`**

```json
{
  "error": "Check-in allowed 1 minute(s) after checkout",
  "code": "checkin_too_soon_after_checkout",
  "min_checkin_after_checkout_minutes": 1,
  "remaining_seconds": 42
}
```

#### Face attendance enabled — no image

**HTTP `400 Bad Request`**

```json
{
  "error": "Face attendance requires an image"
}
```

#### Face attendance enabled — face mismatch

**HTTP `400 Bad Request`**

```json
{
  "error": "Face does not match enrolled profile"
}
```

(Exact `error` text comes from face verification.)

#### Face library not installed on server

**HTTP `503 Service Unavailable`**

```json
{
  "error": "Face attendance is enabled for this location but face_recognition is not installed on the server.",
  "hint": "See docs/FACE_ATTENDANCE_INSTALL.md"
}
```

---

## `POST /dashboard/attendance/checkout_v5/`

### Success — checkout (site matches check-in)

**HTTP `200 OK`**

```json
{
  "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "site": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "shift_date": "2026-09-02",
  "checkin_time": "2026-09-02T09:05:12+05:30",
  "checkout_time": "2026-09-02T17:30:00+05:30",
  "checkin_count": 1,
  "checkout_count": 1,
  "duration_minutes": 504,
  "status": "present",
  "face_attendance": false,
  "face_verified": false,
  "site_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "site_name": "Ravee Home",
  "site_resolution": "request",
  "mode": "checkout"
}
```

Send the **same `site_id`** as check-in (or omit both and rely on nearest — must still resolve to the check-in site).

### Error scenarios — checkout

#### No shift today

**HTTP `400 Bad Request`**

```json
{
  "message": "No shifts today"
}
```

#### Never checked in

**HTTP `400 Bad Request`**

```json
{
  "message": "Cannot checkout before checkin"
}
```

#### Already checked out (no open session)

**HTTP `400 Bad Request`**

```json
{
  "error": "No open check-in session found for checkout.",
  "code": "no_open_checkin"
}
```

#### Check-in was via old API without site on log

**HTTP `400 Bad Request`**

```json
{
  "error": "Check-in site is missing. Please check in again using checkin_v5.",
  "code": "checkin_site_missing"
}
```

#### Checkout site ≠ check-in site

**HTTP `400 Bad Request`**

```json
{
  "error": "Checkout site must match your check-in site.",
  "code": "checkout_site_mismatch",
  "checkin_site_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "checkin_site_name": "Ravee Home",
  "checkout_site_id": "b2c3d4e5-f6a7-8901-bcde-f12345678901",
  "checkout_site_name": "Babu Home"
}
```

#### Missing GPS / geofence / site_id validation errors

Same shapes as check-in (`latitude and longitude are required`, outside boundary, invalid `site_id`, `403` site access).

#### Face attendance errors

Same shapes as check-in (`503`, missing image, face mismatch).

---

## cURL examples

### Check-in with `site_id`

```bash
curl -X POST "https://your-domain.com/dashboard/attendance/checkin_v5/" \
  -H "Authorization: Bearer <access_token>" \
  -F "latitude=9.973179" \
  -F "longitude=78.150094" \
  -F "site_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

### Check-in without `site_id` (nearest site)

```bash
curl -X POST "https://your-domain.com/dashboard/attendance/checkin_v5/" \
  -H "Authorization: Bearer <access_token>" \
  -F "latitude=9.973179" \
  -F "longitude=78.150094"
```

### Check-out (same site as check-in)

```bash
curl -X POST "https://your-domain.com/dashboard/attendance/checkout_v5/" \
  -H "Authorization: Bearer <access_token>" \
  -F "latitude=9.973180" \
  -F "longitude=78.150095" \
  -F "site_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890"
```

### Check-in with face image

```bash
curl -X POST "https://your-domain.com/dashboard/attendance/checkin_v5/" \
  -H "Authorization: Bearer <access_token>" \
  -F "latitude=9.973179" \
  -F "longitude=78.150094" \
  -F "site_id=a1b2c3d4-e5f6-7890-abcd-ef1234567890" \
  -F "checkin_image=@/path/to/face.jpg"
```

---

## Mobile integration notes

1. Prefer sending `last_selected_site_id` from `GET /auth/v5/my-sites/` or `shift_today_v5` as `site_id` on every punch.
2. Use **check-in and check-out on the same v5 pair** — do not mix `checkin_v4` + `checkout_v5` if you need site-match enforcement.
3. On `checkout_site_mismatch`, show the user they must checkout at the same site they checked in at (`checkin_site_name`).
4. v5 success responses are a **superset** of v4 — existing parsers for v4 fields continue to work.
5. Kiosk `POST /dashboard/attendance/face_attendance/` is separate and still uses GPS auto-detect (not these endpoints).

---

## Error code summary

| HTTP | `code` (when present) | When |
|------|-------------------------|------|
| 400 | — | No shift, missing GPS, geofence, invalid site, face errors |
| 400 | `checkin_too_soon_after_checkout` | Check-in before minimum gap after checkout |
| 400 | `no_open_checkin` | Checkout with no open check-in session |
| 400 | `checkin_site_missing` | Open check-in log has no `site_id` |
| 400 | `checkout_site_mismatch` | Checkout site ≠ check-in site |
| 403 | — | User not allowed to use resolved site |
| 503 | — | Face library missing on server |
