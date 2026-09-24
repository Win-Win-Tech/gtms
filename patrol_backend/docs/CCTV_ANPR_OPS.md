# CCTV ANPR — Ops runbook (Phases 3–5)

See also:
- [`CCTV_ANPR_FULL_PLAN.md`](CCTV_ANPR_FULL_PLAN.md)
- [`CCTV_ANPR_GEOMETRY_API.md`](CCTV_ANPR_GEOMETRY_API.md) — ROI / line create·read·edit·delete (web + mobile; existing camera APIs)

---

## Important: why `run_anpr_reader` is NOT Celery

You already run something like:

```bash
celery -A patrol_backend worker -B --loglevel=info
```

That is:
- **worker** = runs jobs that are already in Redis queues  
- **`-B` (beat)** = timer for scheduled jobs (emails, missed checkout, etc.)

**Celery never watches the CCTV camera.**  
It only wakes up when a **job** is already sitting in Redis.

The **ANPR Reader** is a **different program**:

```text
ANPR Reader  = always looks at RTSP / line / ROI, then PUTS a job in Redis
Celery       = TAKES that job, runs OCR, check-in/out
```

So:

| Process | What it is | Do you need it for ANPR? |
|---------|------------|---------------------------|
| Your current `celery worker -B` | Normal GTMS jobs + beat | Keep it (reports, etc.) |
| Celery worker on queue `anpr` | OCR + gate for plates | **Yes** (see options below) |
| `python manage.py run_anpr_reader` | Camera watcher | **Yes — separate systemd service** |
| Extra beat for ANPR | — | **No** |

You do **not** start the reader “inside” Celery.  
You do **not** need a second beat for ANPR.

The manual `cd … source venv … python manage.py run_anpr_reader` was only for **testing**.  
On the real server it should be a **systemd service** (always on, auto-restart), same idea as your Celery service.

---

## What was added in code

| Piece | Path |
|--------|------|
| Shared OCR from frame | `visitor/ai/pipeline_v2.py` → `extract_vehicle_from_bgr` |
| ANPR package | `visitor/anpr/` |
| Reader command | `python manage.py run_anpr_reader` |
| Celery task | `visitor.anpr.tasks.process_anpr_frame` → queue **`anpr`** |
| Settings | `ANPR_*` in `patrol_backend/settings.py` |

---

## 1) Migrate DB (once)

```bash
cd /root/htdocs/ravi/gms/patrol_backend   # your real path
source /root/htdocs/ravi/gms/venv/bin/activate   # adjust venv path
python manage.py migrate visitor
python manage.py migrate scheduler
```

---

## 2) Env (put in systemd Environment / EnvironmentFile)

```bash
ANPR_ENABLED=true
ANPR_MAX_CAMERAS=2
ANPR_DETECT_FPS=1
ANPR_COOLDOWN_SEC=60
ANPR_QUEUE=anpr
ANPR_CAMERA_REFRESH_SEC=300   # reload SiteCamera from DB every 5 min (diff-only)
```

No RabbitMQ. Same Redis as Celery.

Reader polls cameras every `ANPR_CAMERA_REFRESH_SEC` (default **300**). It only restarts a camera worker when RTSP / direction / **gate_mode** / geometry / site changed; unchanged cameras keep their RTSP + tracker. Stale MySQL sockets are cleared before each poll (`close_old_connections` + `connection.close`, one retry).

After a plate is enqueued, that track waits `ANPR_COOLDOWN_SEC` then **re-arms** so a parked car can toggle check-in/out again without leaving the frame (only when **gate_mode** = `parked_toggle`).

### Gate mode (`SiteCamera.gate_mode`)

Set under **Organisation → Site → CCTV cameras**:

| Mode | When it fires | How in/out is chosen |
|------|---------------|----------------------|
| **Whenever plate is seen** `parked_toggle` | Stable plate in view (parked OK), or line cross if a line exists | Lane type: both / entry-only / exit-only |
| **Only when vehicle crosses the line** `line_direction` | **Only** when the vehicle crosses the virtual line | Crossing side: `cross_dir > 0` → check-in, `< 0` → check-out. Flip by drawing the line the opposite way. Lane type can still restrict to entry-only / exit-only. |

