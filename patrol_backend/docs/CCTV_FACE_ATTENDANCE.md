# CCTV Face Attendance (walk-by, kiosk rules)

**Status:** Phase A1 done locally (detection + tracking, **no attendance yet**) · A2 next  
**Last updated:** 2026-10-07  
**Related docs:**
- [`CCTV_CAMERA_MODES_PLAN.md`](CCTV_CAMERA_MODES_PLAN.md) — Vehicle / Face camera types (Phase 0)
- [`CCTV_ANPR_OPS.md`](CCTV_ANPR_OPS.md) — the vehicle reader this one is modelled on
- [`FACE_ATTENDANCE_INSTALL.md`](FACE_ATTENDANCE_INSTALL.md) — kiosk face attendance (dlib + FAISS)

---

## 1. What we are building

A **Face** camera (Organisation → Site → CCTV cameras, type **Face**, feature **Face attendance**)
watches a walkway. When a registered employee walks past, their attendance is marked
with the **same rules as the kiosk tablet**.

| Situation | What happens |
|-----------|--------------|
| Registered person walks past | Check-in (or check-out if already checked in) |
| 2–3 people walk past together | Each one is marked separately |
| Same person again **within 5 min** of check-in | Nothing (kiosk rule `FACE_KIOSK_MIN_CHECKOUT_MINUTES=5`) |
| Same person again after 5 min | Check-out |
| Same person seen again within a few seconds | Ignored (same pass / duplicate) |
| Unknown face (not registered) | Ignored — only logged |
| Person has **no shift** today | No punch (same as kiosk), logged as `no_shift` — can be switched off by setting |
| Camera lane is "In only" / "Out only" | Only check-ins / only check-outs on that camera |

---

## 2. Phases

| Phase | Goal | Writes attendance? | Status |
|-------|------|--------------------|--------|
| **A1** | Find every face, follow each person, pick their best photo, log it | No | ✅ Done (local) |
| **A2** | Recognise who it is (face index), save every pass as an event | No (dry run) | Next |
| **A3** | Mark attendance with kiosk rules (shift, 5 min, lane) | **Yes** | Later |
| **A4** | Hardening: load limits, review screen for events, tuning | Yes | Later |

Each phase can be deployed alone and checked on live before the next.

### A1 — Detection + tracking ✅

What it does:
1. Reads the Face camera stream (same RTSP helpers as the vehicle reader).
2. Skips work when the picture is still (motion gate) — checks again every 2 s anyway.
3. Finds all faces in the frame with **YuNet** (small OpenCV model, ~60 ms per frame on CPU).
4. Follows each face across frames, so one person walking past = **one** "person" line, not 20.
5. Keeps the sharpest, biggest photo of each person (the one A2 will recognise).
6. Writes logs so we can see on live: how many people, are faces big enough, how much CPU.

What it does **not** do: recognise anyone or mark attendance.

Code (`dashboard/cctv_face/`):

| File | Purpose |
|------|---------|
| `models/face_detection_yunet_2023mar.onnx` | Face detector model (232 KB, committed) |
| `settings_helpers.py` | Reads `FACE_CCTV_*` settings with safe limits |
| `detector.py` | `FaceDetector` — YuNet wrapper, downsizes frame to 640 px |
| `tracker.py` | `FaceTracker` — follows faces, keeps best crop per person |
| `reader.py` | Camera loop: connect, motion gate, detect, track, log |
| `tests.py` | 16 tests (no database needed) |
| `dashboard/management/commands/run_face_reader.py` | `python manage.py run_face_reader [--once]` |
| `bin/run_cctv_readers.sh` | Starts vehicle + face readers together for one systemd service |

Cameras picked up: **enabled** + type **Face** + feature **Face attendance**, max
`FACE_CCTV_MAX_CAMERAS` (default 1). Camera list is re-read every 5 min, so adding /
disabling a camera in the web app needs no restart.

### A2 — Recognition (dry run)

