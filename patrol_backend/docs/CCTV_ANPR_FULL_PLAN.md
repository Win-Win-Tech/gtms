# GTMS CCTV ANPR — Full Detail Plan & Flow

**Document type:** Design / implementation plan only (no code in this track)  

**Server:** 4 vCPU / 8 GB RAM, CPU-only, GTMS already running  

**Last updated:** 2026-09-09

---

## Beginner guide — read this first

If the rest of the document feels heavy, this section alone is enough to understand the plan.

### The goal in one sentence

When a car comes in front of the CCTV, the system should **read the number plate** and **check the vehicle in or out** in GTMS — automatically.

### Three separate programs (do not mix them)

Think of **three different workers** on the same server. They are **not** the same thing.

```
┌──────────────────┐     ┌──────────────────┐     ┌────────────────────────────┐
│ 1. MediaMTX      │     │ 2. ANPR Reader   │     │ 3. Celery                  │
│                  │     │                  │     │                            │
│ Job: show LIVE   │     │ Job: WATCH       │     │ Job: HEAVY work later      │
│ video in browser │     │ camera, decide   │     │ - anpr queue = OCR+gate    │
│ (what you see on │     │ “a car is here”, │     │ - celery queue = emails,   │
│  CCTV Live page) │     │ send ONE photo   │     │   reports, alerts          │
└──────────────────┘     └──────────────────┘     └────────────────────────────┘
```

| Name | Is it Celery? | Is it MediaMTX? | What it does |
|------|---------------|-----------------|--------------|
| **MediaMTX** | No | Yes | Only for **live viewing** in the browser |
| **ANPR Reader** | **No** — separate always-on process (like a small daemon / `manage.py` command under systemd) | No | Continuously reads the camera, watches ROI, tracks car, sends photo when needed |
| **Celery** | Yes | No | Background job runner. For ANPR it only does **OCR + save check-in/out** when Reader gives it work |

**ANPR Reader is NOT MediaMTX and NOT Celery.**  
MediaMTX = TV screen.  
Reader = security guard watching the gate.  
Celery = office clerk who reads the plate number and writes it in the register (slow, careful work).

---

### Step-by-step story (one car)

**Step 1 — Camera is always sending video**  
IP camera streams RTSP (same URL you saved on the site).

**Step 2 — Two things can use that stream**
- MediaMTX → your **CCTV Live** page (humans watch).
- ANPR Reader → automation (computer watches). Same camera, two consumers.

**Step 3 — Reader looks only inside the ROI**  
ROI = “Region Of Interest” = a **box drawn on the video** (gate lane only).  
We ignore trees, sky, far road.  
How we “draw” it in v1: save coordinates in config (e.g. rectangle: top-left and bottom-right as % of frame). Later we can add a UI to draw the box; plan does not require fancy UI on day one.  
Example: “only look at the bottom-center 40% of the image.”

**Step 4 — Reader does NOT look at every video frame for OCR**  
Camera may send ~25 pictures per second. That is too many.  
About **1 time per second**, Reader takes the **latest** picture and asks: “Is there a plate/vehicle **inside the ROI**?”  
(Simple detection — not full number reading yet.)

**Step 5 — “Vehicle crossed / entered ROI” means**  
Not magic. It means:
- detection finds a plate/vehicle box **inside** the ROI, and
- tracker says “this is the same car for a few looks in a row” (stable),  
optional later: car crossed a virtual line inside the ROI.  

If the car is **outside** the ROI box → ignore.  
If the same car stays parked in ROI for 10 minutes → process **once**, then ignore (cooldown).

**Step 6 — When Reader is sure → pick ONE good photo**  
From the last few looks, pick the sharpest / largest plate photo.  
Save it as a **JPEG in memory** (or a short temp file).  
**Do not** save every video frame to disk.

**Step 7 — Reader puts a job in a queue**  
Like putting one envelope on a tray:
- “Camera X, Site Y, this JPEG, time = now”

That tray lives in **Redis** and is named queue **`anpr`**.  
We **add this queue** (GTMS today only has the default `celery` queue for emails/reports).

**Step 8 — Celery worker for `anpr` picks jobs one by one**  
A Celery worker process is started like:  
“only listen to queue `anpr`, and do **1 job at a time**.”  

It does **not** watch the camera.  
It does **not** scan all cameras.  
It only:
1. Sees if any job is waiting on `anpr`
2. Takes the oldest waiting job
3. Runs plate YOLO + RapidOCR (read number)
4. Creates check-in or check-out in the database
5. Goes idle until the next job

**Step 9 — Other Celery work is a different tray**  
Report emails etc. stay on queue name **`celery`**.  
Same Redis “office”, **two trays**:
- tray `anpr` → plate reading  
- tray `celery` → normal GTMS background tasks  

So yes: Celery can do “other jobs”, but those are **not** “check if ROI was crossed.” ROI watching is **only** the Reader.

