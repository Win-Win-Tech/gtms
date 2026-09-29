# CCTV ANPR — How it works (Reader → Queue → Celery)

Plain-language guide to the vehicle number-plate system: what each part does,
which packages are used and why, and what was changed to improve accuracy
(Sep 2026).

See also:
- [`CCTV_ANPR_OPS.md`](CCTV_ANPR_OPS.md) — systemd services, install steps, troubleshooting
- [`CCTV_ANPR_FULL_PLAN.md`](CCTV_ANPR_FULL_PLAN.md) — original design plan
- [`CCTV_ANPR_GEOMETRY_API.md`](CCTV_ANPR_GEOMETRY_API.md) — ROI / virtual line APIs
- [`OCR_ENGINE_BENCHMARK_REPORT.md`](OCR_ENGINE_BENCHMARK_REPORT.md) — why RapidOCR instead of PaddleOCR

---

## 1. What ANPR does (one paragraph)

A CCTV camera at the gate streams video. The **ANPR Reader** watches the video,
finds number plates, follows each vehicle, and when a vehicle should be recorded
it saves 1–3 photos of the plate and puts a **job** in a **Redis queue**. A
**Celery worker** picks up the job, reads the plate text with OCR, cleans the
number (Indian plate rules), and creates a **CCTV check-in or check-out** in the
Visitors module with the vehicle photo.

---

## 2. Big picture

```text
 CP Plus camera (RTSP, channel=1&subtype=0)
        │  live H.264 video
        ▼
 ┌──────────────────────────────────────────────┐
 │ 1. ANPR READER  (patrol-anpr-reader service) │   always running
 │    python manage.py run_anpr_reader          │
 │    • background thread reads newest frame    │
 │    • plate YOLO finds plates (1–3 per sec)   │
 │    • tracker follows each vehicle            │
 │    • keeps best 3 plate photos per vehicle   │
 │    • decides WHEN to record (line / parked)  │
 └───────────────┬──────────────────────────────┘
                 │ JPEGs in media/anpr_pending/  +  job (JSON)
                 ▼
 ┌──────────────────────────────────────────────┐
 │ 2. QUEUE  Redis list "anpr"                  │   waiting room
 │    max 8 jobs (backpressure)                 │
 └───────────────┬──────────────────────────────┘
                 ▼
 ┌──────────────────────────────────────────────┐
 │ 3. CELERY WORKER (patrol-anpr-celery)        │   one job at a time
 │    task visitor.anpr.tasks.process_anpr_frame│
 │    • plate YOLO again on the plate photo     │
 │    • upscale (AI) + glare/colour fix         │
 │    • RapidOCR reads text                     │
 │    • Indian plate rules clean the number     │
 │    • vote across 3 photos                    │
 │    • stabilize (match known plates)          │
 │    • check-in / check-out + save photo       │
 └───────────────┬──────────────────────────────┘
                 ▼
   MySQL: Visitor, VisitorEntry (entry_source=CCTV), VisitorAsset (photo)
                 ▼
   Web / mobile Visitors list shows plate + CCTV badge
```

**Why two programs (Reader + Celery)?**
- Watching video must never stop. OCR is slow (≈1–6 s). If OCR ran inside the
  reader, the reader would miss vehicles while reading a plate.
- So the reader only does fast work (detect + track) and hands slow work
  (OCR + database) to Celery through Redis.
- Celery never watches the camera — it only wakes up when a job is in Redis.

---

## 3. Services on the live server

| Service | Command | Job |
|---|---|---|
| `patrol-anpr-reader` | `python manage.py run_anpr_reader` | Watches cameras, sends jobs |
| `patrol-anpr-celery` | `celery -A patrol_backend worker -Q anpr -c 1 --prefetch-multiplier=1` | Runs OCR jobs from queue `anpr` |
| Main Celery | `celery -A patrol_backend worker -B` | Reports, emails, beat (not ANPR) |
| Redis | `redis-server` (localhost:6379/0) | Queue (broker) + task results |
| MySQL | — | Visitors / entries / photos |

