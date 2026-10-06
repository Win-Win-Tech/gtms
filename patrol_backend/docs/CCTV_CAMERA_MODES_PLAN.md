# CCTV Camera Types — Vehicle / Face (Face Attendance, ID Card Extract)

**Status:** Phase 0 done locally (not deployed) · Phase A next  
**Last updated:** 2026-10-05 (replaces the 2026-09-26 "Visitor / Attendance / People count" draft)  
**Related docs:**
- [`CCTV_VEHICLE_GATE_PLAN.md`](CCTV_VEHICLE_GATE_PLAN.md) — existing vehicle ANPR gate
- [`CCTV_ANPR_OPS.md`](CCTV_ANPR_OPS.md) — ANPR reader / Celery ops
- [`CCTV_ANPR_GEOMETRY_API.md`](CCTV_ANPR_GEOMETRY_API.md) — ROI + tripwire line
- [`CCTV_LIVE_VIEW.md`](CCTV_LIVE_VIEW.md) — MediaMTX / HLS
- [`FACE_ATTENDANCE_INSTALL.md`](FACE_ATTENDANCE_INSTALL.md) — kiosk face attendance (dlib + FAISS)

---

## 1. In one sentence

When adding a site camera, admin chooses **Vehicle** (current ANPR flow, unchanged) or **Face**; a Face camera has feature checkboxes — **Face attendance** (walk-by, one person or a group in one frame) now, **ID card extract** later.

---

## 2. Client need

| Need | Description |
|------|-------------|
| Today | Every camera is a vehicle camera: plate → visitor check-in / check-out |
| Wanted | Camera type choice: **Vehicle** or **Face** |
| Vehicle | Exactly the current flow |
| Face → Face attendance | Registered employees walking past the camera get check-in / check-out. Single person or a group of people in the same frame |
| Face → ID card extract | Wanted, but the use case is **not decided yet** → Phase B, designed later |
| Main focus | Face attendance + ID card extract. The other 11 AI ideas (people count, PPE, fire/smoke, …) are ignored for now, but the data model must allow them later |

---

## 3. What exists today (baseline)

| Capability | Exists? | Notes |
|------------|---------|--------|
| Site camera CRUD (name, RTSP, direction, gate_mode, geometry) | Yes | `scheduler.SiteCamera`, `PUT /scheduler/sites/<site_id>/cameras/` |
| Live HLS view | Yes | MediaMTX |
| ANPR → visitor check-in / check-out | Yes | Reader + Celery `anpr` queue + `apply_gate_event` |
| Camera type / feature field | **No** | ANPR reader loads **every** enabled camera (`visitor/anpr/reader.py`, camera refresh) |
| Face attendance | Kiosk only | Phone uploads a still → `identify_user_in_location` (FAISS) → punch |
| ID card OCR | Upload only | Visitor AI v2 `type=id` (RapidOCR + document detection) |

### Current face attendance (kiosk)

```
Mobile/tablet uploads one still face
  → POST /dashboard/attendance/face_attendance/
  → geofence + identify_user_in_location (FAISS, strict 0.38 / relaxed 0.45 + margin)
  → open session? → checkout : checkin
  → CheckInLog + AttendanceCheckin
```

Face encoding: dlib `face_recognition` (128-d). The slow part on CPU is dlib's HOG **face detection** (~1 s per full frame); encoding a face whose box is already known is much cheaper.

### Important camera API behaviour

`PUT /scheduler/sites/<site_id>/cameras/` **deletes and recreates** all cameras of the site, so camera ids change on every save. Anything that references a camera (event logs) must use `on_delete=SET_NULL` and also store the camera name.

---

## 4. Decisions

