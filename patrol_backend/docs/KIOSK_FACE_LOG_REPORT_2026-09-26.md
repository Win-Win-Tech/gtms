# Kiosk Face Log Report — 2026-09-26 (11:09–11:39 UTC)

**Source:** `kiosk-face.log` lines 1–152  
**Primary site:** **Indus Garments** (`8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5`)  
**Kiosk actor (admin):** **Poongodi** (`admin@ig.com`)  
**Secondary site (1 request):** **Test** (`259ea9c6-9e14-44b2-ad8a-52d4385060de`)  

**Media base (browser):** `http://147.93.27.224/media/`  
(Adjust host if you use a domain/HTTPS in front of nginx.)

---

## Executive summary — root causes

| Cause | Count (this window) | Meaning |
|-------|---------------------|---------|
| **`face_not_detected`** | **many** (see table — each row has full URL) | Camera frame had **no usable face**. **Main problem.** |
| **`ambiguous_match`** | **3** | Face seen, but match unclear — **no logphoto saved**. |
| **`multiple_faces_detected`** | **1** | More than one face — **no logphoto saved**. |
| **`checkout_too_early`** | **1** | Face OK (**Dineshkumar**); checkout &lt; 5 min after check-in. |
| **Success** | **check-ins/outs in table** | Punches completed normally (fast, &lt; ~0.6 s). |

**Not a slow-server / 55-user FAISS issue here.** Failures are almost all **capture quality** (`face_not_detected`). Successful punches are fast (`face_ms` ~50–280 ms, total &lt; 600 ms).

---

## Photo URL rules

| Type | When saved? | Browser path |
|------|-------------|--------------|
| **Logphoto** (failed capture) | Only for `face_not_detected`, `face_not_matched`, `no_enrolled_faces` | `/media/logphoto/2026-09-26/{location_id}/{code}_{random}.jpg` |
| **Registered face** | Always (enrollment) | `/media/user_faces/...` from DB `face_photo` |
| **`ambiguous_match`** | **Not saved today** (code not in `FACE_IDENTIFY_ERROR_CODES`) | — |
| **`checkout_too_early` / success** | No failure logphoto | Use registered face only |

### Logphoto URLs

Full pasteable URLs are in the **Logphoto** column of the request-by-request table below  
(`http://147.93.27.224/media/` + path from `Face identify error image saved`).

**No logphoto** for: `ambiguous_match`, `multiple_faces_detected`, `checkout_too_early`, successful punches.

---

## Registered face URLs (matched users)

| User | Registered photo (paste in browser) |
|------|-------------------------------------|
| Dineshkumar | http://147.93.27.224/media/user_faces/DINESHKUMAR.jpeg |
| Thirumani | http://147.93.27.224/media/user_faces/thirumani_3.jpeg |
| ManiKandan | http://147.93.27.224/media/user_faces/MANIKANDAN.jpeg |
| Sureshkumar | http://147.93.27.224/media/user_faces/SURESHKUMAR.jpeg |
| Thirupathi Raja | http://147.93.27.224/media/user_faces/Thirupathi.jpg |
| Sakthivel | http://147.93.27.224/media/user_faces/sakthivel.jpeg |
| Ram Ganesh | http://147.93.27.224/media/user_faces/RAMGANESH.jpeg |
| Chandru | http://147.93.27.224/media/user_faces/CHANDRU_EB7017X.jpeg |
| Palani selvam | http://147.93.27.224/media/user_faces/palanisamy.jpeg |
| Ravi (Test org) | http://147.93.27.224/media/user_faces/1000541792.jpg |

---

## Request-by-request

### Indus Garments — actor Poongodi

