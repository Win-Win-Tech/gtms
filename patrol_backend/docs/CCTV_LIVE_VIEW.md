# CCTV Live View — How It Works (Phase 2)

**Audience:** Developers and ops deploying GTMS to a server.  
**Scope:** Live browser viewing only. **We do not record or store video.**  
**Related plan:** [`CCTV_VEHICLE_GATE_PLAN.md`](CCTV_VEHICLE_GATE_PLAN.md)

---

## 1. Big picture (one paragraph)

The **camera speaks RTSP** (a protocol browsers cannot play). **MediaMTX** sits beside Django, **pulls** that RTSP stream (over TCP), and **re-publishes** it as **HLS** (a format browsers *can* play). Django only **stores camera settings** (name, RTSP URL, site) and returns an **HLS link**. The **React app** loads that link with **hls.js**. Video bytes never go through Django.

```
┌─────────────┐   RTSP (TCP)   ┌────────────┐   HLS (:8888)   ┌──────────────┐
│ IP Camera / │ ─────────────► │  MediaMTX  │ ──────────────► │ Browser      │
│ NVR         │                │  (sidecar) │                 │ (hls.js)     │
└─────────────┘                └─────▲──────┘                 └──────▲───────┘
                                     │ API sync (:9997)              │
                                     │ path + RTSP URL               │ hls_url
                               ┌─────┴──────┐                 ┌──────┴───────┐
                               │   Django   │ ── JSON APIs ──►│ React GTMS   │
                               │ (config DB)│                 │ CCTV Live    │
                               └────────────┘                 └──────────────┘
```

---

## 2. What we store (and what we do **not**)

### Stored in the database (`scheduler.SiteCamera`)

| Field | Meaning |
|--------|---------|
| `site` | FK → `LocationSite` |
| `name` | Display name (e.g. Gate) |
| `rtsp_url` | Full RTSP URL including credentials |
| `direction` | `toggle` / `in` / `out` (for later OCR gate logic) |
| `is_enabled` | Shown on live view if true |
| `sort_order` | Ordering |
| `stream_path` | MediaMTX path id, e.g. `cam-eacfccfeb1c34208` |

Migration: `scheduler/migrations/0028_sitecamera.py` (additive — does not change site boundary fields).

### Not stored

- **No video recording** — MediaMTX `record: false` / sync payload `record: False`
- No HLS segments kept for playback history
- No frames saved in Phase 2 (OCR comes in Phase 3+)

Live view is **ephemeral**: MediaMTX pulls RTSP on demand when someone opens HLS, converts to short HLS segments in memory/temp, and discards them after viewers leave (`sourceOnDemandCloseAfter`).

---

## 3. End-to-end flow

### A. Admin configures a camera

1. **Organisation → Sites → Edit site** → CCTV cameras panel.  
2. Frontend `PUT /scheduler/sites/<site_id>/cameras/` with RTSP URL.  
3. Django saves `SiteCamera`, sets `stream_path = cam-<uuid16>`.  
4. Best-effort: Django calls MediaMTX Control API to register that path with:
   - `source` = RTSP URL  
   - `rtspTransport` = `tcp` (required for many cameras; same idea as `ffplay -rtsp_transport tcp`)  
   - `sourceOnDemand` = true  
   - `record` = false  

### B. User opens CCTV Live

1. Frontend `GET /scheduler/cctv/live-cameras/`.  
2. Django returns cameras the user may see (site ACL) plus:
   - `hls_url` e.g. `http://SERVER:8888/cam-xxx/index.m3u8`  
   - `mediamtx_hls_base`  
   - **Best-effort:** each camera path is re-registered on MediaMTX (so after MediaMTX
     reboot, opening CCTV Live works without clicking Sync).  
3. **Frontend does not play RTSP.** It plays `hls_url` with **hls.js** (retries while
   on-demand pull starts).  
4. Browser → MediaMTX HLS. MediaMTX → camera RTSP.  

### C. Sync button

`POST /scheduler/cctv/sync-mediamtx/` (org admin / superadmin) re-pushes all enabled cameras to MediaMTX. Use after MediaMTX restart (paths are in-memory / config API, not in our DB as video).

---

## 4. Why MediaMTX? What is its role?