---

### Queue questions (simple answers)

| Question | Answer |
|----------|--------|
| Do we add a new queue? | **Yes** — name: `anpr` |
| Does Celery check the camera? | **No** |
| Does Celery check all queued items? | It processes the `anpr` tray **one by one** (limit: 1 at a time on this server) |
| Is there a limit? | **Yes** — max ~5–10 waiting jobs; if too many, Reader **stops adding** new ones; very old jobs are thrown away (stale) |
| Why a limit? | Server is small (4 CPU / 8 GB). Unlimited queue = old photos of cars that already left → wrong check-in/out |
| What is passed in the queue? | **One JPEG + ids** (camera, site, time) — not the live video stream |

---

### Tiny diagram of data handoff

```
Camera video
    │
    ├──► MediaMTX ──► Browser (live view only)
    │
    └──► ANPR Reader (always on)
              │
              │  every ~1 second: look inside ROI
              │  car stable? ──no──► do nothing
              │               │
              │              yes
              │               ▼
              │         one JPEG + info
              │               │
              │               ▼
              │         Redis queue "anpr"  (small waiting list)
              │               │
              │               ▼
              │         Celery worker (-c 1)
              │               │
              │               ▼
              │         Read plate → Check-in or Check-out in GTMS DB
```

---

### What we build in phases (plain)

| Phase | In human words |
|-------|----------------|
| **0** | Switches and model files ready |
| **3A** | Make existing plate OCR callable from a photo (not only Manual Entry upload) |
| **3B** | Build the **Reader** (camera watch + ROI/line + track + send photo) |
| **3C** | Build the **`anpr` Celery** worker (read plate from that photo) |
| **4** | Write check-in/out into Visitor entries |
| **5** | Show CCTV badge / plate nicely in the UI |

---

## Beginner: Adding a second camera (very important)

### Short answer

| Need another…? | Answer |
|----------------|--------|
| Another **MediaMTX**? | **No** — same MediaMTX; add another path/camera |
| Another **ANPR Reader process**? | **No for 2 cams** — **one** Reader can watch both cameras in a loop |
| Another **Celery / another `anpr` queue**? | **No** — **one** shared `anpr` queue + **one** OCR worker (`-c 1`) for all cameras |
| Another **Redis**? | **No** |

### What actually happens with 2 cameras

```
Camera A (Test site) ──┐
                       ├──► ONE ANPR Reader process
Camera B (Other site) ─┘         │
                                 │ when either cam has an event
                                 ▼
                         ONE Redis queue "anpr"
                                 │
                                 ▼
                         ONE Celery worker (-c 1)
                         (reads plate, check-in/out)
```

**Order of work with 2 cameras:**
1. Reader connects to Camera A and Camera B (both RTSP).
2. Each second (example): check A, then check B (round-robin), or alternate.
3. Each camera has its **own ROI or line** and its own tracks (`track_id` includes camera id).
4. If Camera A fires an event → put job on `anpr` (with `camera_id=A`, `site_id=…`).
5. If Camera B fires while OCR is busy → job waits in the **same** queue.
6. Celery still does **one OCR at a time** (this server is small). Camera B waits a few seconds — that is OK.

### What we do NOT do

- Do **not** start 2 Celery OCR workers just because there are 2 cameras (RAM/CPU will fight GTMS).
- Do **not** create 2 different `anpr` queues per camera for v1.
- Do **not** need 2 MediaMTX servers.

### Limit

- This server: **max 2 cameras** for ANPR.
- Camera 3+ → need a stronger server or a separate ANPR machine.

---

## Beginner: ROI box vs line (admin draws what?)

Both are possible.

| Shape | Meaning |
|-------|---------|
| **Box** | “Only look inside this rectangle” |
| **Line** | “When the car **crosses** this line, create an event” (can use direction: side A→B = in, B→A = out) |

**Recommended:** detection in a box (or full frame if view is tight) + **line to trigger** the event.  
**Admin UI later:** draw line (or box) on **CCTV Live** preview or on **Site → CCTV camera** settings.  
**Day one:** store line/box as numbers in config if UI is not ready.

Guard live TV stays on **MediaMTX** — drawing ROI does not replace MediaMTX.

---

## Beginner: Empty IC / empty name — only vehicle number?

### What you asked
For CCTV check-in/out: store **empty IC**, **empty visitor name**, and only keep the **vehicle number**.

### What the database allows today

`Visitor` model requires:
- `ic_passport_number` — **required** (not blank)
- `visitor_name` — **required** (not blank)

Also unique: **one active visitor per (organisation location + IC)**.

So:

| Idea | Possible? |
|------|-----------|
| Empty IC `""` for every CCTV car | **No (unsafe)** — second car also `""` → **unique constraint error** |
| Empty `visitor_name` | **No** without changing the model (`blank=True`) |
| Only `VisitorEntry.vehicle_number` = plate | **Yes** — plate always stored on the **entry** |
| Fake/synthetic IC so DB is happy | **Yes — this is the plan** |

