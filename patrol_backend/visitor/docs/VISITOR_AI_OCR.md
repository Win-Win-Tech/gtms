# Visitor AI OCR — ID & Vehicle extract (Iteration 2)

Separate guide for the **visitor AI assist** APIs. Manual entry stays the source of truth; AI only **prefills** fields (always editable).

---

## What it does

| `type` | Behaviour |
|--------|-----------|
| `id` | Detect ID-like region → OCR → extract IC / Aadhaar / passport-like number |
| `vehicle` | YOLO detect vehicle → crop → OCR → extract plate number |

If no ID / no vehicle / low confidence / timeout → soft fail JSON (`found: false`). **Never blocks** check-in.

---

## API (Postman)

**Endpoint:** `POST /visitors/ai/extract/`  
**Auth:** `Authorization: Bearer <TOKEN>`  
**Body:** `multipart/form-data`

| Field | Required | Values |
|-------|----------|--------|
| `type` | yes | `id` or `vehicle` |
| `image` | yes | image file (also accepts `file`, `id_image`, `vehicle_image`) |

### Postman steps
1. Method **POST** → `http://localhost:8000/visitors/ai/extract/`
2. Headers → Bearer token
3. Body → form-data:
   - `type` = `id` (text)
   - `image` = File (pick a MyKad / passport / Aadhaar photo)
4. Send. First call may be slow (model download + warm-up).

### Curl — ID

```bash
curl -X POST "http://localhost:8000/visitors/ai/extract/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "type=id" \
  -F "image=@/path/to/id.jpg"
```

**Success example**
```json
{
  "type": "id",
  "found": true,
  "number": "900101145678",
  "confidence": 0.91
}
```

**Not found**
```json
{
  "type": "id",
  "found": false,
  "number": null,
  "confidence": null
}
```

### Curl — vehicle

```bash
curl -X POST "http://localhost:8000/visitors/ai/extract/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "type=vehicle" \
  -F "image=@/path/to/car.jpg"
```

**Success example**
```json
{
  "type": "vehicle",
  "found": true,
  "number": "TN58B8050",
  "confidence": 0.88
}
```

**Not found**
```json
{
  "type": "vehicle",
  "found": false,
  "number": null,
  "confidence": null
}
```

Public response fields are only: `type`, `found`, `number`, `confidence` (same `number` key for ID and vehicle).

---

## Pipeline (what happens each step)

```text
1. Upload          → read multipart image
2. Preprocess      → EXIF fix, RGB, resize max side 1280, reject >8MB
3. Detect
     id      → OpenCV document quad (or custom YOLO if VISITOR_AI_ID_WEIGHTS set)
     vehicle → YOLOv8n COCO (car/truck/bus/motorcycle)
4. Crop ROI        → pad around box (or full frame if no box for id)
5. PaddleOCR       → text lines + scores (CPU)
6. Parse           → regex: MyKad / Aadhaar / passport OR plate patterns
7. Gate            → confidence / timeout / busy
8. JSON response   → { type, found, number, confidence }
```

---

## Code map (what each file does)

| File | Role |
|------|------|
| `visitor/views_ai.py` | Django API view: auth, multipart, calls pipeline |
| `visitor/ai/__init__.py` | Public export `extract_from_upload` |
| `visitor/ai/preprocess.py` | Load image, EXIF, resize, size limit |
| `visitor/ai/detect.py` | YOLO vehicle detect; ID region (OpenCV / optional YOLO) |
| `visitor/ai/ocr_engine.py` | Lazy singleton PaddleOCR |
| `visitor/ai/parse.py` | Regex extract ID number / plate |
| `visitor/ai/pipeline.py` | Orchestrates steps, timeout, concurrency semaphore |
| `visitor/urls.py` | Route `ai/extract/` |

### Important design notes
- **COCO YOLO has no “ID card” class.** Default ID detect uses OpenCV document-like contours. You can plug custom weights via `VISITOR_AI_ID_WEIGHTS`.
- Models load **once** (lazy singleton) and stay warm.
- Default **1 concurrent** AI job (`VISITOR_AI_MAX_CONCURRENT`) for 4 vCPU / 8 GB.
- Soft timeout default **45s** (`VISITOR_AI_TIMEOUT_SEC`).
- Light RAM defaults: angle-cls **off**, **2** CPU threads, idle unload after **90s**.

---

## Environment variables (optional)

| Env | Default | Meaning |
|-----|---------|---------|
| `VISITOR_AI_YOLO_WEIGHTS` | `yolov8n.pt` | Ultralytics weights path/name |
| `VISITOR_AI_ID_WEIGHTS` | _(empty)_ | Optional custom ID YOLO `.pt` |
| `VISITOR_AI_OCR_LANG` | `en` | PaddleOCR language |
| `VISITOR_AI_USE_GPU` | `false` | Set `true` only if CUDA available |
| `VISITOR_AI_ENABLE_MKLDNN` | `false` | Keep off under Django — MKLDNN causes `could not execute a primitive` |
| `VISITOR_AI_USE_ANGLE_CLS` | `false` | Extra Paddle angle model (~+RAM); leave off on 8 GB |
| `VISITOR_AI_CPU_THREADS` | `2` | Paddle / BLAS thread cap |
| `VISITOR_AI_IDLE_UNLOAD_SEC` | `90` | Unload OCR/YOLO after idle seconds (`0` = keep warm) |
| `VISITOR_AI_VEHICLE_SKIP_YOLO` | `false` | OCR full frame for plates (never load torch/YOLO) |
| `VISITOR_AI_TIMEOUT_SEC` | `45` | Soft timeout seconds (CPU OCR often needs >8s) |
| `VISITOR_AI_MIN_OCR_CONF` | `0.45` | Min OCR score to accept |
| `VISITOR_AI_MAX_CONCURRENT` | `1` | Parallel AI jobs |

