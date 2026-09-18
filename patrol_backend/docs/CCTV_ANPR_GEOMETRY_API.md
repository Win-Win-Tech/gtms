# CCTV ANPR Zone (ROI / Line) — Mobile API

Auth: `Authorization: Bearer <JWT>`  
Base URL: replace `$BASE` (example `https://your-server.example.com`)

ROI and line are stored on each camera as `anpr_geometry`. There is no separate geometry URL — use the camera APIs below.

Play video with `rtsp_url` from the response (in-app RTSP player).

| Variable | Example |
|----------|---------|
| `$TOKEN` | JWT access token |
| `$SITE_ID` | `24e77a27-8b48-403d-93ee-cf272ac377e4` |
| `$BASE` | `https://api.example.com` |

---

## API index

| Method | Endpoint | Purpose |
|--------|----------|---------|
| `GET` | `/scheduler/cctv/live-cameras/` | List enabled cameras for live view + current `anpr_geometry` + `rtsp_url` |
| `GET` | `/scheduler/sites/<site_id>/cameras/` | List all cameras for a site (required before save) |
| `PUT` | `/scheduler/sites/<site_id>/cameras/` | Create / update / delete / clear ROI & line (full camera list replace) |

| Client action | How |
|---------------|-----|
| Load overlays | Use `anpr_geometry` from GET |
| Draw / move (local) | Edit in app memory (normalized 0..1) |
| Save ROI / line | PUT with updated `anpr_geometry` |
| Delete ROI only | PUT omitting `roi` (keep `line` if needed) |
| Delete line only | PUT omitting `line` (keep `roi` if needed) |
| Clear all | PUT with `"anpr_geometry": {}` |

---

## Permissions

| Endpoint | Who |
|----------|-----|
| `GET` live-cameras / site cameras | Authenticated user with site access |
| `PUT` site cameras | Org admin (same organisation as the site) or superadmin |

---

## `anpr_geometry` format

Coordinates are **normalized floats `0..1`** on the full video frame (not screen pixels).

```text
(0,0) ───────────────► x = 1 (right)
  │
  │   roi  = box  (x1,y1) → (x2,y2)
  │   line = segment (x1,y1) → (x2,y2)
  ▼
  y = 1 (bottom)
```

```json
{
  "roi":  { "x1": 0.10, "y1": 0.15, "x2": 0.90, "y2": 0.95 },
  "line": { "x1": 0.00, "y1": 0.55, "x2": 1.00, "y2": 0.55 }
}
```

| Value | Meaning |
|-------|---------|
| both `roi` + `line` | Box + tripwire |
| only `roi` | Detection zone only |
| only `line` | Tripwire only |
| `{}` | No zone (cleared) |

Convert touch points:

```text
x_norm = x_in_video_content / video_width
y_norm = y_in_video_content / video_height
```

Related camera fields (echo them on PUT):

| Field | Values | Meaning |
|-------|--------|---------|
| `gate_mode` | `parked_toggle` \| `line_direction` | When to record |
| `direction` | `toggle` \| `in` \| `out` | Lane type |

**PUT replaces the entire camera list for the site.** Camera `id` values are new after every successful PUT — use IDs from the PUT response. Always echo every camera and fields (`name`, `rtsp_url`, `direction`, `gate_mode`, `is_enabled`, `sort_order`, `stream_path`) so nothing is reset. If `gate_mode` is omitted, server defaults to `parked_toggle`.

ANPR reader picks up geometry on its refresh interval (default up to ~5 minutes) or after reader restart.

---

## 1. List live cameras (read geometry + RTSP)

### Request

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  "$BASE/scheduler/cctv/live-cameras/"
```

### Request (filter by site)

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  "$BASE/scheduler/cctv/live-cameras/?site_id=$SITE_ID"
```

### Success response `200`