Path: `/root/htdocs/ravi/gms/patrol_backend` · venv: `/root/htdocs/ravi/gms/gtms_venv`

Deploy order when code changes: **restart Celery first, then the Reader**
(the reader sends new job fields that old Celery code would not accept).

---

## 4. Step by step

### 4.1 Reader (`visitor/anpr/reader.py`, `tracker.py`)

Runs a loop per camera, `ANPR_DETECT_FPS` times per second.

1. **Get the newest frame** — a background thread (`_FrameGrabber`) reads the
   RTSP stream continuously and keeps only the latest frame. The detect loop
   always gets a *fresh* frame, never an old buffered one. If no new frame
   arrives for a while, it reconnects.
2. **Crop to ROI** (optional zone drawn in CCTV Live → ANPR zone) and shrink to
   max 960 px for speed.
3. **Detect plates** — plate YOLO (`license_plate_detector.pt`), conf ≥ 0.18.
   If the plate model is missing, falls back to vehicle YOLO (`yolov8n.pt`).
4. **Track** — each plate box is matched to an existing vehicle track:
   - first by overlap (IoU),
   - then by nearest centre (so a *moving* plate that moved more than its own
     width between frames is still the same vehicle; limit `ANPR_TRACK_MAX_JUMP`).
   - A track becomes **STABLE** after 2 hits (`ANPR_MIN_TRACK_HITS`).
   - If a virtual line exists, the tracker detects when the plate crosses it and
     the direction (`cross_dir` +1 in / −1 out).
5. **Keep the best photos** — for every frame the plate is visible, score =
   *plate sharpness (Laplacian) × √plate width*. Keeps:
   - best plate zoom (for OCR) + full frame (evidence),
   - 2 runner-up plate zooms (`ANPR_EXTRA_OCR_FRAMES`) for voting.
6. **Decide when to record** (`SiteCamera.gate_mode`):
   - `line_direction` — only when the vehicle crosses the line (recommended for production).
   - `parked_toggle` — whenever a stable plate is seen (testing; parked cars toggle in/out after cooldown).
7. **Enqueue** — writes JPEGs into `media/anpr_pending/`:
   - `…jpg` evidence (vehicle crop or full frame — saved as the visitor photo)
   - `…_ocr.jpg` best plate zoom
   - `…_ocr2.jpg`, `…_ocr3.jpg` runner-up plate zooms

   then sends the job. Track state → `OCR_QUEUED` (not re-sent while still visible).

### 4.2 Queue (`visitor/anpr/queue.py`)

- Uses the same Redis as normal Celery (`CELERY_BROKER_URL = redis://localhost:6379/0`). No RabbitMQ.
- Before sending, checks queue length. If ≥ `ANPR_MAX_QUEUE_DEPTH` (8) →
  **backpressure**: job skipped and temp JPEGs deleted (protects the server when OCR falls behind).
- Job expires after `ANPR_STALE_FRAME_SEC + 30` s.

Job payload (JSON):

```text
camera_id, site_id, location_id, track_id, direction_mode, gate_mode,
captured_at, jpeg_path, ocr_jpeg_path, cross_dir, detect_meta,
ocr_extra: [{path, meta}, …]      ← runner-up plate photos for voting
```

### 4.3 Celery task (`visitor/anpr/tasks.py` → `process_anpr_frame`)

1. **Stale check** — if the job waited more than `ANPR_STALE_FRAME_SEC` (30 s), drop it.
2. Load site + camera (gate mode can change from the admin UI any time).
3. **OCR the best plate photo** — `extract_vehicle_from_bgr()` (see 4.4).
4. **Voting** — if the first read is not trusted or confidence < `ANPR_VOTE_BELOW_CONF` (0.95),
   also OCR the 2 runner-up photos and pick the plate most photos agree on
   (sum of confidence). One blurred frame that misreads `7` as `1` is outvoted.