| # | Topic | Decision |
|---|--------|----------|
| 1 | Main choice | Camera type: **Vehicle** or **Face** (one per camera) |
| 2 | Face features | Checkboxes: **Face attendance**, **ID card extract** — at least one required |
| 3 | Existing cameras | Become `vehicle` automatically; no behaviour change |
| 4 | ANPR reader | Only reads `vehicle` cameras |
| 5 | Build order | Phase 0 (settings) → Phase A (face attendance) → Phase B (ID card, design later) |
| 6 | Scale for v1 | **Single face camera per site**; single person or a small group in one frame |
| 7 | Attendance identity | Registered users only (existing face index). Unknown faces never punch |
| 8 | Shift rule | **Decide later** → one setting, default = same as kiosk (no shift today = no punch) |
| 9 | ID card extract | **Use case not decided** → checkbox hidden / disabled ("coming soon") until Phase B |
| 10 | Other AI features | Out of scope; model must allow adding them without new columns |

---

## 5. Camera settings model (Phase 0)

### New fields on `SiteCamera` (additive)

| Field | Type | Default | Meaning |
|-------|------|---------|---------|
| `camera_type` | CharField(16), choices | `vehicle` | `vehicle` \| `face` |
| `features` | JSONField (list of strings) | `[]` | Face: `face_attendance`, `id_card_extract`. Vehicle: empty (ANPR is implied) |

Why a list instead of one boolean per feature: the future ideas fit the same two fields without migrations.

| `camera_type` | `features` now | Possible later |
|---------------|----------------|----------------|
| `vehicle` | — (ANPR) | — |
| `face` | `face_attendance`, `id_card_extract` | `gender_age` |
| `people` (later) | — | `people_count`, `crowd_threshold`, `idle_sitting`, `restricted_area`, `fall_detect` |
| `safety` (later) | — | `fire_smoke`, `weapon`, `ppe`, `missing_object` |
| `production` (later) | — | `conveyor_count` |

Only `vehicle` and `face` are accepted in Phase 0.

### Validation (API)

| Rule | Error |
|------|-------|
| `camera_type` not in allowed list | "Unknown camera type" |
| `face` with empty `features` | "Select at least one face feature" |
| Unknown feature, or feature not allowed for the type | "Feature X is not available for Y cameras" |
| `id_card_extract` before Phase B ships | Rejected: "ID card extract is coming soon and cannot be enabled yet" |
| `vehicle` | `features` saved as `[]` |

`direction` stays on every camera (used by face attendance too). `gate_mode` and the ROI/line in `anpr_geometry` are only used for vehicle cameras in v1.

### API payload

`GET` / `PUT /scheduler/sites/<site_id>/cameras/` and `GET /scheduler/cctv/live-cameras/` gain two keys:

```json
{
  "name": "Main door",
  "rtsp_url": "…",
  "camera_type": "face",
  "features": ["face_attendance"],
  "direction": "toggle",
  "gate_mode": "parked_toggle",
  "is_enabled": true,
  "anpr_geometry": {}
}
```

Old clients that don't send the keys → `vehicle` / `[]`, so they keep working.

### Web UI (Organisation → Site → CCTV cameras, `SiteCctvCamerasPanel.jsx`)

```
Name  [__________]      RTSP URL [______________]
Camera type   (•) Vehicle   ( ) Face

-- Vehicle selected --
Direction [Both / Entry only / Exit only]
Gate mode [Whenever plate is seen / Only when crossing line]
ROI + line editor                                   (all as today)

-- Face selected --
Features   [x] Face attendance
           [ ] ID card extract  (coming soon — disabled)
Direction [Both / Entry only / Exit only]
Hint: mount at head height, 2–4 m from the door, faces ≥ 80 px wide
```

- Camera list shows a small badge: **Vehicle** / **Face**.
- Live view shows all camera types (unchanged).

---

## 6. Face attendance (Phase A)

### Kiosk vs CCTV

| Kiosk today | CCTV face attendance |
|-------------|----------------------|
| One person stands still | People walk past; one or several faces in a frame |
| Phone uploads a photo + GPS | RTSP stream from site camera |
| Geofence check | Site/location comes from the camera (no GPS) |
| One encode per request | Each person is encoded **once per walk-past**, not every frame |

