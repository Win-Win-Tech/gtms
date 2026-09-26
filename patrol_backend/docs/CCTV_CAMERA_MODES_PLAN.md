# CCTV Camera Modes — Visitor / Attendance / People Count

**Status:** Planning (no implementation yet)  
**Last updated:** 2026-09-26  
**Related docs:**
- [`CCTV_VEHICLE_GATE_PLAN.md`](CCTV_VEHICLE_GATE_PLAN.md) — existing vehicle ANPR gate
- [`CCTV_ANPR_OPS.md`](CCTV_ANPR_OPS.md) — ANPR reader / Celery ops
- [`CCTV_ANPR_GEOMETRY_API.md`](CCTV_ANPR_GEOMETRY_API.md) — ROI + tripwire line
- [`CCTV_LIVE_VIEW.md`](CCTV_LIVE_VIEW.md) — MediaMTX / HLS

---

## 1. In one sentence

Extend each site CCTV camera so admin can choose **Visitor (vehicle ANPR)** or **Attendance (walk-by face punch)** as the primary mode, with an optional **People count** add-on that counts Entry / Exit on tripwire — delivered **one capability at a time**.

---

## 2. Client need (high level)

| Need | Description |
|------|-------------|
| Today | Cameras are vehicle-only: detect plate → create visitor entry / exit |
| Wanted | Camera settings: choose **Attendance** or **Visitor**; optional **People count** |
| Attendance | Not kiosk stand-still — people walk past camera; detect face; mark attendance using open session |
| People count | Separate Entry count and Exit count when crossing tripwire (anonymous) |

---

## 3. What exists today (baseline)

| Capability | Exists? | Notes |
|------------|---------|--------|
| Site camera CRUD (name, RTSP, direction, gate_mode, geometry) | Yes | `scheduler.SiteCamera` |
| Live HLS view | Yes | MediaMTX |
| ANPR → visitor check-in / check-out | Yes | Reader + Celery `anpr` queue + `apply_gate_event` |
| Per-camera tripwire (ROI + line) | Yes | Vehicle only |
| Camera purpose / mode field | **No** | No `visitor` / `attendance` / people-count flags |
| Walk-by CCTV face → attendance | **No** | Face attendance is **kiosk-only** today |
| People Entry / Exit counters | **No** | — |

### Current vehicle pipeline

```
RTSP camera
  → run_anpr_reader (detect / track in ROI, optional line cross)
  → Redis queue `anpr`
  → Celery process_anpr_frame (OCR)
  → apply_gate_event
       → Visitor (synthetic IC CCTV-{plate})
       → VisitorEntry check-in / check-out
       → VisitorAsset evidence photo
```

### Current face attendance (kiosk only)

```
Mobile/tablet upload still face
  → POST /dashboard/attendance/face_attendance/
  → geofence + identify_user_in_location (FAISS)
  → open session? → checkout : checkin
  → CheckInLog + AttendanceCheckin
```

**Open session** (no separate Session model): for that user on today’s shift window, latest check-in is newer than latest check-out.

---

## 4. Decisions (locked from product discussion)

| # | Topic | Decision |
|---|--------|----------|
| 1 | Primary mode | Mutually exclusive: **Visitor** **or** **Attendance** (main control) |
| 2 | People count | **Add-on checkbox** (optional), not a third primary mode |
| 3 | Direction / in-out | Same as vehicle: **same camera + tripwire**, or **separate Entry / Exit cameras** |
| 4 | Separate tripwires | **Yes** — each camera has its own `anpr_geometry.line` |
| 5 | Attendance capture style | Walk-by (not kiosk stand-still) |
| 6 | People count identity | Anonymous only (no face match required) |
| 7 | Attendance identity | Registered users only (face match) |
| 8 | Cooldown | Same pattern as vehicle (`ANPR_COOLDOWN_SEC`, default **60s**); key by plate (visitor) or user (attendance) |
| 9 | Display UI for counts / CCTV attendance events | **Decide later** |
| 10 | Delivery | **One by one** (not all features in one release) |
| 11 | Shift required for CCTV attendance | **Pending client confirmation** |
| 12 | Dual Entry+Exit race | Handled by shared site cooldown + lane rules (see §6); stronger “gate pair” optional later |

