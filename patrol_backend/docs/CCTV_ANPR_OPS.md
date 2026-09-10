# CCTV ANPR — Ops runbook (Phases 3–5)

See also: [`CCTV_ANPR_FULL_PLAN.md`](CCTV_ANPR_FULL_PLAN.md)

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

Reader polls cameras every `ANPR_CAMERA_REFRESH_SEC` (default **300**). It only restarts a camera worker when RTSP / direction / geometry / site changed; unchanged cameras keep their RTSP + tracker. Stale MySQL sockets are cleared before each poll (`close_old_connections` + `connection.close`, one retry).

After a plate is enqueued, that track waits `ANPR_COOLDOWN_SEC` then **re-arms** so a parked car can toggle check-in/out again without leaving the frame.

OCR often misreads 1 character on the same vehicle. Before gate, plates are **stabilized** (country-agnostic): match open CCTV entries / same track memory / recent site plates by small edit distance — never remaps to a *worse/shorter* known plate.

When plate YOLO weights are missing, OCR uses the **lower band of the vehicle box** (not the whole car) plus multi-pass consensus. For best accuracy place a plate `.pt` via `VISITOR_AI_PLATE_WEIGHTS`.

**Evidence images:** on successful gate events the JPEG is saved to `VisitorAsset`:
- check-in → `check_in_photo`
- check-out → `exit_photo`  
Temp files under `media/anpr_pending/` are deleted after copy.

DB pool: `DB_POOL_RECYCLE` (default **280**) — keep below MySQL `wait_timeout` so long processes do not hit “server has gone away”.

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
ExecStart=/root/htdocs/ravi/gms/venv/bin/python manage.py run_anpr_reader
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

Enable:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now gtms-anpr-reader
sudo systemctl status gtms-anpr-reader
sudo journalctl -u gtms-anpr-reader -f
```

### Optional: ANPR-only Celery worker service

Only if you chose **Option B** above.

`/etc/systemd/system/gtms-celery-anpr.service`:

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
ExecStart=/root/htdocs/ravi/gms/venv/bin/celery -A patrol_backend worker -Q anpr -c 1 --prefetch-multiplier=1 --loglevel=info
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now gtms-celery-anpr
```

---

## Final picture on the server

```text
1) gunicorn / daphne     → normal website API
2) celery worker -B      → reports + beat  (+ anpr queue if Option A)
3) gtms-anpr-reader      → WATCHES camera  ← new systemd service (required)
4) mediamtx              → live view for guards (unchanged)
5) (optional) celery -Q anpr   → only if Option B
```

Manual `python manage.py run_anpr_reader` = same as the reader service, but for a quick test in SSH.  
After the systemd unit works, you do **not** start it by hand every time.

---

## Optional: set line/ROI

```bash
python manage.py shell
```

```python
from scheduler.models import SiteCamera
c = SiteCamera.objects.filter(is_enabled=True).first()
c.anpr_geometry = {
  "roi": {"x1": 0.05, "y1": 0.25, "x2": 0.95, "y2": 0.98},
  "line": {"x1": 0.0, "y1": 0.55, "x2": 1.0, "y2": 0.55},
}
c.save(update_fields=["anpr_geometry", "modified_on"])
```

---

## Verify

1. `redis-cli ping`
2. `systemctl status gtms-anpr-reader` → active  
3. Logs: `[ANPR_READER] active cameras=…`  
4. Vehicle crosses line → `[ANPR] enqueued`  
5. Celery → `[ANPR_GATE] CHECK_IN` / `CHECK_OUT`  
6. Visitors list → plate + CCTV badge  