### What we will do (locked for this plan)

We still show the UI mainly as **vehicle number**, but in DB:

| Field | CCTV value | Why |
|-------|------------|-----|
| `Visitor.ic_passport_number` | `CCTV-TN58AB1234` | Required + unique; not a real passport |
| `Visitor.visitor_name` | `TN58AB1234` (same as plate) | Required; UI can treat this as “name = plate” |
| `VisitorEntry.vehicle_number` | `TN58AB1234` | Real plate used for search/reports |
| `VisitorEntry.entry_source` | `cctv` | Marks auto gate entry |

**For humans / frontend (Phase 5):** show **plate as the title**, hide or de-emphasize the synthetic IC (`CCTV-…`). Guests see “vehicle number”, not a fake IC card.

**If product later requires a truly empty IC:** that needs a **schema change** (nullable IC + unique on plate, or a new identity type). That is extra work and risk; not the default in this plan.

---

## 0. One-page summary

### Problem
We have RTSP CCTV at the gate and an existing **upload-based** plate OCR API (Manual Entry). We need **automatic** vehicle check-in / check-out from the live camera **without** melting the shared 8 GB server or flooding Celery with video frames.

### Approach (short)
1. **Live view stays as today** — MediaMTX → browser (WebRTC/HLS). Unchanged for guards.
2. **Separate ANPR pipeline** — long-lived **Reader** process per camera (or shared multi-cam reader) that:
   - reads RTSP (TCP),
   - applies **ROI**,
   - runs **light detection at 1–2 FPS**,
   - **tracks** the same vehicle,
   - fires an **event** only when ready,
   - sends **one best JPEG** to Celery.
3. **Celery queue `anpr`** (concurrency **1**) runs existing **plate YOLO + RapidOCR** only when the Reader sends an event.
4. **Gate service** maps plate → check-in or check-out (toggle / in / out) with cooldown.
5. **Visitor** created/reused as `CCTV-{PLATE}`; entry gets `entry_source=cctv`.

### Who watches the camera? (Celery does **not**)

**No — Celery does not poll the camera or watch the ROI.**

| Process | Job |
|---------|-----|
| **ANPR Reader** (always-on) | Opens RTSP, applies ROI, detects, tracks, decides “vehicle event”, picks best JPEG |
| **Celery `anpr` worker** | Wakes only when a task arrives → OCR + gate write → idle again |
| **Celery default worker** | Unrelated GTMS jobs (report email, missed checkout, location alerts) on queue `celery` |

Flow in plain words:
1. Reader sees vehicle in ROI and track is stable → pushes **one** task to queue `anpr`.
2. ANPR Celery worker picks that task → OCR → check-in/out.
3. Meanwhile the **same Redis**, but **different queue**, can run other Celery tasks (reports, etc.) on the **default** worker — those are **not** camera jobs.
4. If another vehicle event arrives while OCR is busy, it **waits in `anpr` queue** (or is skipped if backlog/stale rules say so). It is **not** a second parallel OCR on this server (`-c 1`).

### Why not “every 2 seconds run full OCR in Celery”?
Camera ≈ 25 FPS; OCR ≈ 2–3 s on CPU. Putting frames (or even every 2s full OCR) on Celery creates a **stale backlog** and steals CPU from Django. We are **event-driven**: detect often, OCR **rarely**.

### Capacity
| Cameras on this box | Guidance |
|---------------------|----------|
| **1** | Safe pilot |
| **2** | Allowed (e.g. test site + another site) if detect ≤1 FPS each, **one** OCR worker shared |
| **3+** | Not on this server without more hardware |

Env ceiling: `ANPR_MAX_CAMERAS=2`.

---

## 1. Locked product decisions

| Topic | Decision |
|--------|----------|
| End-state | Phases 3–5: OCR + auto check-in/out + list/report UX |
| Visitor identity | DB: `ic_passport_number = CCTV-{PLATE}`, `visitor_name = plate`. Entry: `vehicle_number = plate`. UI shows plate (not real IC). Empty IC for all cars is **not** used (unique constraint). |
| Plate storage | `VisitorEntry.vehicle_number` |
| Entry source | New value `cctv` (additive schema) |
| Host / photos | CCTV: **no host**, **no required photos**; optional evidence JPEG |
| Checkout photo | Manual checkout still requires `exit_photo`; **CCTV checkout bypasses** that via internal gate path |
| Camera modes | `toggle` / `in` / `out` on `SiteCamera.direction` |
| Check-in vs check-out | **Primary:** open-entry toggle (no open → IN; open → OUT). Line-crossing direction can assist. Not front/rear CNN in v1 |
| Trigger geometry | Prefer **virtual line** (optional box for detect area). Admin draws later on CCTV Live or site camera settings |
| Cross-site | Per-site open state (site B can check in while A still open) |
| Never left | Stay open (no auto-checkout timeout) |
| OCR engine | Reuse **RapidOCR v2 + plate YOLO** (`visitor/ai/pipeline_v2.py`) |
| Do not use for CCTV | PaddleOCR (RAM too high on 8 GB) |
| Live view | Separate from ANPR; MediaMTX continues for guard TV |
| Max cameras (this host) | **2** — one Reader, one `anpr` Celery worker shared |