| # | Time (UTC) | Matched user | Issue / result | Response | face_ms | dist | Logphoto (paste in browser) | Registered photo |
|---|------------|--------------|----------------|----------|---------|------|------------------------------|------------------|
| 1 | 11:09:04 | — | Multiple faces | `400 multiple_faces_detected` | — | — | — (not saved) | — |
| 2 | 11:09:34 | **Dineshkumar** | Face OK → checkout too early (&lt;5 min) | `400 checkout_too_early` | 133 | 0.43 | — (match ok, no failure photo) | http://147.93.27.224/media/user_faces/DINESHKUMAR.jpeg |
| 3 | 11:26:40 | — | No face | `400 face_not_detected` | 58 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_2c74c4211c97.jpg | — |
| 4 | 11:26:58 | **Thirumani** | Checkout OK | `200` | 114 | 0.40 | — | http://147.93.27.224/media/user_faces/thirumani_3.jpeg |
| 5 | 11:27:17 | — | No face | `400 face_not_detected` | 59 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_a6f3fe65d852.jpg | — |
| 6 | 11:27:27 | — | No face | `400 face_not_detected` | 62 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_3fff7455ed09.jpg | — |
| 7 | 11:27:36 | **ManiKandan** | Check-in OK | `201` | 114 | 0.33 | — | http://147.93.27.224/media/user_faces/MANIKANDAN.jpeg |
| 8 | 11:27:57 | — | No face | `400 face_not_detected` | 77 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_eef739f99d78.jpg | — |
| 9 | 11:28:06 | — | No face | `400 face_not_detected` | 80 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_617c5564ac22.jpg | — |
| 10 | 11:28:16 | — | No face | `400 face_not_detected` | 116 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_5f647fd8c84d.jpg | — |
| 11 | 11:28:27 | — | No face | `400 face_not_detected` | 51 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_bc090a95ab7c.jpg | — |
| 12 | 11:28:36 | — | No face | `400 face_not_detected` | 77 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_43c659c5a40c.jpg | — |
| 13 | 11:28:45 | — | Ambiguous | `400 ambiguous_match` | 177 | 0.46 | — (not saved) | — |
| 14 | 11:28:59 | — | No face | `400 face_not_detected` | 83 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_30cb30b21dba.jpg | — |
| 15 | 11:29:08 | — | No face | `400 face_not_detected` | 69 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_284538d57c03.jpg | — |
| 16 | 11:29:18 | — | No face | `400 face_not_detected` | 68 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_5b6505d0320e.jpg | — |
| 17 | 11:29:34 | — | Ambiguous | `400 ambiguous_match` | 122 | 0.53 | — (not saved) | — |
| 18 | 11:29:47 | **Sureshkumar** | Check-in OK | `201` | 143 | 0.41 | — | http://147.93.27.224/media/user_faces/SURESHKUMAR.jpeg |
| 19 | 11:30:06 | **Thirupathi Raja** | Check-in OK | `201` | 178 | 0.26 | — | http://147.93.27.224/media/user_faces/Thirupathi.jpg |
| 20 | 11:30:23 | **Sakthivel** | Check-in OK | `201` | 256 | 0.45 | — | http://147.93.27.224/media/user_faces/sakthivel.jpeg |
| 21 | 11:30:53 | **Dineshkumar** | Checkout OK | `200` | 115 | 0.37 | — | http://147.93.27.224/media/user_faces/DINESHKUMAR.jpeg |
| 22 | 11:31:23 | **Ram Ganesh** | Checkout OK | `200` | 114 | 0.44 | — | http://147.93.27.224/media/user_faces/RAMGANESH.jpeg |
| 23 | 11:31:53 | **Chandru** | Check-in OK | `201` | 58 | 0.38 | — | http://147.93.27.224/media/user_faces/CHANDRU_EB7017X.jpeg |
| 24 | 11:36:30 | — | No face | `400 face_not_detected` | 128 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_c6b25aca54ed.jpg | — |
| 25 | 11:36:59 | — | No face | `400 face_not_detected` | 80 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_59ff98717ab4.jpg | — |
| 26 | 11:37:30 | **Dineshkumar** | Check-in OK | `201` | 219 | 0.33 | — | http://147.93.27.224/media/user_faces/DINESHKUMAR.jpeg |
| 27 | 11:37:53 | — | No face | `400 face_not_detected` | 126 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_620b3e612a8c.jpg | — |
| 28 | 11:39:02 | — | No face | `400 face_not_detected` | 86 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_95f20330f978.jpg | — |
| 29 | 11:39:14 | — | No face | `400 face_not_detected` | 120 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_23d8216e8c30.jpg | — |
| 30 | 11:45:31 | — | No face | `400 face_not_detected` | 69 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_d0ca992f88cd.jpg | — |
| 31 | 11:52:06 | **Palani selvam** | Check-in OK | `201` | 275 | 0.43 | — | http://147.93.27.224/media/user_faces/palanisamy.jpeg |
| 32 | 11:52:23 | **Ram Ganesh** | Check-in OK | `201` | 222 | 0.38 | — | http://147.93.27.224/media/user_faces/RAMGANESH.jpeg |
| 33 | 11:52:34 | — | No face | `400 face_not_detected` | 86 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_9d3f4ee3ac07.jpg | — |
| 34 | 11:52:44 | — | No face | `400 face_not_detected` | 98 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_14f46ed76ca7.jpg | — |
| 35 | 11:52:54 | — | No face | `400 face_not_detected` | 52 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_eb4d6bd9e209.jpg | — |
| 36 | 11:53:03 | — | No face | `400 face_not_detected` | 87 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_10ce3f53616b.jpg | — |
| 37 | 11:53:13 | — | No face | `400 face_not_detected` | 67 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_c2abfd5f5420.jpg | — |
| 38 | 11:53:22 | — | No face | `400 face_not_detected` | 54 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_7b720f0c757e.jpg | — |
| 39 | 11:53:34 | — | No face | `400 face_not_detected` | 52 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_6e0404503871.jpg | — |
| 40 | 11:53:44 | — | No face | `400 face_not_detected` | 59 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_35b8043554a4.jpg | — |
| 41 | 11:53:53 | — | No face | `400 face_not_detected` | 89 | — | http://147.93.27.224/media/logphoto/2026-09-26/8a1a7dc0-3e63-4aa8-990a-eb9b0ffe68c5/face_not_detected_91441d796e56.jpg | — |
| 42 | 11:54:03 | — | Ambiguous | `400 ambiguous_match` | 289 | 0.58 | — (not saved) | — |