---

## Packages: venv vs system

### Install in the **project venv** (required)

These must be inside your Django virtualenv:

| Package | Purpose |
|---------|---------|
| `ultralytics` | YOLOv8 (pulls `torch`, `opencv`, etc.) |
| `paddlepaddle` | Paddle runtime (**CPU** build for this server) |
| `paddleocr` | OCR API |
| `opencv-python-headless` | OpenCV without GUI (ID region + YOLO deps) |

**Do not** put YOLO/Paddle in global system Python unless you know you want that.

### System / OS packages (Ubuntu — often needed)

Install with `apt` (outside venv) so Paddle/OpenCV can link libraries:

- `libgl1` / `libglib2.0-0` (OpenCV)
- build tools sometimes needed for wheels: `build-essential`

### System / OS (Windows)

- Usually **no** extra global packages if you use official CPU wheels
- Install **Microsoft Visual C++ Redistributable** if `torch` / `paddle` fail to load DLLs
- Prefer Python **3.10–3.12** 64-bit

---

## Setup — Ubuntu (production-like 4 vCPU / 8 GB)

```bash
# 1) OS libs
sudo apt update
sudo apt install -y libgl1 libglib2.0-0 libgomp1

# 2) Activate project venv
cd /path/to/backendnew/gtms
source venv/bin/activate   # or: source .venv/bin/activate

# 3) Upgrade pip
pip install -U pip setuptools wheel

# 4) Install AI deps (CPU) — ORDER MATTERS
# Step A: CPU torch FIRST (stops ultralytics from pulling NVIDIA CUDA wheels)
pip install --index-url https://download.pytorch.org/whl/cpu torch torchvision

# Step B: then paddle + ultralytics (use the pin file, or install one-by-one)
pip install -r patrol_backend/visitor/requirements-visitor-ai.txt

# If paddlepaddle pin fails, try official CPU index:
# pip install paddlepaddle==3.0.0 -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
# pip install paddleocr==2.9.1 ultralytics==8.3.70 opencv-python-headless==4.10.0.84

# 5) Restart Django
cd patrol_backend
python manage.py runserver 0.0.0.0:8000
```

First request downloads `yolov8n.pt` and PaddleOCR models into cache — allow time / network.

**RAM tip:** keep Gunicorn workers low (e.g. 2) when AI shares the box with face indexing.

---

## Setup — Windows (local Postman testing)

```bat
:: 1) Open "x64 Native Tools" or normal cmd with venv
cd C:\path\to\backendnew\gtms
venv\Scripts\activate

:: 2) pip
python -m pip install -U pip setuptools wheel

:: 3) Paddle CPU (check https://www.paddlepaddle.org.cn for exact wheel if needed)
pip install paddlepaddle==3.0.0
pip install paddleocr==2.9.1
pip install ultralytics==8.3.70
pip install opencv-python-headless==4.10.0.84

:: 4) Run
cd patrol_backend
python manage.py runserver 8000
```

If `import paddle` fails on Windows, install **VC++ Redistributable** and retry with the official paddle CPU wheel for your Python version.

---

## requirements-visitor-ai.txt

See sibling file `visitor/requirements-visitor-ai.txt` — install **in addition to** main `requirements.txt`, not as a replacement.

---

## Hardware guidance (4 vCPU / 8 GB)

| Do | Don’t |
|----|--------|
| Resize to 1280 (already coded) | Full 12MP OCR |
| Angle-cls off + idle unload (defaults) | Keep angle-cls + warm forever on 8 GB |
| `VISITOR_AI_VEHICLE_SKIP_YOLO=1` on tight RAM | Load torch/YOLO just to test ID OCR |
| `MAX_CONCURRENT=1` | Many parallel OCR jobs |
| Soft fail on timeout | Hang the request forever |

Expected warm latency: roughly **1–2+ seconds** on CPU (varies by image).

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| 503 `dependency_missing` | Install paddleocr / ultralytics in **same** venv as Django |
| `could not execute a primitive` | Paddle oneDNN/MKLDNN under Django threads — keep `VISITOR_AI_ENABLE_MKLDNN` off (default) and restart server |
| First call very slow / `timeout` | Normal — model download; raise `VISITOR_AI_TIMEOUT_SEC` or retry |
| `busy` | Wait; only 1 job by default |
| Always `no_vehicle` | Use a clear car+plate photo; check YOLO loaded |
| Always `no_id_document` | Clearer ID photo; number must match MyKad/Aadhaar/passport patterns |
| OOM / killed | Lower workers; ensure only one AI concurrent; set `VISITOR_AI_VEHICLE_SKIP_YOLO=1`; wait for idle unload (~90s) |
| RAM stays high after AI | Wait `VISITOR_AI_IDLE_UNLOAD_SEC` (default 90); check logs for “Unloading PaddleOCR”; note Python may not return all RSS to OS |

---

## Frontend (later)

Wire Manual Entry:
- After ID proof upload → `type=id` → fill `ic_passport_number` from `number` if `found`
- After vehicle photo → `type=vehicle` → fill `vehicle_number` from `number` if `found`

Not required to test this API in Postman.