---

## 5. Target camera settings model

### UI (Organisation → Site → CCTV cameras)

For each camera:

1. **Primary mode** (required) — radio / select:
   - `Visitor` (vehicle ANPR → visitor entry) — default for existing cameras
   - `Attendance` (walk-by face → punch)
2. **People count** (optional checkbox) — add-on:
   - When enabled: tripwire crosses increment Entry / Exit counters
3. Existing fields remain: name, RTSP, enabled, `direction` (`toggle` / `in` / `out`), `gate_mode`, ROI + line geometry

### Proposed DB fields on `SiteCamera` (additive)

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `primary_mode` | CharField | `visitor` | `visitor` \| `attendance` |
| `people_count_enabled` | Boolean | `false` | Add-on people Entry/Exit counting |

> Exact allow-list for “People count on Visitor cams vs Attendance cams only” — still open (see §12).

### Logical flow

```
SiteCamera
  ├─ primary_mode = visitor     → ANPR plate gate → VisitorEntry
  ├─ primary_mode = attendance  → Face detect + match → CheckInLog / Attendance
  └─ people_count_enabled=true  → Tripwire Entry/Exit anonymous counts (add-on)
```

---

## 6. Separate Entry + Exit cameras and dual-sight race

### Can each have its own tripwire?

**Yes.** Geometry is per `SiteCamera`. Entry camera draws its line; Exit camera draws its own. Independent.

### Scenario (client doubt)

Vehicle at gate:

- **Entry camera** (outside-focused) captures vehicle **front**
- **Exit camera** (inside-focused) captures vehicle **back / side** at almost the same time  
  (or reverse: Exit marks, then Entry immediately sees same vehicle)

### What happens with **current** vehicle gate code

Key file: `visitor/anpr/gate.py`

| Rule | Behavior |
|------|----------|
| Shared cooldown | Key = `site_id + plate` (**not** camera). First success sets ~60s cooldown; second event for same plate → `cooldown` skip |
| Lane filter | `direction=in` → only check-in; `direction=out` → only check-out |
| Open entry | Exit with no open visit → `no_open_entry`; Entry while already in → `already_in` |
| Fuzzy plate | Front OCR ≠ rear OCR may still link to an open CCTV entry (not 100%) |

```
EntryCam ──► Gate: check_in plate X ──► DB open VisitorEntry
                                    ──► cooldown(site, X) = 60s
ExitCam  ──► Gate: check_out plate X (same moment)
         ◄── skip: cooldown  OR  no_open_entry (if Entry not committed yet)
```

### Remaining failure modes

| Risk | Why | Mitigation |
|------|-----|------------|
| Front OCR ≠ rear OCR | Different plate strings → two “vehicles” | Fuzzy open-entry match; ops: prefer readable plate side; later harden |
| Exit fires before Entry commits | Exit rejected; then Entry check-in | Usually OK for true entry; Exit line must not fire on approach |
| Exit cam sees approach lane | False exit attempts | Camera aim + `line_direction` + `direction=out` |
| Reverse: Exit then Entry | After checkout, Entry within cooldown skipped; after cooldown can check-in again | Correct for re-entry; tune cooldown if false double-sighting |

### Recommended ops config (document in hardening phase)

1. Prefer `gate_mode=line_direction` on both cams (**not** `parked_toggle`).
2. Entry: `direction=in` + inbound line only. Exit: `direction=out` + outbound line only.
3. Aim Entry at outside approach; Exit at inside leave lane — minimize overlap.
4. Keep **one shared cooldown per site + plate** (never per-camera-only for the same plate).

### Applies later to Attendance / People count

| Mode | Cooldown / debounce key |
|------|-------------------------|
| Visitor | `site + plate` (today) |
| Attendance | `site + user_id` (planned) |
| People count | Site/camera + track id or short debounce on line cross (planned) |

**v1 stance:** Shared cooldown + lane filter is enough. Optional later: explicit “gate pair” linking Entry/Exit cameras.

---

## 7. Attendance mode (walk-by) — detailed intent

### Not kiosk