```json
{
  "mediamtx_hls_base": "http://127.0.0.1:8888",
  "mediamtx_webrtc_base": "http://127.0.0.1:8889",
  "results": [
    {
      "id": "be9945ab-451d-4091-b694-bd90288d17df",
      "name": "Gate",
      "site_id": "24e77a27-8b48-403d-93ee-cf272ac377e4",
      "site_name": "Main Gate",
      "direction": "toggle",
      "gate_mode": "line_direction",
      "is_enabled": true,
      "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
      "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
      "hls_url": "http://127.0.0.1:8888/cam-be9945ab-451d-4091-b694-bd90288d17df/index.m3u8",
      "whep_url": "http://127.0.0.1:8889/cam-be9945ab-451d-4091-b694-bd90288d17df/whep",
      "sort_order": 0,
      "anpr_geometry": {
        "roi": {
          "x1": 0.1,
          "y1": 0.15,
          "x2": 0.9,
          "y2": 0.95
        },
        "line": {
          "x1": 0.0,
          "y1": 0.55,
          "x2": 1.0,
          "y2": 0.55
        }
      }
    }
  ]
}
```

Use `rtsp_url` for playback. Use `anpr_geometry` for overlays.  
`hls_url` / `whep_url` / `mediamtx_*` can be ignored if you play RTSP natively.

### Empty geometry example

```json
"anpr_geometry": {}
```

### Error responses

**`401` Unauthorized**

```json
{
  "detail": "Authentication credentials were not provided."
}
```

**`200` with no cameras** (no access / none enabled)

```json
{
  "mediamtx_hls_base": "http://127.0.0.1:8888",
  "mediamtx_webrtc_base": "http://127.0.0.1:8889",
  "results": []
}
```

---

## 2. List site cameras (read before save)

### Request

```bash
curl -sS \
  -H "Authorization: Bearer $TOKEN" \
  -H "Accept: application/json" \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

### Success response `200`

```json
[
  {
    "id": "be9945ab-451d-4091-b694-bd90288d17df",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "line_direction",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
    "anpr_geometry": {
      "line": {
        "x1": 0.0,
        "y1": 0.55,
        "x2": 1.0,
        "y2": 0.55
      }
    },
    "created_on": "2026-09-17T10:00:00.000000Z",
    "modified_on": "2026-09-18T08:00:00.000000Z"
  }
]
```

### Error responses

**`403` no site access**

```json
{
  "detail": "You do not have permission to perform this action."
}
```

**`404` site not found**

```json
{
  "detail": "Not found."
}
```

---

## 3. Save cameras (create / edit / delete / clear geometry)

### Endpoint

```http
PUT /scheduler/sites/<site_id>/cameras/
Content-Type: application/json
```

### Body fields (each camera)

| Field | Required | Notes |
|-------|----------|--------|
| `name` | yes | Unique per site |
| `rtsp_url` | yes | Must start with `rtsp://` or `rtsps://` |
| `direction` | no | `toggle` \| `in` \| `out` (default `toggle`) |
| `gate_mode` | no | `parked_toggle` \| `line_direction` (default `parked_toggle`) |
| `is_enabled` | no | default `true` |
| `sort_order` | no | default `0` |
| `stream_path` | no | Echo existing value |
| `anpr_geometry` | no | Object; `{}` clears zone |

### Save flow

1. `GET /scheduler/sites/$SITE_ID/cameras/`
2. Build `cameras` array for **all** cameras from that response
3. Change `anpr_geometry` only for the camera being edited
4. `PUT` the full list
5. Update local state from PUT response (`id` values change)

---