---

## 2. Building blocks (reuse vs new)

### Reuse from current GTMS
| Piece | Where |
|--------|--------|
| `SiteCamera` (RTSP, direction, stream_path) | `scheduler/models.py` |
| MediaMTX live WebRTC/HLS | Live view pipeline |
| Upload plate OCR API | `POST /visitors/ai/extract-v2/` |
| Plate detect + RapidOCR | `visitor/ai/detect.py`, `pipeline_v2.py`, `rapid_ocr_engine.py` |
| Plate parse (IN/MY) | `visitor/ai/parse.py` |
| Celery + Redis | `CELERY_BROKER_URL=redis://localhost:6379/0` |
| Visitor check-in/out models | `visitor/models.py` |
| Vehicle movement **report** | Aggregates entries by check-in/out times (not a separate gate model) |

### New for ANPR (Phases 3–5)
| Piece | Notes |
|--------|--------|
| RTSP frame reader for ANPR | No OpenCV/ffmpeg grabber in app today |
| ROI / tracker / event builder | New |
| Celery queue `anpr` | Today: single default queue only |
| `entry_source=cctv` | Only `manual` / `invitation` today |
| Auto gate service | New |
| CCTV badge / plate-primary UI | Phase 5 |

### Important correction
Manual Entry v2 is **plate YOLO → plate crop → RapidOCR**, not “vehicle YOLO then OCR only”. CCTV must reuse that plate path.

---

## 3. Big-picture architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         SAME PHYSICAL CAMERA                              │
│  rtsp://user:pass@cam/...                                                │
└─────────────┬───────────────────────────────┬───────────────────────────┘
              │                               │
              ▼                               ▼
   ┌─────────────────────┐         ┌──────────────────────────┐
   │ MediaMTX (existing) │         │ ANPR Reader (new)        │
   │ Live for browser    │         │ systemd / manage.py      │
   │ WebRTC :8889 / HLS  │         │ RTSP TCP, ROI, detect,   │
   └──────────┬──────────┘         │ track, event → Celery    │
              │                    └────────────┬─────────────┘
              ▼                                 │
        CCTV Live UI                            │ only BEST JPEG + meta
                                                ▼
                                   ┌────────────────────────┐
                                   │ Redis queue: `anpr`    │
                                   │ Celery worker -c 1     │
                                   └────────────┬───────────┘
                                                ▼
                                   ┌────────────────────────┐
                                   │ OCR: plate YOLO +      │
                                   │ RapidOCR (reuse v2)    │
                                   └────────────┬───────────┘
                                                ▼
                                   ┌────────────────────────┐
                                   │ Gate service           │
                                   │ toggle / in / out      │
                                   │ cooldown + duplicates  │
                                   └────────────┬───────────┘
                                                ▼
                                   ┌────────────────────────┐
                                   │ Visitor + VisitorEntry │
                                   │ entry_source = cctv    │
                                   └────────────────────────┘