### Pipeline

```
Face camera (feature face_attendance)
  → RTSP frames, sampled (~5 fps)
  → fast face detector on the whole frame (all faces)
  → track each face across frames (same idea as visitor/anpr/tracker.py)
  → per track: keep best crop (size, sharpness, frontal)
  → track done (left view / enough frames)
       → dlib encoding of the best crop (box already known → no HOG)
       → identify_user_in_location(camera.site.location, encoding)  (existing FAISS)
       → no match / weak match → log only, no punch
       → match → cooldown check (site + user)
               → shift rule (setting)
               → direction: camera.direction in / out / toggle
               → punch via existing kiosk punch helpers
               → save event + snapshot
```

Group in one frame = several tracks at once; each track goes through the same steps independently.

### Speed / server load

| Step | Choice | Why |
|------|--------|-----|
| Face detection | **OpenCV YuNet** (`cv2.FaceDetectorYN`, ONNX, ~300 KB model) | Fast on CPU, finds many faces per frame. No new Python package (OpenCV already installed) |
| Encoding | Existing dlib `face_recognition.face_encodings(img, known_face_locations=…)` | Same 128-d vectors as stored `User.face_encoding` → no re-enrolment |
| Matching | Existing FAISS `identify_user_in_location` | Same thresholds as kiosk |

Server is 4 vCPU / 8 GB and ANPR already uses ~2 cores. v1 = one face camera per site. Measure CPU / RAM on live with the real camera before adding more.

### Accuracy guards

- Minimum face size (default 80 px wide); smaller faces are tracked but not encoded.
- Use kiosk **strict** tolerance (0.38) + margin check; no relaxed second pass on CCTV.
- Optional vote: same user matched on 2 crops of the track before punching (setting).
- Unknown faces never create attendance.

### Direction

| Camera `direction` | Behaviour |
|--------------------|-----------|
| `in` | Only check-in (no punch if already checked in) |
| `out` | Only check-out (no punch if no open session) |
| `toggle` | Open session → check-out, else check-in (cooldown prevents flip-flop) |

Line-cross direction for faces (like vehicle `line_direction`) → later, only if needed.

### Cooldown

- Key `cctv-face:cooldown:{site_id}:{user_id}`, default **300 s** (`FACE_CCTV_COOLDOWN_SEC`).
- Longer than vehicle (60 s) because a person may stand near the door for minutes.

### Rules reused from kiosk

| Rule | Source |
|------|--------|
| Location must have `is_face_attendance_enabled` | `Location` |
| User must have a face enrolled | `User.face_encoding` / face index |
| Shift today required | Setting `FACE_CCTV_REQUIRE_SHIFT` (default **true**, same as kiosk; client decides later) |
| Check-in / check-out rows | `kiosk_attendance_fast.py`, `attendance_resolve.py` |

### Event log (new model, for review / debugging)

`CctvFaceEvent`

| Field | Notes |
|-------|-------|
| `site` | FK LocationSite |
| `camera` | FK SiteCamera, `SET_NULL` (ids change on camera save) |
| `camera_name` | Copy of name |
| `user` | FK User, null when unknown |
| `distance` | Match distance |
| `result` | `checkin` / `checkout` / `cooldown` / `no_shift` / `already_in` / `no_open_session` / `unknown` |
| `snapshot` | Face crop (optional; `FACE_CCTV_SAVE_UNKNOWN` decides for unknown faces) |
| `created_on` | — |

Screens for this log → decide later (admin / Django admin is enough for v1 testing).

### Runtime