`line_direction` **requires** a virtual line (CCTV Live → ANPR zone). Without a line, events will not fire.

OCR often misreads 1 character on the same vehicle. Before gate, plates are **stabilized** (country-agnostic): match open CCTV entries / same track memory / recent site plates by small edit distance — never remaps to a *worse/shorter* known plate.

When plate YOLO weights are missing, OCR uses the **lower band of the vehicle box** (not the whole car) plus multi-pass consensus. Install the plate `.pt` (section below) for best accuracy.

**Evidence images:** on successful gate events the JPEG is saved to `VisitorAsset`:
- check-in → `check_in_photo`
- check-out → `exit_photo`  
Temp files under `media/anpr_pending/` are deleted after copy.

DB pool: env `DB_POOL_RECYCLE` → settings `POOL_OPTIONS.RECYCLE` (default **280**).  
Use key **`RECYCLE`** (not `POOL_RECYCLE` — ignored by `dj_db_conn_pool`).

ANPR reader also:
- **releases** the DB connection after every camera query (management commands otherwise hold it forever)
- **keepalive** `SELECT 1` every `ANPR_DB_KEEPALIVE_SEC` (default **60**)
- disables SQLAlchemy **rollback-on-return** so a dead idle socket does not log  
  `Exception during reset or similar` / `MySQL server has gone away`

---

## 3) Celery — how to combine with your current command

### Option A (simplest on small server) — one worker listens to both queues

Change your existing worker to also consume `anpr`:

```bash
celery -A patrol_backend worker -B -Q celery,anpr --concurrency=2 --loglevel=info
```

- Still **one** Celery + Beat process (like today)  
- Queue `celery` = reports / normal tasks  
- Queue `anpr` = plate OCR  
- Use low concurrency (2) so OCR does not take all CPUs  

### Option B (cleaner) — keep your current Celery, add a small ANPR-only worker

Keep:

```bash
celery -A patrol_backend worker -B --loglevel=info
```

Add second service:

```bash
celery -A patrol_backend worker -Q anpr -c 1 --prefetch-multiplier=1 --loglevel=info
```

**Either A or B is fine.**  
**Beat (`-B`) stays only on your main Celery** — do not add `-B` on the ANPR-only worker.

---

## 4) ANPR Reader — **must** be its own systemd service

This is the part that watches the camera. Celery cannot replace it.

### Example: `/etc/systemd/system/gtms-anpr-reader.service`

Adjust paths/user to match your server:

```ini
[Unit]
Description=GTMS CCTV ANPR Reader (RTSP watch)
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/htdocs/ravi/gms/patrol_backend
Environment=DJANGO_SETTINGS_MODULE=patrol_backend.settings
Environment=ANPR_ENABLED=true
Environment=ANPR_MAX_CAMERAS=2
Environment=ANPR_DETECT_FPS=1
Environment=ANPR_COOLDOWN_SEC=60
Environment=ANPR_CAMERA_REFRESH_SEC=300
Environment=ANPR_QUEUE=anpr
# If you use a .env file instead:
# EnvironmentFile=/root/htdocs/ravi/gms/patrol_backend/.env
ExecStart=/root/htdocs/ravi/gms/gtms_venv/bin/python manage.py run_anpr_reader
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now patrol-anpr-reader
sudo systemctl status patrol-anpr-reader
sudo journalctl -u patrol-anpr-reader -f
```

**Do not** use gunicorn in this unit — that is the website API, not the camera watcher.  
Healthy reader: `Main PID: … (python)` and `manage.py run_anpr_reader`.

### Optional: ANPR-only Celery worker service

Only if you chose **Option B** above.

`/etc/systemd/system/patrol-anpr-celery.service`:

```ini
[Unit]
Description=GTMS Celery ANPR worker (OCR queue)
After=network.target redis.service
Wants=redis.service

[Service]
Type=simple
User=root
WorkingDirectory=/root/htdocs/ravi/gms/patrol_backend
Environment=DJANGO_SETTINGS_MODULE=patrol_backend.settings
Environment=ANPR_ENABLED=true
ExecStart=/root/htdocs/ravi/gms/gtms_venv/bin/celery -A patrol_backend worker -Q anpr -c 1 --prefetch-multiplier=1 --loglevel=info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now patrol-anpr-celery
sudo systemctl status patrol-anpr-celery
```