```

**Two consumers of one camera are intentional:**
- MediaMTX = humans watching live  
- ANPR Reader = automation  

They must not share one process that blocks the UI.

---

## 4. Full ANPR runtime flow (order-wise)

This is the **correct ordered pipeline** from camera to DB.

### Step 1 — Start / load config
1. Process starts (`run_anpr_reader` or systemd unit).
2. Load env: `ANPR_ENABLED`, `ANPR_MAX_CAMERAS` (≤2), `ANPR_DETECT_FPS`, `ANPR_COOLDOWN_SEC`, ROI config.
3. Load enabled `SiteCamera` rows (up to max cameras), each with `rtsp_url`, `site_id`, `direction`, `stream_path`.
4. Warm models **once** in the OCR worker (Celery child), not on every frame. Reader may run lighter detect or share weights carefully — prefer OCR models only in Celery worker to avoid double RAM; reader can run a small plate/motion pass or call a thin detect if memory allows. **Design choice for implementation:** Reader does motion + plate-box detect at low FPS; full OCR only in Celery (may re-run plate YOLO for crop quality — acceptable at event rate).

### Step 2 — Open RTSP (per camera)
1. Connect with **TCP** transport (same as MediaMTX / `ffplay -rtsp_transport tcp`).
2. On failure: exponential backoff reconnect; clear in-memory tracks for that camera; do not invent events.
3. Continuously grab frames into a **latest-frame slot** (always overwrite).  
   **Never** queue every frame.

### Step 3 — Frame policy (in memory only)
1. Maintain:
   - `latest_frame` (always newest),
   - short **ring buffer** (e.g. last 1–2 seconds of sampled frames) for best-frame selection.
2. **Do not** write every frame to disk.
3. Disk write only later for **optional evidence** on successful check-in/out.

### Step 4 — ROI (region of interest)
1. Each camera has a configurable ROI (polygon or rectangle) in normalized coordinates (0–1) or pixels.
2. Default for pilot: **full frame** or a gate lane box configured in admin/env JSON.
3. All detection runs **inside ROI crop** (or masks outside ROI).
4. Purpose:
   - Ignore parking / road background,
   - Reduce YOLO cost,
   - Define where a vehicle “counts” for the gate.

```
Full frame
┌────────────────────────────┐
│  sky / trees (ignored)     │
│    ┌──────────────────┐    │
│    │   ROI = gate lane │    │
│    │   detect here     │    │
│    └──────────────────┘    │
└────────────────────────────┘
```

### Step 5 — Sample rate (not every frame)
1. Wall-clock throttle: process detect at **`ANPR_DETECT_FPS`** (default **1**, max **2** per camera).
2. Between ticks: still read RTSP (to avoid buffer buildup) but **drop** frames without inference.
3. If two cameras: round-robin or parallel threads with **shared OCR queue** still concurrency 1.

### Step 6 — Presence / plate detection (cheap, frequent)
On each detect tick:
1. Resize ROI crop (e.g. long side ~640–800).
2. Run **plate detector** (preferred) and/or light motion check.
3. Outputs: list of boxes `{x1,y1,x2,y2, conf}` inside ROI.
4. If **no** boxes → update tracker with “miss”; maybe delete lost tracks; **stop** (no Celery).

### Step 7 — Tracking (same vehicle across frames)
1. Use a **simple IoU / centroid tracker** (v1 — not ByteTrack required).
2. Match current boxes to existing `track_id`s.
3. New box without match → new `track_id`.
4. Track state examples: `CANDIDATE` → `STABLE` → `OCR_QUEUED` → `COMMITTED` → `COOLDOWN` → `LOST`.
5. Purpose: vehicle visible for 2 seconds at 1 FPS ≈ 2 detections → **one** track, not two OCR jobs.

### Step 8 — Event trigger (when to care)
Fire an ANPR **event** only when **all** are true:
1. Track is **STABLE** (seen ≥ N frames, e.g. 2–3),
2. Best plate box conf ≥ threshold,
3. Track not already `OCR_QUEUED` / `COMMITTED` / in cooldown,
4. Global/camera OCR queue not saturated (backpressure),
5. Optional: box center crossed a virtual **line** inside ROI (entry/exit bias) — optional enhancement; **v1 can rely on toggle only**.

**Not an event:** every detection frame, every 2 seconds blindly, parked car already committed.

### Step 9 — Best-frame selection (before Celery)
From ring buffer frames belonging to this `track_id`, score candidates:
- plate box **area** (larger usually easier OCR),
- detector **confidence**,
- **sharpness** (e.g. Laplacian variance),
- optional brightness (reject too dark).

Pick **one** best frame → encode **JPEG** in memory.

### Step 10 — Enqueue Celery (what goes on the queue)

**PUT ON QUEUE:**
```json
{
  "camera_id": "...",
  "site_id": "...",
  "location_id": "...",
  "track_id": "cam1-42",
  "direction_mode": "toggle",
  "captured_at": "ISO-8601",
  "jpeg_path_or_bytes_ref": "...",
  "detect_meta": { "box": [...], "conf": 0.81 }
}
```

**DO NOT PUT ON QUEUE:**
- Raw 25 FPS frames  
- Numpy arrays every tick  
- Duplicate events for same `track_id`  
- Frames older than freshness limit  

Mark track `OCR_QUEUED` immediately so Step 8 will not double-fire.

### Step 11 — Celery worker (`anpr` queue)
1. Dedicated worker:  
   `celery -A patrol_backend worker -Q anpr -c 1 --prefetch-multiplier=1`
2. Task `process_anpr_frame`:
   - If `now - captured_at` > ~10s → **reject** (stale).
   - Decode JPEG → BGR.
   - Call shared **`extract_vehicle_from_bgr`** (same logic as extract-v2 plate pipeline).
   - Soft/hard time limits (~30s / ~45s).
3. If OCR `found=false` → optional 1–2 retries with alternate buffered frames; else log failure; release track to allow later retry only if still present and cooldown allows.
4. If OCR `found=true` → normalize plate string → call **Gate service**.

### Step 12 — Gate service (check-in / check-out)
1. Normalize plate (uppercase, strip spaces; existing IN/MY validators).
2. Upsert `Visitor` (cannot use empty IC — see Beginner section):
   - `ic_passport_number = "CCTV-" + plate` (synthetic, unique)
   - `visitor_name = plate` (required field; UI shows plate)
   - scoped to camera site’s organisation `location`
3. Always set `VisitorEntry.vehicle_number = plate` (this is the real plate field).
4. Load camera `direction`:
   - **`in`:** only check-in if no open entry at this site for this visitor/plate; else ignore.
   - **`out`:** only check-out if open entry exists; else ignore.
   - **`toggle`:** no open → **CHECK-IN**; open → **CHECK-OUT**.
   - If virtual **line** configured: crossing direction can confirm in vs out.
5. Open entry = `VisitorEntry` with `status=checked_in` for that site + visitor (per-site rule).
6. **Cooldown** `(site_id, plate)` for `ANPR_COOLDOWN_SEC` (default 60) after any successful event.
7. CHECK-IN create:
   - `entry_source=cctv`
   - `status=checked_in`
   - `vehicle_number=plate`
   - `vehicle_type` default e.g. `unknown` or `car` (so vehicle movement report does not drop it)
   - no host; no mandatory photos
8. CHECK-OUT update:
   - `status=checked_out`, set `check_out_time`
   - **no** `exit_photo` requirement
9. Optional: save evidence JPEG as `VisitorAsset`.
10. Mark track `COMMITTED` → then `COOLDOWN` until track lost + timer elapsed.

### Step 13 — Duplicate / parked-car protection
| Situation | Handling |
|-----------|----------|
| Same car in ROI for minutes | One event; track COMMITTED; ignore until lost + cooldown |
| OCR reads `TN58AB1234` then `TN58AB1Z34` | Fuzzy normalize / similarity; prefer higher conf; cooldown on committed plate |
| Two cars close | Separate tracks; prefer larger/closer-to-line box |
| Reader restart | In-memory tracks cleared; DB open state + cooldown prevent double IN |
| Celery backlog | Reader stops enqueueing; drops new events (prefer miss over wrong stale OUT) |

### Step 14 — Observability
Log structured lines: `camera_id`, `track_id`, `event`, `plate`, `action`, `elapsed_ms`, `queue_depth`.  
Metrics later: events/hour, OCR fail rate, CPU%.

---

## 5. Celery / Redis design (detail)

### RabbitMQ — do we need it?

**No. Do not add RabbitMQ for ANPR.**

| Option | Use for this project? |
|--------|------------------------|
| **Redis** (already used by GTMS Celery) | **Yes — best** |
| **RabbitMQ** | **No** — extra install, ops, and no real benefit here |

**Why Redis is best here:**
1. GTMS Celery is **already** `CELERY_BROKER_URL=redis://localhost:6379/0`.
2. ANPR sends **few** jobs (one JPEG per car event), not millions of messages.
3. We only need a simple queue named `anpr` next to the default `celery` queue — Redis handles that.
4. Adding RabbitMQ means another service to install, monitor, and fail — on an 8 GB box that already runs Django, Redis, MediaMTX, MySQL, etc.