### Scenario A — Create / set line only

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "line_direction",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
        "anpr_geometry": {
          "line": {
            "x1": 0.0,
            "y1": 0.55,
            "x2": 1.0,
            "y2": 0.55
          }
        }
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "a1b2c3d4-1111-2222-3333-444455556666",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "line_direction",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-a1b2c3d4-1111-2222-3333-444455556666",
    "anpr_geometry": {
      "line": {
        "x1": 0.0,
        "y1": 0.55,
        "x2": 1.0,
        "y2": 0.55
      }
    },
    "created_on": "2026-09-18T09:30:00.000000Z",
    "modified_on": "2026-09-18T09:30:00.000000Z"
  }
]
```

---

### Scenario B — Create / set ROI only

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "parked_toggle",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
        "anpr_geometry": {
          "roi": {
            "x1": 0.1,
            "y1": 0.2,
            "x2": 0.9,
            "y2": 0.95
          }
        }
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "b2c3d4e5-2222-3333-4444-555566667777",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "parked_toggle",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-b2c3d4e5-2222-3333-4444-555566667777",
    "anpr_geometry": {
      "roi": {
        "x1": 0.1,
        "y1": 0.2,
        "x2": 0.9,
        "y2": 0.95
      }
    },
    "created_on": "2026-09-18T09:31:00.000000Z",
    "modified_on": "2026-09-18T09:31:00.000000Z"
  }
]
```

---

### Scenario C — Create / update both ROI and line

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "line_direction",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
        "anpr_geometry": {
          "roi": {
            "x1": 0.1,
            "y1": 0.2,
            "x2": 0.9,
            "y2": 0.95
          },
          "line": {
            "x1": 0.0,
            "y1": 0.55,
            "x2": 1.0,
            "y2": 0.55
          }
        }
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "c3d4e5f6-3333-4444-5555-666677778888",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "line_direction",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-c3d4e5f6-3333-4444-5555-666677778888",
    "anpr_geometry": {
      "roi": {
        "x1": 0.1,
        "y1": 0.2,
        "x2": 0.9,
        "y2": 0.95
      },
      "line": {
        "x1": 0.0,
        "y1": 0.55,
        "x2": 1.0,
        "y2": 0.55
      }
    },
    "created_on": "2026-09-18T09:32:00.000000Z",
    "modified_on": "2026-09-18T09:32:00.000000Z"
  }
]
```

---

### Scenario D — Delete line only (keep ROI)

Omit the `line` key.

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "parked_toggle",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
        "anpr_geometry": {
          "roi": {
            "x1": 0.1,
            "y1": 0.2,
            "x2": 0.9,
            "y2": 0.95
          }
        }
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "d4e5f6a7-4444-5555-6666-777788889999",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "parked_toggle",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-d4e5f6a7-4444-5555-6666-777788889999",
    "anpr_geometry": {
      "roi": {
        "x1": 0.1,
        "y1": 0.2,
        "x2": 0.9,
        "y2": 0.95
      }
    },
    "created_on": "2026-09-18T09:33:00.000000Z",
    "modified_on": "2026-09-18T09:33:00.000000Z"
  }
]
```

---

### Scenario E — Delete ROI only (keep line)

Omit the `roi` key.

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "line_direction",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
        "anpr_geometry": {
          "line": {
            "x1": 0.0,
            "y1": 0.55,
            "x2": 1.0,
            "y2": 0.55
          }
        }
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "e5f6a7b8-5555-6666-7777-888899990000",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "line_direction",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-e5f6a7b8-5555-6666-7777-888899990000",
    "anpr_geometry": {
      "line": {
        "x1": 0.0,
        "y1": 0.55,
        "x2": 1.0,
        "y2": 0.55
      }
    },
    "created_on": "2026-09-18T09:34:00.000000Z",
    "modified_on": "2026-09-18T09:34:00.000000Z"
  }
]
```

---

### Scenario F — Clear all (ROI + line)

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "parked_toggle",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-be9945ab-451d-4091-b694-bd90288d17df",
        "anpr_geometry": {}
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "f6a7b8c9-6666-7777-8888-999900001111",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "parked_toggle",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-f6a7b8c9-6666-7777-8888-999900001111",
    "anpr_geometry": {},
    "created_on": "2026-09-18T09:35:00.000000Z",
    "modified_on": "2026-09-18T09:35:00.000000Z"
  }
]
```