- New management command `run_face_reader` (separate from `run_anpr_reader`).
- New systemd service `patrol-face-reader` with the same limits style as ANPR (`OMP_NUM_THREADS=2`, `MemoryHigh` / `MemoryMax`, `CPUWeight=50`).
- Reads only cameras with `camera_type=face` and `face_attendance` in `features`; refreshes the camera list periodically like the ANPR reader.
- Detection, tracking, encoding and punching all in this one process (single camera → no Redis image hand-off needed).

### Settings (planned)

| Setting | Default |
|---------|---------|
| `FACE_CCTV_FPS` | 5 |
| `FACE_CCTV_MIN_FACE_PX` | 80 |
| `FACE_CCTV_TOLERANCE` | 0.38 |
| `FACE_CCTV_VOTES` | 1 (2 = stricter) |
| `FACE_CCTV_COOLDOWN_SEC` | 300 |
| `FACE_CCTV_REQUIRE_SHIFT` | true |
| `FACE_CCTV_SAVE_UNKNOWN` | false |
| `FACE_CCTV_MAX_CAMERAS` | 1 |

### Camera placement (ops — matters more than code)

- Head height (≈ 1.8–2.2 m), facing the walking direction, 2–4 m from the door.
- Faces ≥ 80 px wide in the image (1080p at a door: OK; wide-angle ceiling CCTV: will mostly fail).
- Avoid strong backlight (glass door with sun behind people).

---

## 7. ID card extract (Phase B — design later)

Not designed yet; the use case must be confirmed first.

Known constraint: card text is far too small to read from a walk-by CCTV frame. It is only realistic at a **desk / reception camera** where the person holds the card close.

Candidate (to discuss):

```
Reception face camera (feature id_card_extract)
  → visitor holds ID card to camera
  → detect card → RapidOCR (reuse visitor AI v2 type=id pipeline)
  → name + IC number (+ face snapshot from same camera)
  → create / pre-fill visitor entry for guard to confirm
```

Until then: checkbox shown as disabled "coming soon" (or hidden) and rejected by the API.

---

## 8. Delivery phases

| Phase | Goal | Depends on |
|-------|------|------------|
| **0** | Camera type + features: model, API, web UI; ANPR reader skips face cameras | — |
| **A** | Face attendance walk-by (single camera, single + group faces) | Phase 0 |
| **B** | ID card extract | Use case decision |
| **C** | Hardening: placement guide, thresholds from live data, event log screen, more cameras if CPU allows | A |

### Phase 0 — Camera settings ✅ DONE (local, 2026-10-05)

- [x] Migration `scheduler/0031_sitecamera_camera_type_features`: `camera_type` (default `vehicle`), `features` (default `[]`)
- [x] Model: `SiteCamera.CameraType`, `SiteCamera.Feature`, `FEATURES_BY_TYPE`, `UNAVAILABLE_FEATURES`, `has_feature()`
- [x] Serializer + validation (§5); `PUT` view saves both fields; `_camera_live_payload` returns both
- [x] ANPR reader `load_cameras`: `filter(is_enabled=True, camera_type="vehicle")`
- [x] Web `SiteCctvCamerasPanel.jsx`: camera type radio (Vehicle / Face) on each camera card; Face shows feature checkboxes instead of "When to record"; ID card extract shown disabled "coming soon"
- [x] Web `SiteFormPage.jsx`: blocks save when a Face camera has no feature
- [x] Web `CctvLiveView.jsx`: "ANPR zone" editor only for vehicle cameras
- [x] Web `AnprGeometryEditor.jsx`: saving a zone now keeps `camera_type`, `features` **and `gate_mode`** of every camera (it used to reset `gate_mode` to `parked_toggle` for all cameras of the site — pre-existing bug fixed)
- [x] Tests `scheduler/tests.py` (9, `SimpleTestCase`): default vehicle, face needs feature, vehicle rejects face feature, ID card rejected, unknown type/feature, old client payload, live payload, reader filter

**Done when:** admin can save a Face camera with Face attendance; existing vehicle cameras keep working; ANPR reader does not open Face cameras.