**When RabbitMQ would matter:** huge multi-service messaging, complex routing, very high throughput. That is not this gate ANPR design.

**Plan:** keep **Redis only** as Celery broker; add queue name `anpr` on the same Redis.

### Important: Celery is not the camera watcher

The **Reader** owns the camera loop (RTSP → ROI → detect → track → event).  
**Celery only runs jobs that were already decided.**

```
Reader (continuous)          Celery anpr worker (on demand)
─────────────────────        ──────────────────────────────
read RTSP forever            idle…
ROI + detect @ 1 FPS
track vehicle
event? ──JPEG──► Redis anpr ──► wake → OCR → gate → DB → idle again
```

Other GTMS Celery tasks (emails, alerts) use queue **`celery`**, not `anpr`. Same broker, different workers/queues — they do **not** mean “another camera task.”

### Current GTMS Celery (today)
- Broker: Redis db0  
- **One** default queue `celery`  
- Tasks: report emails, missed checkout, location-missing, media cleanup  
- Visitor OCR today is **HTTP**, not Celery  

### Target for ANPR

| Setting | Value |
|---------|--------|
| Queue name | `anpr` |
| Routing | `visitor.anpr.tasks.*` → `anpr` |
| Concurrency | **1** |
| Prefetch | **1** |
| Task expiry | yes (avoid zombie jobs) |
| Stale frame | reject if age &gt; threshold |
| Max pending | small (e.g. 5–10); drop oldest / skip enqueue |
| Isolation | Separate worker process from default queue |

```
Default worker:  -Q celery        (reports, alerts)
ANPR worker:     -Q anpr -c 1     (OCR + gate only)
```

### Backpressure algorithm (order)
1. Before enqueue, Reader checks approximate queue length / “OCR busy” flag.
2. If busy or depth ≥ max → **do not enqueue**; keep tracking; try again when track still stable and queue free (or skip this pass).
3. Never buffer hundreds of JPEGs on disk.