5. **Gate trust check** (`ocr_gate.py`) — rejects:
   - short numbers, camera OSD / timestamp text, invalid Indian plates,
   - OCR confidence < 0.45,
   - text that did not come from a plate crop.
6. **Stabilize** (`plate_stabilize.py`) — OCR may differ by 1 character on the
   same vehicle. Matches against open CCTV entries, same-track memory, recent site
   plates (edit distance 1). Never changes to a weaker plate, and ignores old
   junk plates (e.g. `TN58TN58`).
7. **Gate** (`gate.py` → `apply_gate_event`):
   - cooldown per plate (`ANPR_COOLDOWN_SEC`, 60 s) to avoid double events,
   - `Visitor` with IC `CCTV-<plate>` (created once per plate),
   - check-in → new `VisitorEntry` (entry_source CCTV) + `check_in_photo`,
   - check-out → close open entry + `exit_photo`,
   - vehicle type from YOLO label (car / truck / bus / motorcycle).
8. Delete all temp JPEGs in `media/anpr_pending/` (every exit path).

### 4.4 Reading the plate (`visitor/ai/pipeline_v2.py`, `plate_enhance.py`, `parse.py`)

```text
plate photo
  → plate YOLO (find exact plate box; choose the one nearest the tracked plate)
  → crop plate with a little margin
  → upscale small plates (FSRCNN AI x2 / x4)
  → try 1: raw crop                         → RapidOCR
  → try 2: enhanced crop                    → RapidOCR
       • plate colour (white/yellow/green/black)
       • best colour channel (red channel for green EV plates)
       • flip so text is dark on light
       • glare/reflection spots filled in (inpaint)
       • gamma for over-bright plates, CLAHE contrast, sharpen
  → try 3: two-row plate joined into one row (bikes/autos/trucks)
  → keep only OCR text INSIDE the plate box (drops painted truck text,
    second vehicle's plate, shop boards)
  → join pieces on the same row, then top row + bottom row
  → Indian plate rules (parse.py)
```

Stops at the first good read (max 5 OCR tries). In CCTV mode it **never** OCRs
the whole frame — that is what used to merge two vehicles into one number.

**Indian plate rules** (`parse.py`):
- Format `SS 00 X(XX) 0000` — state code must be a real state/UT (`TN`, `KA`, `DL`…).
- One-letter OCR slips in state code repaired toward preferred state
  (`ANPR_PREFERRED_STATES=TN`): `TM38…` → `TN38…`, lost first letter `N64…` → `TN64…`.
- BH series `22BH1234AA` supported.
- Number part needs ≥ 3 digits (`ANPR_MIN_PLATE_DIGITS`) — stops `TN58TN58`.
- Series never has `I` or `O` (real RTO rule) — rejects `BA27TI59`, `AW59O8682`.
- Small words like `IND` on HSRP plates ignored.

---

## 5. Packages — what, where, when, why

