# CCTV Vehicle Gate — Phased Plan

**Status:** Phase 2 complete — live HLS view (MediaMTX)  
**Last updated:** 2026-09-08  
**Server constraint:** 4 vCPU / 8 GB — pilot = **1 camera**, sample later at 5–10s (not 100-site scale yet)

**How live view works (store / load / MediaMTX / HLS / server install):**  
→ see **[`CCTV_LIVE_VIEW.md`](CCTV_LIVE_VIEW.md)**

## In one sentence

Admins attach RTSP cameras to existing `LocationSite`s; later phases add live view, plate OCR sampling, and visitor check-in/out — **without changing site boundary behaviour**.

## Decisions (locked)

| Topic | Decision |
|--------|----------|
| Product | Same Visitor Management menu / list |
| Person | Create/reuse `Visitor` with vehicle number (plate) |
| CCTV entry rules | Straight `checked_in` / `checked_out` — no host, no photos |
| Camera modes | Support `toggle` / `in` / `out` (v1 UI has all; worker later) |
| Cross-site | Per-site open state (site B can check in while A still open) |
| Never left | Stay open (no auto-checkout timeout) |
| Live view ACL | Superadmin, org admin, users with site access |
| OCR | Reuse existing vehicle YOLO+OCR via worker (Phase 3+) |
| Streaming | MediaMTX (or similar) beside app (Phase 2) |
| Schema safety | **Additive only** — do not alter `LocationSite` boundary fields or existing visitor APIs |

## Phase overview

| Phase | Goal | Status |
|-------|------|--------|
| **1** | Camera CRUD on site (name + RTSP + direction) | **Done** |
| **2** | Live browser view (MediaMTX + embed) | **Done** |
| **3** | Frame sample + OCR worker (1 camera pilot) | Pending |
| **4** | Check-in/out + cooldown + `entry_source=cctv` | Pending |
| **5** | Visitor list UX (CCTV badge, plate as title) | Pending |

Do **one phase at a time**.

---

## Phase 1 — CCTV configuration

**Goal:** Store cameras per site; edit on Organisation → Sites → Add/Edit Site.

### Delivered

- [x] Model `scheduler.SiteCamera` (FK → `LocationSite`) — **new table only**
- [x] Migration `0028_sitecamera` (CreateModel — no `LocationSite` AlterField)
- [x] API:
  - `GET /scheduler/sites/<site_id>/cameras/`
  - `PUT /scheduler/sites/<site_id>/cameras/` — replace list `{ "cameras": [...] }`
- [x] Site access: GET = site ACL; PUT = org admin / superadmin
- [x] Web: `SiteCctvCamerasPanel` on `SiteFormPage` (boundary / alert routing untouched)
- [x] API client: `getSiteCameras` / `saveSiteCameras`

### Live DB step (you run)

```bash
cd backendnew/gtms/patrol_backend
python manage.py migrate scheduler 0028
```

---

## Phase 2 — Live view (this phase)

**Goal:** Browser-playable HLS for cameras the user can access.

### Delivered

- [x] Auto `stream_path` on camera save (`cam-<uuid>`)
- [x] Best-effort MediaMTX path sync on camera save + `POST /scheduler/cctv/sync-mediamtx/`
- [x] `GET /scheduler/cctv/live-cameras/` — cameras on allowed sites + `hls_url`
- [x] Visitor menu tab **CCTV Live** (`/visitor/cctv-live`) + HLS.js player
- [x] Example config: [`mediamtx.yml.example`](mediamtx.yml.example)
- [x] Settings: `MEDIAMTX_ENABLED`, `MEDIAMTX_API_URL`, `MEDIAMTX_HLS_BASE_URL`
- [x] MediaMTX path sync forces `rtspTransport: tcp` (same as `ffplay -rtsp_transport tcp`)
- [x] `GET /scheduler/cctv/live-cameras/` auto re-registers paths (so first open works after MediaMTX restart; Sync still available)
- [x] Low-Latency HLS + player live-edge recovery
- [x] Prefer MediaMTX **WebRTC (WHEP)** for low latency; HLS fallback; container sized to 16:9 video

### Ops steps (you run on server)

1. Install/run MediaMTX using `docs/mediamtx.yml.example` (ports **8888** HLS, **9997** API).  
2. Ensure Django can reach `http://127.0.0.1:9997` and the browser can reach HLS  
   (if UI is remote, set `MEDIAMTX_HLS_BASE_URL` to a public/nginx-proxied URL, not localhost).  
3. Save site camera (or click **Sync MediaMTX** on CCTV Live) — pulls RTSP over **TCP**.  
4. Open **Visitor → CCTV Live** and select the camera.

### Verify Phase 2

1. MediaMTX process running  
2. Sync succeeds (synced ≥ 1)  
3. Video plays (or clear error if RTSP unreachable)  
4. User without site access does not see that site’s cameras  

### Out of scope for Phase 2

- OCR / check-in  
- Recording / playback history  

---

## Phase 3 — Sample + OCR (next, not started)

**Goal:** Every 5–10s grab a frame, run existing vehicle OCR library.

### Tasks (outline only)

- [ ] Dedicated queue / process; load YOLO once  
- [ ] Snapshot from MediaMTX or RTSP  
- [ ] Call shared detect+OCR code used by `/visitors/ai/extract/?type=vehicle`  

**Hardware:** 1 enabled camera only on this server.

---

## Phase 4 — Check-in / check-out

Outline only — plate → visitor entry (`entry_source=cctv`), cooldown, no host/photos.

## Phase 5 — List / reports UX

Outline only — CCTV badge, plate as primary label.

---

## Explicit non-goals (until asked)

- 100-site concurrent ANPR on 8 GB RAM  
- Changing site boundary polygons / breach alerts  

## File map

| Phase | Path |
|-------|------|
| 1 Model / migration | `scheduler/models.py`, `migrations/0028_sitecamera.py` |
| 1–2 API | `scheduler/views_cameras.py`, `scheduler/mediamtx.py`, `urls.py` |
| 1 Web config | `SiteCctvCamerasPanel.jsx`, `SiteFormPage.jsx` |
| 2 Live UI | `GTMS_NEw/.../CctvLiveView.jsx`, `Visitor.jsx`, `App.jsx` |
| 2 MediaMTX | `docs/mediamtx.yml.example`, `bin/mediamtx/` |
| 2 Explain / deploy | [`CCTV_LIVE_VIEW.md`](CCTV_LIVE_VIEW.md) |