| Kiosk today | CCTV Attendance (planned) |
|-------------|---------------------------|
| User stands still in front of tablet | User **walks** past fixed camera |
| Phone uploads one photo + GPS | RTSP frames from site camera |
| Admin auth on device | Camera agent / reader pipeline |
| Geofence mandatory | Site bound via camera’s `LocationSite` (no phone GPS) |

### Planned flow

```
Attendance-mode RTSP camera
  → detect person / face in ROI (optional tripwire for direction)
  → crop face → identify_user_in_location (existing FAISS)
  → if matched registered user:
       → cooldown check (site + user)
       → resolve direction (line cross OR camera direction=in/out)
       → open session? → checkout : checkin
       → apply punch (reuse kiosk_apply_punch / attendance helpers)
  → else: ignore or log (no anonymous attendance)
```

### Direction semantics (same as vehicle)

| Setup | Behavior |
|-------|----------|
| Single cam + `line_direction` | Cross side → in or out |
| Separate cams | Entry cam `direction=in` only check-in; Exit cam `direction=out` only check-out |
| `toggle` (if allowed) | Open session → out, else in — riskier for walk-by; prefer line or dedicated cams |

### Cooldown

- Reuse vehicle pattern: default **60 seconds** (configurable).
- Key: `cctv-attendance:cooldown:{site_id}:{user_id}` (name TBD).
- Prevents double punch when both Entry and Exit glimpse the same person.

### Open product question (blocking punch rules)

**Must the user have today’s assigned shift** (same as kiosk `no_shift_today`), or may any registered face at that location punch?

→ Confirm with client before implementing Phase A punch gate.

### Reuse (do not reinvent)

| Piece | Path |
|-------|------|
| Face 1:N | `patrol_backend/utils/face_index.py` → `identify_user_in_location` |
| Session + punch | `patrol_backend/utils/kiosk_attendance_fast.py` |
| Attendance row rules | `patrol_backend/utils/attendance_resolve.py` |
| Tripwire tracking ideas | `visitor/anpr/tracker.py`, `geometry.py` |

### Risks / gaps vs kiosk

- Distant / angled / multi-person frames → miss or wrong match
- Quality retry / second-pass can be slow on CPU (see Indus kiosk timing lessons)
- Must debounce walk-by (cooldown + direction) or punches flip repeatedly
- Lighting at gate often worse than kiosk booth

---

## 8. People count add-on — detailed intent

### What it is

- Optional checkbox on camera.
- On **tripwire cross**: increment **Entry** or **Exit** separately.
- **Anonymous** — no face identify, no attendance, no visitor record.

### What it is not

- Not a substitute for Attendance.
- Not plate / vehicle count (that stays Visitor/ANPR).
- Not the first place we build dashboard/mobile UI (deferred).

### Planned flow

```
Camera with people_count_enabled
  → person / blob track in ROI
  → line cross → Entry (+1) or Exit (+1)
  → persist counter (store TBD: per site / camera / day)
  → optional internal read API later; UI later
```

### Open product question

Can People count be enabled on **both** Visitor and Attendance cameras, or only one primary mode?

---

## 9. Delivery phases (one by one)