| Package (version) | Used in | What it does here | When it runs | Why this one |
|---|---|---|---|---|
| **Django** 5.2.6 | whole backend | ORM for `Visitor`, `VisitorEntry`, `VisitorAsset`, `SiteCamera`; `manage.py run_anpr_reader`; cache for cooldown / track memory | Reader startup + camera reload; every Celery job | Existing GTMS framework |
| **django-db-connection-pool** 1.2.6 | settings `DATABASES` | MySQL connection pooling; reader releases connection after each query | Reader DB polls, Celery DB writes | Long-running reader otherwise hits `MySQL server has gone away` |
| **Redis** server | localhost:6379/0 | Queue `anpr` (Celery broker) + task results | Every enqueue / job | Already used by GTMS Celery; simple, fast, no RabbitMQ needed |
| **redis** (Python) 7.0.1 | `anpr/queue.py` | Reads queue length (`LLEN anpr`) for backpressure | Before every enqueue | Direct, 1 ms check |
| **celery** 5.4.0 + **kombu** 5.5.4 | `anpr/tasks.py`, `queue.py` | Background job runner; kombu is Celery's messaging layer to Redis | Every plate event | Existing GTMS task system; separate `anpr` queue keeps OCR away from reports |
| **opencv** (`cv2`) 4.11 | reader, pipeline, enhance | RTSP video capture (FFmpeg), JPEG encode/decode, resize, sharpness, CLAHE, inpaint (glare), perspective warp, `cv2.dnn` runs FSRCNN | Every frame (reader) and every job (Celery) | Standard, fast C++ image library; reads RTSP directly |
| **numpy** 1.26.4 | everywhere with images | Image arrays, maths | Always | Required by OpenCV / YOLO / OCR |
| **ultralytics** 8.3.70 | `visitor/ai/detect.py` | Runs **YOLOv8** — plate detector (`license_plate_detector.pt`) and fallback vehicle detector (`yolov8n.pt`) | Reader: every detect tick. Celery: once per photo | Small nano model, good speed on CPU, easy API |
| **torch** 2.13 (CPU) + torchvision | under ultralytics | Neural-network engine for YOLO | With YOLO | Ultralytics needs it; CPU build (no GPU on server, smaller) |
| **rapidocr-onnxruntime** 1.4.4 (+ onnxruntime) | `visitor/ai/rapid_ocr_engine.py` | OCR — finds text lines and reads characters; returns text, confidence and **box position** | Celery, 1–5 times per photo | Chosen over PaddleOCR: much less RAM (PaddleOCR added +3.3 GB), 3× faster cold start on the 4 vCPU / 8 GB server |
| **huggingface_hub** (`hf` CLI) | one-time install only | Downloaded plate model `joker5914/yolov8n-license-plate` | Once, on install | Model hosted on Hugging Face |
| Python `threading` (built in) | reader | Background frame grabber thread | Always (reader) | Keeps the newest frame without blocking detection |

**AI model files** (`visitor/ai/weights/`):

| File | Size | Purpose | In git? |
|---|---|---|---|
| `license_plate_detector.pt` | ~6 MB | YOLOv8n trained on number plates | **No** (`*.pt` ignored) — installed on live by `hf download` |
| `yolov8n.pt` | ~6 MB | Vehicle YOLO (fallback), auto-downloaded by ultralytics | No |
| `FSRCNN_x2.pb`, `FSRCNN_x4.pb` | ~40 KB each | AI super-resolution: upscales small plates before OCR (~20–40 ms) | **Yes** |
| RapidOCR ONNX models | inside the pip package | Text detection + recognition | Comes with pip |

**Note:** OpenCV 4.10/4.11 cannot load FSRCNN by itself (missing `DepthToSpace`
layer). `plate_enhance.py` registers a small custom layer so it works without extra packages.

**Note:** `requirements.txt` lists three OpenCV packages (`opencv-python`,
`opencv-contrib-python` 4.11, `opencv-python-headless` 4.10). They all provide
the same `cv2` module; whichever was installed last wins. The ANPR code works on both
4.10 and 4.11. Keeping only one is cleaner.

---

## 6. What we changed (Sep 2026) and why

### Problem reported

| Vehicle | Accuracy before |
|---|---|
| Car | ~90% |
| Bike | ~20% |
| Auto | ~20% |
| Truck / tanker | ~0% |

Main failures: two vehicles in one image → combined number; green plates wrong;
blurred / reflective / turned plates fail; invalid numbers like `TN58TN58`;
moving vehicles not recorded (only parked ones).

### Steps done on the live server

1. **Installed plate model** — `hf download joker5914/yolov8n-license-plate best.pt`
   → `visitor/ai/weights/license_plate_detector.pt`. Logs now show `Plate YOLO ready`.
2. **Checked RTSP URL** — main stream `channel=1&subtype=0` (full resolution) is correct.
3. **Camera settings** — tried changing exposure / bitrate / I-frame on the CP Plus
   web UI. Video changes (6144 kbps, I-frame 25) caused stream timeouts and H.264
   decode errors, so the camera was **reset to default**. Accuracy is now handled in code.

### Code changes