- Encode the best crop (dlib, same as kiosk) and search the location's FAISS face index.
- Strict match (distance ≤ 0.38) accepted; relaxed match (≤ 0.45) only if 2 crops agree.
- Face index refreshed every 5 min (new registrations picked up without restart).
- New table `CctvFaceEvent` (camera name, user, distance, result, crop path, time).
- Result is `recognised` / `unknown` / `too_small` — nothing written to attendance.

### A3 — Attendance

- `apply_cctv_punch` uses the kiosk helpers (`kiosk_apply_punch`, shift window, 5 min rule).
- Settings: `FACE_CCTV_PUNCH_ENABLED`, `FACE_CCTV_REQUIRE_SHIFT` (default on),
  `FACE_CCTV_USER_COOLDOWN_SEC=60`.
- Lane from camera direction: in / out / toggle.

### A4 — Hardening

- CPU / memory limits tuned from live numbers, event review page, alerts.

---

## 3. Settings (A1)

All read from environment variables (set them in the systemd unit, §4).

| Setting | Default | Meaning |
|---------|---------|---------|
| `FACE_CCTV_ENABLED` | `false` | Master switch — reader does nothing unless `true` |
| `FACE_CCTV_MAX_CAMERAS` | `1` | How many Face cameras to watch |
| `FACE_CCTV_FPS` | `2` | Frames checked per second per camera |
| `FACE_CCTV_DETECT_MAX_SIDE` | `640` | Frame is shrunk to this size before detection (smaller = faster, misses far faces) |
| `FACE_CCTV_DETECT_SCORE` | `0.8` | How sure the detector must be that it is a face (0.3–0.99) |
| `FACE_CCTV_MIN_FACE_PX` | `60` | Face narrower than this (in camera pixels) is "too small" to recognise |
| `FACE_CCTV_MIN_TRACK_HITS` | `2` | Face must be seen in at least this many frames to count as a person |
| `FACE_CCTV_TRACK_LOST_SEC` | `1.5` | Person counted as "gone" after this many seconds without their face |
| `FACE_CCTV_MOTION_GATE` | `true` | Skip detection when the picture does not change |
| `FACE_CCTV_IDLE_DETECT_SEC` | `2` | Even with no motion, check every N seconds |
| `FACE_CCTV_MOTION_HOLD_SEC` | `2` | Keep detecting N seconds after motion stops |
| `FACE_CCTV_MOTION_MIN_AREA` | `0.003` | Share of the picture that must change to count as motion |
| `FACE_CCTV_CAMERA_REFRESH_SEC` | `300` | Re-read the camera list every N seconds |
| `FACE_CCTV_CV_THREADS` | `1` | OpenCV threads (keep 1 on the 2 vCPU server) |
| `FACE_CCTV_SAVE_DEBUG_CROPS` | `false` | Save each person's best photo to `media/face_cctv_debug/` |
| `FACE_CCTV_DEBUG_KEEP` | `200` | Keep only the newest N debug photos |
| `FACE_CCTV_DB_KEEPALIVE_SEC` | `60` | Ping the database every N seconds (env only) |

---

## 4. Deploy A1 on live (step by step)

No database migration in A1. No new pip packages (OpenCV is already installed for ANPR).

**Step 1 — Pull the code** (the usual way you deploy), then check the model file arrived:

```bash
cd /root/htdocs/ravi/gms/patrol_backend
ls -l dashboard/cctv_face/models/face_detection_yunet_2023mar.onnx
# should show about 232589 bytes
```

**Step 2 — Quick test (runs ~15 seconds then stops):**

```bash
cd /root/htdocs/ravi/gms/patrol_backend
FACE_CCTV_ENABLED=true /root/htdocs/ravi/gms/gtms_venv/bin/python manage.py run_face_reader --once
```

Good output contains:
- `[FACE_CCTV] watching camera=… name=…` — camera found
- `[FACE_CCTV] started fps=2 …`
- `[FACE_CCTV] status camera=… detects=1 …` — a frame was read and checked

If you see `no enabled Face cameras with face attendance`: in the web app the camera
must be **Enabled**, type **Face**, and **Face attendance** ticked.

**Step 3 — One service for both readers (vehicle + face):**