Healthy Celery: `celery@… ready` and registered task `visitor.anpr.tasks.process_anpr_frame`.

### Troubleshoot systemd

| Symptom | Cause | Fix |
|---------|--------|-----|
| `status=203/EXEC` | Wrong binary path | Use real `gtms_venv` paths; `ls` the ExecStart file |
| Reader runs as **gunicorn** | Wrong ExecStart | Must be `python manage.py run_anpr_reader` |
| `MySQL server has gone away` | Stale DB pool after idle | Deploy latest reader; keep `DB_POOL_RECYCLE≤280`; restart reader |
| Celery inactive, reader OK | No OCR → no check-in/out | Fix/start `patrol-anpr-celery` |
| `Received unregistered task ... process_anpr_frame` | Nested `visitor.anpr.tasks` not auto-discovered; or wrong worker ate the message | Ensure `ANPR_ENABLED=true` on ANPR Celery; `visitor.apps` + `CELERY_IMPORTS` register the task after Django ready; restart `patrol-anpr-celery`; default worker must use `-Q celery` and a unique `-n` |
| `AppRegistryNotReady` on anpr reader/celery after celery.py change | Eager `import visitor.anpr.tasks` inside `celery.py` (loaded from `patrol_backend/__init__` before `django.setup`) | Do **not** import ANPR tasks in `celery.py`; use `VisitorConfig.ready()` / `CELERY_IMPORTS` instead |

After deploying reader code:

```bash
sudo systemctl restart patrol-anpr-reader
sudo systemctl restart patrol-anpr-celery
```

---

## Final picture on the server

```text
1) gunicorn / daphne           → normal website API
2) celery worker -B            → reports + beat  (+ anpr queue if Option A)
3) patrol-anpr-reader          → WATCHES camera  ← required
4) mediamtx                    → live view for guards
5) patrol-anpr-celery (opt B)  → OCR queue only
```

Manual `python manage.py run_anpr_reader` = same as the reader service, but for a quick test in SSH.  
After the systemd unit works, you do **not** start it by hand every time.

---

## Optional: set line/ROI

ROI and virtual line are **optional per camera** when **gate_mode** is `parked_toggle`. Empty `anpr_geometry` (`{}`) means
capture-on-stable (full frame) — no default line/ROI is applied.

For **gate_mode** = `line_direction`, a virtual **line is required**.

**Admin UI (preferred):** Visitor → **CCTV Live** → **ANPR zone**
- **Add ROI** / **Add line** — only when that org wants them
- **Delete ROI** / **Delete line** / **Clear all** then **Save** — removes zone
- Leave empty → reader captures without requiring a line cross (**parked_toggle** only)

Reader reloads geometry within `ANPR_CAMERA_REFRESH_SEC` (default 5 min).

**Shell (optional):**

```bash
python manage.py shell
```

```python
from scheduler.models import SiteCamera
c = SiteCamera.objects.filter(is_enabled=True).first()
# Optional zone (omit roi and/or line keys you don't want):
c.anpr_geometry = {
  "roi": {"x1": 0.05, "y1": 0.25, "x2": 0.95, "y2": 0.98},
  "line": {"x1": 0.0, "y1": 0.55, "x2": 1.0, "y2": 0.55},
}
# Or clear for capture-only:
# c.anpr_geometry = {}
c.save(update_fields=["anpr_geometry", "modified_on"])
```

---

## OCR quality (watermark / full-frame / distant plates)

The ANPR Celery task is **stricter than mobile extract-v2**:

- Rejects camera OSD/timestamp text (`12.13:05-Wed`, `Smart Surveillance`, `…SMA` suffix).
- **No check-in/out** when OCR came from `full_frame_enhanced` or `screen_inset_enhanced` (no plate/vehicle crop).
- Requires a trusted detector (`plate_crop`, `plate_wide`, `plate_context`, `vehicle_bottom`, …).
- Plate YOLO conf ≥ **0.28** (distant plates); vehicle-box conf ≥ **0.5**.