### Test location — actor admin@test.com

| # | Time (UTC) | Matched user | Issue / result | Response | face_ms | dist | Logphoto (paste in browser) | Registered photo |
|---|------------|--------------|----------------|----------|---------|------|------------------------------|------------------|
| T1 | 11:20:36 | **Ravi** | Face match OK (punch lines may be truncated in snippet) | success match | 957 | 0.39 | — | http://147.93.27.224/media/user_faces/1000541792.jpg |
| T2 | 11:47:39 | — | No face | `400 face_not_detected` | 665 | — | http://147.93.27.224/media/logphoto/2026-09-26/259ea9c6-9e14-44b2-ad8a-52d4385060de/face_not_detected_b508fdb65e35.jpg | — |
| T3 | 11:48:35 | **Ravi** | Face match OK | success match | 768 | 0.40 | — | http://147.93.27.224/media/user_faces/1000541792.jpg |

---

## API response shapes (what device gets)

**`face_not_detected`**
```json
{ "success": false, "code": "face_not_detected", "message": "No face found in uploaded image" }
```
HTTP **400**

**`ambiguous_match`**
```json
{ "success": false, "code": "ambiguous_match",
  "message": "Face match is unclear. Please stand still, look at the camera directly, and try again." }
```
HTTP **400**

**`checkout_too_early`** (Dineshkumar 11:09:34)
```json
{ "success": false, "code": "checkout_too_early",
  "message": "Checkout allowed after 5 minutes from checkin",
  "user_id": "3b5646f1-5529-4f90-897e-00e19984338f",
  "has_shift": true,
  "min_checkout_minutes": 5,
  "remaining_seconds": <remaining> }
```
HTTP **400**

**Success check-in** → HTTP **201** `success: true`  
**Success check-out** → HTTP **200** `success: true`

---

## Cause analysis (Indus)

1. **Dominant issue = capture, not matching DB**  
   16× `face_not_detected` with very low `face_ms` (~50–130 ms) means detect failed early — person not facing camera, too far, motion blur, lighting, or empty/wrong frame.

2. **When face is clear, system works**  
   8 successful punches; distances 0.26–0.45 (acceptable). Punch path &lt; 300 ms.

3. **`checkout_too_early` is policy, not face bug**  
   Dineshkumar matched; rule `FACE_KIOSK_MIN_CHECKOUT_MINUTES=5`. Later at 11:30:53 checkout succeeded.

4. **`ambiguous_match`**  
   Weak / mid-crowd look-alike. No logphoto saved for this code today — recommend adding `ambiguous_match` to `FACE_IDENTIFY_ERROR_CODES` so you can compare live vs enrolled.

5. **Not “2–3 minutes per request” in this log**  
   Worst face here ~256 ms; worst total success ~578 ms.

---