**Deploy (live):**

```bash
cd /path/to/patrol_backend && git pull
../gtms_venv/bin/python manage.py migrate scheduler
sudo systemctl restart patrol_backend dapne_patrol_backend patrol-anpr-reader
# web: build + release GTMS_NEw (Node 20)
```

Until Phase A ships, a Face camera only gives live view (no attendance yet).

Note: `makemigrations scheduler --dry-run` also shows an old unrelated diff on `SiteCamera.direction` (choices/help text); intentionally not included.

### Phase A — Face attendance

- [ ] YuNet model file + loader (`utils/face_cctv/detector.py` or similar)
- [ ] Face tracker + best-crop selection
- [ ] Encode + `identify_user_in_location` + guards (§6)
- [ ] Cooldown, direction, shift setting, punch via kiosk helpers
- [ ] `CctvFaceEvent` model + migration
- [ ] `run_face_reader` command + systemd unit with limits
- [ ] Structured timing logs (`[FACE_CCTV]`)
- [ ] Tests with mocks (no DB): direction, cooldown, unknown face, shift rule
- [ ] Live test: one person, then 2–3 people together, then repeat within cooldown

**Done when:**
- Registered person walking past → one check-in (or check-out).
- 2–3 registered people together → each gets their own punch.
- Same person again within cooldown → no extra punch.
- Unknown face → no attendance, event logged as `unknown`.
- ANPR timing on the same server is not noticeably worse.

### Phase B — ID card extract

- [ ] Confirm use case (§7)
- [ ] Then write its own section / plan

### Phase C — Hardening

- [ ] Tune thresholds from `CctvFaceEvent` data
- [ ] Placement guide in ops doc
- [ ] Event list screen (if wanted)
- [ ] Raise `FACE_CCTV_MAX_CAMERAS` only after CPU check

---

## 9. Out of scope (for now)

- The other AI features (people count, crowd, PPE, fire/smoke, weapon, missing object, idle, fall, shouting, conveyor count, restricted area, gender/age)
- Changing vehicle camera behaviour
- Kiosk UI / API changes
- Visitor face recognition (only employees are in the face index)
- GPU / bigger server (revisit after Phase A live measurements)

---

## 10. Codebase anchors

| Area | Path |
|------|------|
| Camera model | `scheduler/models.py` → `SiteCamera` |
| Camera API | `scheduler/views_cameras.py`, `scheduler/serializers.py` |
| Camera UI | `GTMS_NEw/src/pages/organisation/components/SiteCctvCamerasPanel.jsx` |
| ANPR reader (camera refresh) | `visitor/anpr/reader.py` |
| Tracker ideas | `visitor/anpr/tracker.py` |
| Face identify | `patrol_backend/utils/face_index.py` → `identify_user_in_location` |
| Face encode helpers | `patrol_backend/utils/face_utils.py` |
| Kiosk punch | `patrol_backend/utils/kiosk_attendance_fast.py` |
| Attendance rows | `patrol_backend/utils/attendance_resolve.py` |
| ID OCR (Phase B) | Visitor AI v2 `type=id` |

---

## 11. Open items

1. **Shift rule** for CCTV attendance — decide later (default = same as kiosk).
2. **ID card extract** use case — decide before Phase B.
3. `id_card_extract` before Phase B: reject in API (recommended) or store and ignore.
4. Snapshot of unknown faces: save or not (privacy) — default off.

---

## 12. Summary

| Camera type | Feature | Detects | Identifies | Writes | Phase |
|-------------|---------|---------|------------|--------|-------|
| Vehicle | (ANPR) | Plate | Plate string | VisitorEntry in/out | exists |
| Face | Face attendance | Faces (one or group) | Registered employee | Attendance check-in/out + event log | 0 → A |
| Face | ID card extract | ID card at desk | Name / IC from OCR | Visitor entry (TBD) | B |