Do **not** ship Visitor + Attendance + People count together. Suggested order (confirm #2 in §12):

| Phase | Goal | Depends on |
|-------|------|------------|
| **0** | Camera settings foundation (`primary_mode`, `people_count_enabled`) + UI + API | — |
| **A** | Attendance walk-by pipeline + cooldown + punch | Phase 0 + client shift answer |
| **B** | People count Entry/Exit counters + storage + stub API | Phase 0 |
| **C** | Hardening: debounce, placement docs, dual-cam ops notes, logging | A and/or B |

### Phase 0 — Camera settings foundation

**Goal:** Admin can select mode without changing runtime pipelines yet (Visitor path unchanged).

- [ ] Migration: `SiteCamera.primary_mode`, `SiteCamera.people_count_enabled`
- [ ] Serializers + `PUT /scheduler/sites/<site_id>/cameras/` payload
- [ ] Web: `SiteCctvCamerasPanel` — primary mode control + People count checkbox
- [ ] Default: existing cameras → `primary_mode=visitor`, people count off
- [ ] Reader still only runs ANPR for `visitor` until Phase A

### Phase A — Attendance walk-by

**Goal:** Attendance-mode cameras mark check-in/out for matched registered users.

- [ ] Reader / worker branch for `primary_mode=attendance`
- [ ] Person/face detect + crop from RTSP
- [ ] `identify_user_in_location` + site-bound punch (no GPS)
- [ ] Direction from line or `direction=in|out`
- [ ] Cooldown `site + user`
- [ ] Shift rule per client decision
- [ ] Structured logs (timing) for slow-path debug
- [ ] Evidence photo optional (decide later)

### Phase B — People count

**Goal:** Anonymous Entry/Exit counts on tripwire.

- [ ] Enable path when `people_count_enabled=true`
- [ ] Persist Entry / Exit counts (schema TBD)
- [ ] Minimal internal GET for counts
- [ ] UI / reports / mobile — **out of scope until decided**

### Phase C — Hardening

- [ ] Dual-camera ops guide (aim, line, cooldown)
- [ ] Debounce / track ID hygiene
- [ ] OCR front/rear mismatch notes (Visitor)
- [ ] Face quality / multi-face policy (Attendance)
- [ ] Update ops docs alongside `CCTV_ANPR_OPS.md`

---

## 10. Explicitly out of scope (for now)

- Dashboard / mobile screens for people counts or CCTV attendance event lists
- Changing behaviour of cameras that remain `primary_mode=visitor` (except shared cooldown docs)
- Kiosk UI / kiosk API redesign
- Strong “gate pair” object linking Entry+Exit cameras (optional later)
- Auto-checkout timeout for visitors who never exit (existing product rule)

---

## 11. Codebase anchors

| Area | Path |
|------|------|
| Camera model | `scheduler/models.py` → `SiteCamera` |
| Camera API | `scheduler/views_cameras.py` |
| Camera UI | `GTMS_NEw/src/pages/organisation/components/SiteCctvCamerasPanel.jsx` |
| ANPR reader | `visitor/anpr/reader.py` |
| ANPR gate + cooldown | `visitor/anpr/gate.py` |
| ANPR settings | `ANPR_COOLDOWN_SEC` in `patrol_backend/settings.py` |
| Face identify | `patrol_backend/utils/face_index.py` |
| Kiosk punch | `patrol_backend/utils/kiosk_attendance_fast.py` |
| Kiosk API | `dashboard/views.py` → `face_attendance` |

---

## 12. Open items (must answer before coding Attendance)

1. **Shift rule:** CCTV attendance — require today’s assigned shift like kiosk, or any registered user at location?
2. **Build order:** Phase A (Attendance) first, then B (People count) — or reverse?
3. **People count add-on:** Allowed on **both** Visitor and Attendance cameras, or only one?
4. **Dual-camera race:** Confirm v1 = shared site cooldown + lane filter is enough (no gate-pair object yet)?

---

## 13. Acceptance sketch (when a phase is “done”)

### Phase 0 done when

- Admin can save Visitor vs Attendance + People count flag on a site camera.
- Existing Visitor ANPR sites keep working with default `visitor`.

### Phase A done when

- Walking past an Attendance Entry camera (registered face, cooldown clear) creates check-in.
- Walking past Exit (or opposite line) creates check-out when session open.
- Double-sight within cooldown does not double-punch.
- Unmatched faces do not create attendance.

### Phase B done when

- Tripwire Entry and Exit increments are stored and readable via API stub.
- Counts remain anonymous (no user/visitor linkage required).

---

## 14. Summary for stakeholders

| Mode | Detects | Identifies | Writes |
|------|---------|------------|--------|
| Visitor (existing) | Vehicle / plate | Plate string | VisitorEntry in/out |
| Attendance (new) | Face walk-by | Registered user | Attendance check-in/out |
| People count (add-on) | Person line cross | Nobody | Entry count / Exit count |

**Build order:** settings first → one runtime mode at a time → UI for counts later.  
**Dual Entry/Exit cams:** each has own tripwire; shared site cooldown + lane rules prevent most double events.