### What happens if OCR slower than cars?
- Cars queue briefly (seconds), not minutes.
- Stale tasks die.
- Worst case: miss a plate rather than check out the wrong (old) car.

---

## 6. Check-in / check-out logic (examples)

### Example A — Toggle camera (typical single gate)
1. `TN58AB1234` approaches first time → no open entry → **CHECK-IN**  
2. Same plate still in view → cooldown / committed track → **ignore**  
3. Later same plate returns (or leaves and returns) → open entry exists → **CHECK-OUT**  

### Example B — Direction `in` only
- Only creates check-ins; never closes.

### Example C — Direction `out` only
- Only closes open entries; never opens.

### Why not front/rear CNN in v1?
Night + distance + your test FOV make appearance class unreliable. Toggle + cooldown matches existing `SiteCamera.direction` product model. Front/rear can be a later optional signal.

---

## 7. Night / low light (expectations)

| Item | Plan |
|------|------|
| Current night FOV | Plate often too small/dark — OCR may fail |
| Software | Multi-frame best pick, CLAHE optional, conf threshold, retries |
| Hardware | IR / better aim / closer plate FOV helps more than GPU |
| Product | Log failures; do not invent plates |

---

## 8. Failure matrix

| Failure | System behaviour |
|---------|------------------|
| RTSP disconnect | Reconnect; clear tracks; no fake events |
| YOLO/OCR exception | Catch; log; no DB write |
| Celery down | Reader runs; enqueue fails/skips; alert |
| Redis down | ANPR pauses; keep GTMS HTTP if possible |
| Django DB down | OCR may finish; gate write fails; retry carefully or drop |
| Server reboot | No replay of old frames; open entries remain in DB |
| Manual Entry OCR same time | Semaphore + single ANPR OCR worker; expect slower AI assist |

---

## 9. Resource rules (4 vCPU / 8 GB)

| Do | Do not |
|----|--------|
| 1–2 cameras max | 25 FPS YOLO |
| Detect 1 FPS (2 max) | Full OCR every 2 s always |
| Celery `-c 1` for `anpr` | Multiple OCR workers |
| RapidOCR only for CCTV | PaddleOCR for CCTV |
| JPEG on event only | Save every frame to disk |
| Separate `anpr` queue | Dump frames on default Celery queue |

Rough costs (from existing benchmark on this class of host):
- Warm RapidOCR vehicle: ~2–3 s  
- RapidOCR RAM delta: ~0.4–0.8 GB  
- PaddleOCR: +3+ GB — **unsafe** with GTMS  

---

## 10. Phase-wise implementation plan

Do **one phase at a time**. Verify before next.

### Phase overview

| Phase | Goal |
|-------|------|
| **1** | Camera CRUD |
| **2** | Live view (MediaMTX) |
| **0** | Prep: env, weights, docs sync |
| **3A** | Shared frame→OCR library |
| **3B** | ANPR Reader (ROI, track, events) log-only |
| **3C** | Celery `anpr` OCR worker |
| **4** | Gate check-in/out + `entry_source=cctv` |
| **5** | List / report UX |
| **Sign-off** | Load tests → enable production |

---

### Phase 0 — Prep (ops / config)

**Goal:** Safe switches and pinned models before coding behavior.

**Deliverables**
- [ ] Env: `ANPR_ENABLED=false` (default off), `ANPR_MAX_CAMERAS=2`, `ANPR_DETECT_FPS=1`, `ANPR_COOLDOWN_SEC=60`, `ANPR_QUEUE=anpr`
- [ ] Pin local plate `.pt` (`VISITOR_AI_PLATE_WEIGHTS` or `visitor/ai/weights/`)
- [ ] Document systemd units for Reader + `anpr` worker
- [ ] Keep this file as the project source of truth for ANPR

**Exit criteria:** Weights load offline; flags documented.

---

### Phase 3A — Library reuse

**Goal:** Same OCR as Manual Entry, callable from a frame (not only multipart upload).

**Deliverables**
- [ ] `extract_vehicle_from_bgr(bgr) -> {found, number, confidence, ...}`
- [ ] Internally same as `_pipeline_vehicle_plate_v2`
- [ ] Upload `extract-v2` API unchanged

**Exit criteria:** Same test image → same plate via API and library.

---

### Phase 3B — ANPR Reader (no gate writes yet)

**Goal:** Prove RTSP → ROI → detect → track → best frame → enqueue (or log) on **1–2 cameras**.

**Deliverables**
- [ ] `run_anpr_reader` management command / systemd
- [ ] RTSP TCP client, latest-frame + ring buffer
- [ ] ROI config per camera
- [ ] Detect @ 1 FPS, simple IoU tracker
- [ ] Event trigger + best-frame JPEG
- [ ] Enqueue stub or write `AnprEventLog` (OCR result optional if 3C not ready)
- [ ] Reconnect + backpressure hooks