| Problem | Cause | Fix | File |
|---|---|---|---|
| Two vehicles → one combined number | OCR ran on the whole image and joined all text | No full-frame OCR in CCTV mode; choose the plate nearest the tracked one; keep only text inside the plate box | `pipeline_v2.py`, `rapid_ocr_engine.py` |
| Painted truck text read as plate | Same as above | Plate-box text filter | `pipeline_v2.py` |
| Green plates wrong | White text on green; OCR expects dark text | Detect plate colour, use best channel, flip polarity | `plate_enhance.py` |
| Glare / reflection | Bright spots hide letters | Glare spots filled in (inpaint), gamma for over-bright plates | `plate_enhance.py` |
| Bike / auto / far plates small & blurry | Plate only 40–80 px wide | FSRCNN AI upscale x2/x4 + contrast + sharpen | `plate_enhance.py`, weights |
| Two-row plates (bike, auto, truck) | Rows read separately / wrong order | Join rows into one line; row-aware text join | `plate_enhance.py`, `pipeline_v2.py` |
| `TN58TN58`, `ID60T60`, wrong state | No Indian rules | State-code whitelist, min digits, no I/O in series, BH series, first-letter repair | `parse.py` |
| Moving vehicles not recorded | Tracker used box overlap only; small fast plate = new track each frame | Nearest-centre matching fallback | `tracker.py` |
| Old/delayed frames | OpenCV buffers RTSP frames | Background frame-grabber thread keeps only the newest | `reader.py` |
| Blurry photo chosen | Sharpness measured on whole frame | Sharpness measured on the plate area × plate size | `reader.py` |
| One bad frame → wrong number | Only 1 photo OCR'd | Best 3 photos sent; majority vote | `reader.py`, `tasks.py` |
| Old junk plates attract new reads | Stabilizer matched against junk DB plates | Skip plates failing Indian rules | `plate_stabilize.py` |
| Jobs dropped as "stale" | 10 s limit shorter than queue wait with voting | Default raised to 30 s | `settings.py` |

### What code cannot fix

- **Heavy motion blur** (letters smeared) — needs a faster camera shutter (e.g. 1/500 s). Change only shutter, not video/bitrate settings.
- **Plate fully white from headlights** — no letters left to recover.
- **Plate hidden / very angled / dirty.**

---

## 7. Timing

From live Celery logs + local measurement:

| Case | Time per job |
|---|---|
| First job after restart (models load: YOLO ≈ 6 s, OCR ≈ 4 s) | ≈ 10 s (once) |
| Clear plate, confidence ≥ 0.95 | ≈ 1 s |
| Normal plate — voting with 3 photos | ≈ 2–3 s |
| Hard plate (green / blur / glare, several tries + voting) | ≈ 4–6 s |

Extra cost of the new image steps: FSRCNN ≈ 10–40 ms, glare/contrast ≈ 20 ms,
row join ≈ 1 ms. OCR calls are the main cost.

The reader does not wait for OCR — it keeps watching. The entry appears in the
app a few seconds after the vehicle passes.

---

## 8. Settings (environment variables)

Set in the systemd unit (`Environment=KEY=value`), then `daemon-reload` + restart.