---

### Scenario G — Two cameras on one site (edit only Gate)

Always send **every** camera. Change geometry only for the target camera.

#### Request

```bash
curl -sS -X PUT \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "cameras": [
      {
        "name": "Gate",
        "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
        "direction": "toggle",
        "gate_mode": "line_direction",
        "is_enabled": true,
        "sort_order": 0,
        "stream_path": "cam-aaaa",
        "anpr_geometry": {
          "line": {
            "x1": 0.0,
            "y1": 0.60,
            "x2": 1.0,
            "y2": 0.60
          }
        }
      },
      {
        "name": "Exit",
        "rtsp_url": "rtsp://admin:pass@192.168.1.11:554/Streaming/Channels/101",
        "direction": "out",
        "gate_mode": "line_direction",
        "is_enabled": true,
        "sort_order": 1,
        "stream_path": "cam-bbbb",
        "anpr_geometry": {
          "line": {
            "x1": 0.0,
            "y1": 0.50,
            "x2": 1.0,
            "y2": 0.50
          }
        }
      }
    ]
  }' \
  "$BASE/scheduler/sites/$SITE_ID/cameras/"
```

#### Success response `200`

```json
[
  {
    "id": "11111111-aaaa-bbbb-cccc-222222222222",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Gate",
    "rtsp_url": "rtsp://admin:pass@192.168.1.10:554/Streaming/Channels/101",
    "direction": "toggle",
    "gate_mode": "line_direction",
    "is_enabled": true,
    "sort_order": 0,
    "stream_path": "cam-11111111-aaaa-bbbb-cccc-222222222222",
    "anpr_geometry": {
      "line": {
        "x1": 0.0,
        "y1": 0.60,
        "x2": 1.0,
        "y2": 0.60
      }
    },
    "created_on": "2026-09-18T09:36:00.000000Z",
    "modified_on": "2026-09-18T09:36:00.000000Z"
  },
  {
    "id": "33333333-cccc-dddd-eeee-444444444444",
    "site": "24e77a27-8b48-403d-93ee-cf272ac377e4",
    "name": "Exit",
    "rtsp_url": "rtsp://admin:pass@192.168.1.11:554/Streaming/Channels/101",
    "direction": "out",
    "gate_mode": "line_direction",
    "is_enabled": true,
    "sort_order": 1,
    "stream_path": "cam-33333333-cccc-dddd-eeee-444444444444",
    "anpr_geometry": {
      "line": {
        "x1": 0.0,
        "y1": 0.50,
        "x2": 1.0,
        "y2": 0.50
      }
    },
    "created_on": "2026-09-18T09:36:00.000000Z",
    "modified_on": "2026-09-18T09:36:00.000000Z"
  }
]
```

---

### PUT error responses

**`403` not org admin**

```json
{
  "detail": "Only organisation admins can manage site cameras."
}
```

**`400` missing name**

```json
{
  "cameras": "Each camera needs a name."
}
```

**`400` missing RTSP**

```json
{
  "cameras": "Each camera needs an RTSP URL."
}
```

**`400` invalid RTSP scheme**

```json
{
  "cameras": "RTSP URL must start with rtsp:// or rtsps:// (camera: Gate)."
}
```

**`400` duplicate name**

```json
{
  "cameras": "Duplicate camera name: Gate"
}
```

**`401` Unauthorized**

```json
{
  "detail": "Authentication credentials were not provided."
}
```

---

## Recommended client flow

```text
Live screen:
  GET /scheduler/cctv/live-cameras/?site_id=<SITE>
  → play rtsp_url
  → draw anpr_geometry overlays

Edit / save zone:
  1. GET /scheduler/sites/<SITE>/cameras/
  2. User draws ROI / line (0..1)
  3. PUT full cameras[] (updated anpr_geometry on edited camera)
  4. Keep new ids from PUT response
```