**Exit criteria:** Stationary test vehicle does **not** spam events; CPU acceptable for 1–2 hours.

---

### Phase 3C — Celery OCR

**Goal:** Isolated OCR consumer.

**Deliverables**
- [ ] Queue routing `anpr`
- [ ] Task `process_anpr_frame` with stale check + time limits
- [ ] Worker unit `-Q anpr -c 1`
- [ ] Persist OCR outcomes to log table

**Exit criteria:** Queue depth stays ~0 under light traffic; default Celery reports still run.

---

### Phase 4 — Gate (check-in / check-out)

**Goal:** Real visitor entries from plates.

**Deliverables**
- [ ] Migration: `entry_source` choice `cctv`
- [ ] Gate service: synthetic Visitor `CCTV-{plate}` + `visitor_name=plate` + entry `vehicle_number` (not empty IC); toggle/in/out; cooldown; optional line direction
- [ ] CCTV checkout without `exit_photo`
- [ ] Optional evidence asset
- [ ] Wire OCR success → gate

**Exit criteria:**
- First sight → one CHECK-IN  
- Second valid sight after cooldown with open entry → one CHECK-OUT  
- No duplicate IN while parked  

---

### Phase 5 — UX

**Goal:** Operators understand CCTV entries.

**Deliverables**
- [ ] Visitor list: CCTV badge; plate as primary label for `entry_source=cctv`
- [ ] Filter/search works for plates
- [ ] Vehicle movement report includes CCTV rows (`vehicle_type` default set)

**Exit criteria:** QA can demo list + report without DB diving.

---

### Sign-off — Performance gates (before `ANPR_ENABLED=true` in prod)

1. Idle baseline CPU/RAM (GTMS + MediaMTX).  
2. Detect-only 1 FPS × 1–2 cams for 1 hour.  
3. OCR warm latency p50/p95.  
4. Stationary car 10 min → **0** duplicate check-ins after first.  
5. Manual Entry extract-v2 still usable.  
6. Report email beat healthy.  
7. Night sample: document found-rate (go/no-go).  

**Pass:** Free RAM headroom ≳ 1.5 GB; CPU not pegged 100% sustained; queue depth ≈ 0.

---

## 11. Suggested module map (when coding starts)

| Module | Responsibility |
|--------|----------------|
| `visitor/ai/` | Shared OCR library (3A) |
| `visitor/anpr/reader.py` | RTSP, ROI, sample loop |
| `visitor/anpr/tracker.py` | IoU tracks + states |
| `visitor/anpr/events.py` | Trigger + best frame |
| `visitor/anpr/tasks.py` | Celery OCR |
| `visitor/anpr/gate.py` | Check-in/out |
| `visitor/models.py` | `entry_source=cctv` |
| Frontend visitor list | Badge + plate label |
| Docs / systemd | Ops runbooks |

---

## 12. Explicit non-goals (this program)

- 100-site concurrent ANPR on 8 GB  
- GPU requirement for v1  
- Front/rear deep-learning classifier as primary gate signal  
- ByteTrack/BoT-SORT mandatory (simple tracker first)  
- Recording / playback DVR  
- Changing site boundary / SOS  
- Enqueueing every video frame to Celery  
- Replacing Manual Entry upload OCR  

---

## 13. Order checklist (end-to-end happy path)

End-to-end happy path checklist:

1. Camera publishes RTSP.  
2. MediaMTX serves **live view** (unchanged).  
3. ANPR Reader opens **same** RTSP (TCP).  
4. Frames stay in **RAM**; extras dropped.  
5. Crop to **ROI**.  
6. Every **1 s** (example): detect plates in ROI.  
7. **Tracker** assigns `track_id`.  
8. When track **stable** and not cooling down → **event**.  
9. Pick **best JPEG**.  
10. Push **one task** to Redis queue **`anpr`**.  
11. Celery worker (**1 process**) runs **plate YOLO + RapidOCR**.  
12. Gate applies **toggle/in/out** + **cooldown**.  
13. Write **Visitor** (`CCTV-{plate}`) + **VisitorEntry** (`entry_source=cctv`).  
14. UI shows entry with **CCTV badge** / plate title.  
15. Vehicle movement report counts IN/OUT times as today.

---

## 14. Final verdict (capacity)

| Question | Answer |
|----------|--------|
| Feasible on 4 vCPU / 8 GB? | **Yes**, event-driven, RapidOCR, OCR concurrency 1 |
| Biggest bottleneck? | CPU for YOLO+OCR; then camera quality at night |
| Cameras on this server? | **Up to 2** (test + one site), carefully |
| If more sites? | Separate ANPR host or GPU / edge near cameras |

---

*End of document — design only; implementation starts when phases are approved and executed in order.*