| Question | Answer |
|----------|--------|
| What is it? | Open-source media server ([bluenviron/mediamtx](https://github.com/bluenviron/mediamtx)). Single binary — **not** a pip/npm package. |
| Why needed? | Cameras use **RTSP**. Chrome/Firefox do not play RTSP. MediaMTX converts **RTSP → HLS** (and can do WebRTC etc.). |
| Why not Django? | Streaming video continuously is not Django’s job; proxying RTSP through Python would burn CPU/RAM. |
| Why not VLC in the browser? | VLC is a desktop app. Browsers need HLS/WebRTC/MSE. |
| Ports we use | **8888** HLS (browser), **9997** Control API (Django), RTP/RTCP **8002/8003** (avoid Django `:8000`). |

**Role in one line:** MediaMTX is the **bridge** between the camera (RTSP) and the browser (HLS).

---

## 5. Why HLS?

| Protocol | Who uses it |
|----------|-------------|
| **RTSP** | Cameras / NVRs / `ffplay` / VLC |
| **HLS** | Browsers (via `hls.js` or Safari native) |

HLS = HTTP Live Streaming: a playlist (`.m3u8`) + short `.ts` / fMP4 segments over normal HTTP. Works through firewalls, easy CORS, no special browser plugin.

We chose HLS + MediaMTX for Phase 2 because it is simple to deploy and already proven with this camera over TCP.

---

## 6. Frontend — what we built

**App:** `GTMS_NEw` (React + Vite + MUI)

| Piece | Path / note |
|--------|-------------|
| Page | `src/pages/visitor/CctvLiveView.jsx` |
| Tab | `Visitor.jsx` → **CCTV Live** → `/visitor/cctv-live` |
| Site config UI | `SiteCctvCamerasPanel.jsx` on `SiteFormPage.jsx` |
| API client | `src/api/organisation/sites.js` — `getSiteCameras`, `saveSiteCameras`, `getCctvLiveCameras`, `syncCctvMediaMtx` |
| Player package | **`hls.js`** (`^1.5.17` in `package.json`) |

**UI behaviour**

- 1 camera → large tile filling content area  
- 2+ cameras → compact grid (2×2 style)  
- Green **LIVE** badge, camera name overlay, no play/pause chrome  
- Fullscreen button for sharpest view (same stream; more screen pixels)  
- **Live only · not recorded**

**npm install on server (frontend build machine):**

```bash
cd GTMS_NEw
npm install          # includes hls.js
npm run build
```

No other special frontend packages for CCTV beyond `hls.js`.

---

## 7. Backend — what we built

**App:** `backendnew/gtms/patrol_backend` (Django)

| Piece | Path |
|--------|------|
| Model | `scheduler/models.py` → `SiteCamera` |
| Migration | `scheduler/migrations/0028_sitecamera.py` |
| APIs | `scheduler/views_cameras.py` |
| MediaMTX helper | `scheduler/mediamtx.py` |
| URLs | `scheduler/urls.py` |
| Settings | `MEDIAMTX_ENABLED`, `MEDIAMTX_API_URL`, `MEDIAMTX_HLS_BASE_URL` |

**APIs**

| Method | Path | Purpose |
|--------|------|---------|
| GET/PUT | `/scheduler/sites/<site_id>/cameras/` | Configure cameras on a site |
| GET | `/scheduler/cctv/live-cameras/` | List playable cameras + `hls_url` |
| POST | `/scheduler/cctv/sync-mediamtx/` | Push paths to MediaMTX |

**Python packages:** Phase 2 uses existing **`requests`** to call MediaMTX HTTP API. **No new pip package** specifically for live view. (OpenCV in requirements is for visitor AI / later OCR — not for HLS playback.)

---

## 8. Moving this to a server — what to install

### A. Already part of GTMS (usual deploy)

1. Python venv + `pip install -r requirements.txt`  
2. `python manage.py migrate` (includes `0028_sitecamera`)  
3. Node + `npm install` / `npm run build` for frontend  
4. Run Django / gunicorn / nginx as you already do  

### B. Extra for CCTV Live — MediaMTX (required)

MediaMTX is **not** installed by pip. Install the binary:

```bash
# Example (Linux amd64) — use a current release from GitHub
cd /opt   # or next to the app
wget https://github.com/bluenviron/mediamtx/releases/download/v1.11.3/mediamtx_v1.11.3_linux_amd64.tar.gz
tar xf mediamtx_v1.11.3_linux_amd64.tar.gz
```

Or use the copy shipped under the repo:

```text
backendnew/gtms/patrol_backend/bin/mediamtx/
  mediamtx          # binary
  mediamtx.yml      # config
  start.sh          # helper
```

Config template: `docs/mediamtx.yml.example`

```bash
cd backendnew/gtms/patrol_backend/bin/mediamtx
./start.sh
# HLS  http://127.0.0.1:8888
# API  http://127.0.0.1:9997
# Log  mediamtx.log
```

**Keep MediaMTX running** (systemd recommended in production):

```ini
# /etc/systemd/system/mediamtx.service (sketch)
[Unit]
Description=MediaMTX for GTMS CCTV
After=network.target

[Service]
WorkingDirectory=/path/to/patrol_backend/bin/mediamtx
ExecStart=/path/to/patrol_backend/bin/mediamtx/mediamtx /path/to/patrol_backend/bin/mediamtx/mediamtx.yml
Restart=always

[Install]
WantedBy=multi-user.target
```

### C. Environment variables (Django)

| Variable | Default | Meaning |
|----------|---------|---------|
| `MEDIAMTX_ENABLED` | `true` | Call MediaMTX API on save/sync |
| `MEDIAMTX_API_URL` | `http://127.0.0.1:9997` | Django → MediaMTX (same machine OK) |
| `MEDIAMTX_HLS_BASE_URL` | `http://127.0.0.1:8888` | **URL the browser uses** |

**Important:** If users open GTMS from another PC (not localhost), `127.0.0.1:8888` in the browser will **fail**. Then:

1. Put nginx (or similar) in front of MediaMTX HLS, e.g. `https://your-domain/hls/` → `http://127.0.0.1:8888/`  
2. Set `MEDIAMTX_HLS_BASE_URL=https://your-domain/hls`  

Django can still use `MEDIAMTX_API_URL=http://127.0.0.1:9997` on the server.

### D. Firewall / network

| Port | Who needs it |
|------|----------------|
| Camera RTSP (often 554) | **Server → camera** (outbound) |
| MediaMTX 9997 | Django on same host (localhost) |
| MediaMTX 8888 | Browser → server (or via nginx) |
| Django API | Browser → server (as today) |

### E. After deploy checklist

1. MediaMTX process running (`pgrep mediamtx`)  
2. Migrate `0028` applied  
3. Camera RTSP works from server:  
   `ffplay -rtsp_transport tcp "rtsp://user:pass@CAMERA_IP:554/..."`  
4. Save camera in site form **or** click **Sync** on CCTV Live  
5. `curl -sS http://127.0.0.1:9997/v3/config/paths/list` shows `cam-...`  
6. `curl -sS -m 30 http://127.0.0.1:8888/cam-.../index.m3u8` returns `#EXTM3U`  
7. Open **Visitor → CCTV Live**  

---

## 9. Packages summary

| Layer | Package / binary | Purpose |
|-------|------------------|---------|
| Frontend | **hls.js** | Play HLS in Chrome/Firefox |
| Backend | **requests** (existing) | HTTP to MediaMTX API |
| Sidecar | **MediaMTX** binary | RTSP → HLS |
| Optional ops | **ffmpeg/ffplay** | Test RTSP from server (`-rtsp_transport tcp`) |
| Not used for live | VLC, OpenCV | VLC desktop-only; OpenCV later for OCR |

---

## 10. Security notes

- RTSP URLs often contain **passwords** in the DB and in MediaMTX path config. Restrict who can edit sites / Sync.  
- Do not expose MediaMTX **API :9997** to the public internet.  
- Prefer nginx auth / VPN in front of HLS in production.  
- Rotate camera passwords if they were shared in chats or screenshots.

---

## 11. File map (quick)

```
backendnew/gtms/patrol_backend/
  scheduler/
    models.py              # SiteCamera
    views_cameras.py       # cameras + live + sync APIs
    mediamtx.py            # sync/delete paths, build hls_url
    migrations/0028_*.py
  bin/mediamtx/            # binary + yml + start.sh
  docs/
    CCTV_VEHICLE_GATE_PLAN.md
    CCTV_LIVE_VIEW.md      # this file
    mediamtx.yml.example

GTMS_NEw/
  package.json             # hls.js
  src/pages/visitor/CctvLiveView.jsx
  src/pages/visitor/Visitor.jsx
  src/pages/organisation/SiteCctvCamerasPanel.jsx
  src/api/organisation/sites.js
```

---

## 12. What Phase 2 is **not**

- Not recording / DVR  
- Not plate OCR / auto check-in (Phases 3–5)  
- Not replacing site boundary / SOS features  

---

*Last updated: 2026-09-08*