**Distant parked vehicles:** detect may run on a downscaled frame, but OCR crops are taken from the **full-resolution** JPEG. Tiny plate boxes get expanded context (`plate_wide` / `plate_context`) and strong upscale before RapidOCR. The reader also saves a **plate-centered zoom crop** for OCR only (`*_ocr.jpg`). Visitor check-in/out photos use a **vehicle crop or full frame**, never the plate zoom.

Task skip reasons in Celery logs: `watermark_plate`, `untrusted_ocr_source`, `no_plate_crop`, `low_yolo_conf`.

---

## Install plate YOLO weights (local + live server)

**Why:** Without this file, logs show `Falling back to vehicle YOLO` and OCR runs on the whole bike/car box (noisy). With it, detection returns a **tight license-plate box**.

**Not** `pip install` of a plate package — you download one small `.pt` file (~6 MB) once.

**Target path** (code looks here automatically):

```text
patrol_backend/visitor/ai/weights/license_plate_detector.pt
```

Also accepted: `yolov8n-license-plate.pt` in the same folder, or env:

```bash
VISITOR_AI_PLATE_WEIGHTS=/full/path/to/license_plate_detector.pt
```

### Steps (live server)

1. Activate the same venv used by Celery / Django:

```bash
cd /path/to/patrol_backend   # e.g. .../backendnew/gtms/patrol_backend
source ../venv/bin/activate  # adjust to your live venv path
```

2. Install Hugging Face CLI helper (once; needs outbound HTTPS to `pypi.org`):

```bash
pip install huggingface_hub
```

3. Download YOLOv8 **nano** plate model and rename to the expected filename:

```bash
# Use `hf` (not deprecated `huggingface-cli`)
hf download joker5914/yolov8n-license-plate best.pt \
  --local-dir visitor/ai/weights

mv -f visitor/ai/weights/best.pt visitor/ai/weights/license_plate_detector.pt
```

**Alternative** (no `hf` — needs DNS to `huggingface.co`):

```bash
cd visitor/ai/weights
# remove any empty failed download first
rm -f license_plate_detector.pt
wget -O license_plate_detector.pt \
  "https://huggingface.co/joker5914/yolov8n-license-plate/resolve/main/best.pt"
```

4. Confirm file size is ~6 MB (not 0 bytes):

```bash
ls -lh visitor/ai/weights/license_plate_detector.pt
```

5. Restart ANPR services so they load the new weights:

```bash
sudo systemctl restart patrol-anpr-reader
sudo systemctl restart patrol-anpr-celery
# or your live unit names, e.g. gtms-anpr-reader / gtms-anpr-celery
```

6. Verify in logs after the next ANPR frame:

```text
Loading plate YOLO weights=.../license_plate_detector.pt
```

**Bad** (still missing / empty file):

```text
Plate YOLO unavailable ...
Falling back to vehicle YOLO for plate-region detection
```

### Notes

| Topic | Detail |
|--------|--------|
| Memory | Nano plate model is small (~100–300 MB RAM when loaded). Already planned for 4 vCPU / 8 GB. |
| First load | File is downloaded **once**. Python loads it into RAM **once per process restart**, then reuses it. |
| Offline / DNS fail | Copy `license_plate_detector.pt` from a machine that can download (USB/scp) into `visitor/ai/weights/` on the live host. |
| Source model | [joker5914/yolov8n-license-plate](https://huggingface.co/joker5914/yolov8n-license-plate) (`best.pt`). Ultralytics Hub id `keremberke/yolov8n-license-plate-detection` often fails — do not rely on it. |

---

## Verify

1. `redis-cli ping`
2. `systemctl status gtms-anpr-reader` → active  
3. Logs: `[ANPR_READER] active cameras=…`  
4. Vehicle crosses line → `[ANPR] enqueued`  
5. Celery → `[ANPR_GATE] CHECK_IN` / `CHECK_OUT`  
6. Visitors list → plate + CCTV badge  