| Variable | Default | Service | Meaning |
|---|---|---|---|
| `ANPR_ENABLED` | false | both | Turn ANPR on |
| `ANPR_DETECT_FPS` | 1 | reader | Detections per second (**3 recommended** for moving vehicles) |
| `ANPR_DETECT_CONF` | 0.18 | reader | Min plate YOLO confidence |
| `ANPR_DETECT_MAX_SIDE` | 960 | reader | Frame size for detection |
| `ANPR_MIN_TRACK_HITS` | 2 | reader | Frames before a track is stable |
| `ANPR_TRACK_MAX_JUMP` | 0.2 | reader | Max plate movement between frames (fraction of frame) |
| `ANPR_GRAB_THREAD` | 1 | reader | Background frame grabber (0 = old way) |
| `ANPR_EXTRA_OCR_FRAMES` | 2 | reader | Extra photos per vehicle for voting (0 = off) |
| `ANPR_MAX_QUEUE_DEPTH` | 8 | reader | Max waiting jobs |
| `ANPR_CAMERA_REFRESH_SEC` | 300 | reader | Reload camera settings from DB |
| `ANPR_COOLDOWN_SEC` | 60 | both | Ignore same plate for this long after an event |
| `ANPR_STALE_FRAME_SEC` | 30 | celery | Drop jobs older than this |
| `ANPR_VOTE_BELOW_CONF` | 0.95 | celery | Vote when first read is below this |
| `ANPR_PREFERRED_STATES` | TN | celery | State(s) used to repair first letters |
| `ANPR_MIN_PLATE_DIGITS` | 3 | celery | Min digits in last part of plate |
| `VISITOR_AI_PLATE_SR` | 1 | celery | FSRCNN AI upscale (0 = off) |
| `VISITOR_AI_PLATE_WEIGHTS` | — | both | Custom plate `.pt` path |
| `VISITOR_AI_MAX_PLATE_OCR_PASSES` | 5 | celery | Max OCR tries per photo |

Cooldown and plate memory use Django's default in-process cache (no `CACHES`
configured), so they reset when `patrol-anpr-celery` restarts.

---

## 9. Logs — what to look for

```bash
sudo journalctl -u patrol-anpr-reader -f
sudo journalctl -u patrol-anpr-celery -f | grep -E "vote|CHECK_|gate skip|ocr miss|succeeded in"
```

| Log line | Meaning |
|---|---|
| `Plate YOLO ready weights=…license_plate_detector.pt` | Plate model loaded |
| `Falling back to vehicle YOLO` | Plate model missing — install it |
| `[ANPR] enqueued task=… track=…` | Reader sent a job |
| `[ANPR] backpressure — skip enqueue` | Queue full; Celery too slow or stopped |
| `[ANPR_TASK] vote … votes={'TN59BP9717': (2, 1.62), …}` | Voting result (count, total confidence) |
| `vehicle-plate-v2 ocr pass=… up=fsrcnn_x2 … dropped_outside_plate=…` | OCR try details |
| `[ANPR_TASK] gate skip … reason=watermark_plate` | Rejected (junk / invalid Indian plate) |
| `[ANPR_TASK] stale frame age=…` | Job waited too long |
| `[ANPR_PLATE] stabilize raw=… -> …` | Plate corrected to a known plate |
| `[ANPR_GATE] CHECK_IN / CHECK_OUT plate=…` | Entry created / closed |
| `[ANPR_GATE] cooldown skip` | Same plate within cooldown |

---

## 10. File map

| File | Role |
|---|---|
| `visitor/management/commands/run_anpr_reader.py` | Starts the reader loop |
| `visitor/anpr/reader.py` | Camera workers, frame grabber, best-photo selection, enqueue |
| `visitor/anpr/tracker.py` | Vehicle tracking + line crossing |
| `visitor/anpr/geometry.py` | ROI crop, line side maths |
| `visitor/anpr/queue.py` | Enqueue + backpressure |
| `visitor/anpr/tasks.py` | Celery task: OCR, voting, gate |
| `visitor/anpr/ocr_gate.py` | Trust rules before creating entries |
| `visitor/anpr/plate_stabilize.py` | Fix 1-char OCR slips using known plates |
| `visitor/anpr/gate.py` | Check-in / check-out + photo save |
| `visitor/anpr/settings_helpers.py` | Reads `ANPR_*` settings |
| `visitor/ai/detect.py` | YOLO plate / vehicle detection |
| `visitor/ai/pipeline_v2.py` | Plate reading pipeline (`extract_vehicle_from_bgr`) |
| `visitor/ai/plate_enhance.py` | Colour, glare, FSRCNN upscale, two-row join |
| `visitor/ai/rapid_ocr_engine.py` | RapidOCR wrapper (text + boxes) |
| `visitor/ai/parse.py` | Indian plate rules / cleaning |
| `visitor/ai/weights/` | Model files |