The vehicle reader and the face reader run from **one** service, `patrol-cctv-readers`,
using `bin/run_cctv_readers.sh`. The script:
- starts the vehicle reader if `ANPR_ENABLED=true` and the face reader if `FACE_CCTV_ENABLED=true`;
- runs the face reader at lower priority, so when CPU is short the vehicle gate wins;
- if either reader stops, it stops the other too, so systemd restarts both cleanly.

The ANPR **Celery** worker (`patrol-anpr-celery`) stays as it is — it is not part of this.

3a. Look at the current vehicle reader service and note any extra `Environment=ANPR_…` lines:

```bash
cat /etc/systemd/system/patrol-anpr-reader.service
```

3b. Make the script runnable (once, after pulling):

```bash
chmod +x /root/htdocs/ravi/gms/patrol_backend/bin/run_cctv_readers.sh
```

3c. Create the new service file:

```bash
sudo nano /etc/systemd/system/patrol-cctv-readers.service
```

Paste (copy any extra `ANPR_…` lines from 3a under the vehicle section):

```ini
[Unit]
Description=GTMS CCTV readers (vehicle ANPR + face attendance)
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/htdocs/ravi/gms/patrol_backend
EnvironmentFile=-/root/htdocs/ravi/gms/patrol_backend/.env
Environment=DJANGO_SETTINGS_MODULE=patrol_backend.settings
Environment=PYTHON=/root/htdocs/ravi/gms/gtms_venv/bin/python
Environment=OMP_NUM_THREADS=1
Environment=OPENBLAS_NUM_THREADS=1
# --- vehicle (ANPR) reader ---
Environment=ANPR_ENABLED=true
Environment=ANPR_MAX_CAMERAS=2
Environment=ANPR_DETECT_FPS=4
Environment=ANPR_COOLDOWN_SEC=60
Environment=ANPR_CAMERA_REFRESH_SEC=300
Environment=ANPR_QUEUE=anpr
# --- face reader ---
Environment=FACE_CCTV_ENABLED=true
Environment=FACE_CCTV_MAX_CAMERAS=1
Environment=FACE_CCTV_FPS=2
Environment=FACE_CCTV_SAVE_DEBUG_CROPS=true
ExecStart=/root/htdocs/ravi/gms/patrol_backend/bin/run_cctv_readers.sh
Restart=always
RestartSec=5
# Website / API get CPU first when the server is busy
CPUWeight=50
MemoryMax=2500M

[Install]
WantedBy=multi-user.target
```

Save (Ctrl+O, Enter, Ctrl+X).

**Step 4 — Switch from the old vehicle service to the new one:**

The old `patrol-anpr-reader` **must be stopped and disabled** — otherwise two vehicle
readers run and every vehicle entry is created twice.

```bash
sudo systemctl disable --now patrol-anpr-reader
sudo systemctl daemon-reload
sudo systemctl enable --now patrol-cctv-readers
sudo systemctl status patrol-cctv-readers
```

`status` should show both `run_cctv_readers: vehicle reader started` and
`run_cctv_readers: face reader started`.

**Everyday commands:**

| What | Command |
|------|---------|
| Restart both (after deploy) | `sudo systemctl restart patrol-cctv-readers` |
| Stop both | `sudo systemctl stop patrol-cctv-readers` |
| Turn **face off only** | set `Environment=FACE_CCTV_ENABLED=false`, then `sudo systemctl daemon-reload && sudo systemctl restart patrol-cctv-readers` |
| Turn **vehicle off only** | set `Environment=ANPR_ENABLED=false`, then the same two commands |
| Go back to the old vehicle-only service | `sudo systemctl disable --now patrol-cctv-readers && sudo systemctl enable --now patrol-anpr-reader` |

---

## 5. Checking A1 on live

**Watch the logs while someone walks past the camera:**

```bash
sudo journalctl -u patrol-cctv-readers -f | grep FACE_CCTV
```

(Vehicle lines: `| grep ANPR_READER`. Both: leave out the `grep`.)

Two kinds of lines:

```text
[FACE_CCTV] person camera=… track=…-f7 hits=6 seconds=2.5 face_px=112 score=0.93 recognisable=yes
[FACE_CCTV] status camera=… ticks=120 detects=45 idle_skips=75 detect_ms=58 max_faces=2 people=3 recognisable=2 too_small=1 tracks_now=0
```

| Field | What it tells you | Good value |
|-------|-------------------|------------|
| `person … recognisable=yes` | One person walked past and their face was big enough | One line per person per pass |
| `face_px` | Face width in camera pixels | **≥ 80** is comfortable; < 60 = camera too far / too wide |
| `detect_ms` | Time to check one frame | < 150 ms; > 300 ms = server too busy |
| `idle_skips` | Frames skipped because nothing moved | High when nobody is there (saves CPU) |
| `max_faces` | Most faces seen in one frame | Matches the group size walking in |
| `too_small` | People whose face was too small | Should be ~0 — if not, move/zoom camera |

**See the photos it picked** (because `FACE_CCTV_SAVE_DEBUG_CROPS=true`):

```bash
ls -lt /root/htdocs/ravi/gms/patrol_backend/media/face_cctv_debug | head
```

Open a few in the browser (`https://<your-domain>/media/face_cctv_debug/<file>.jpg`) —
the face should be clear and front-facing. If faces are side-on or blurry, adjust the
camera before A2.

When testing is finished, set `FACE_CCTV_SAVE_DEBUG_CROPS=false` (these are face photos
under `media/`), then `daemon-reload` + `restart`.

**Check the server is not overloaded:**

```bash
vmstat 5 5      # "id" (idle) column should stay above ~20; "st" is Hostinger steal
top -o %CPU     # "python manage.py run_face_reader" should stay under ~70%
```

### What to test (walk tests)

1. One person walks past normally → **one** `person` line, `recognisable=yes`.
2. Same person stands in front for 10 s → still **one** line (after they leave).
3. Two or three people together → `max_faces` 2–3 and one `person` line each.
4. Nobody there for a minute → `idle_skips` high, `detects` low.

Send the `status` and `person` lines after these tests — they decide the A2 settings
(minimum face size, how many crops to use).

---

## 6. Camera placement tips

- Face height, at the point people walk through: ideally **≥ 100 px wide** on the camera image.
- Camera at head height or slightly above (not looking straight down).
- Point along the walking direction so people walk **towards** the camera.
- Good light on faces; avoid strong light behind people (doorway glare).
- Narrow walkway / door is better than a wide lobby.

---

## 7. Troubleshooting

| Symptom | Cause | Fix |
|---------|-------|-----|
| `FACE_CCTV_ENABLED is false` | Env not set | Add `Environment=FACE_CCTV_ENABLED=true` to the unit, `daemon-reload`, restart |
| `face detector model could not be loaded` | Model file missing | Pull again; check Step 1 |
| `no enabled Face cameras with face attendance` | Camera settings | Web app: Enabled + Face + Face attendance |
| `connect failed camera=…` | Stream not reachable | Check the camera is online and the RTSP URL in the web app |
| `no new frame` / `frozen stream` repeating | Camera / network drops | Reader reconnects by itself; check camera network |
| `detect_ms` > 300 | Server busy (CPU steal) | Lower `FACE_CCTV_FPS=1`, or stop it until the server is upgraded |
| Many `too_small` | Camera too far / too wide | Zoom or move the camera closer to the walkway |
| Website slow after starting | Too much CPU | Turn face off only (`FACE_CCTV_ENABLED=false`, §4), send `vmstat 5 5` output |
| `run_cctv_readers: a reader stopped` repeating | One reader keeps crashing | `sudo journalctl -u patrol-cctv-readers -n 200` and send the error above that line |
| Vehicle entries created twice | Old `patrol-anpr-reader` still running | `sudo systemctl disable --now patrol-anpr-reader` |

---

## 8. Running tests (developers)

```bash
cd backendnew/gtms/patrol_backend
../venv/bin/python manage.py test dashboard.cctv_face -v 2
```

Tests use mocks — no database or camera needed.
