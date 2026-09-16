# License plate YOLO weights

Place a dedicated plate detector `.pt` here:

- `license_plate_detector.pt` ← preferred name
- `yolov8n-license-plate.pt`

Or set env:

```bash
export VISITOR_AI_PLATE_WEIGHTS=/path/to/your_plate_model.pt
```

## Install (local or live server)

Full steps: `docs/CCTV_ANPR_OPS.md` → **Install plate YOLO weights**.

Quick commands from `patrol_backend/`:

```bash
pip install huggingface_hub   # once

hf download joker5914/yolov8n-license-plate best.pt \
  --local-dir visitor/ai/weights

mv -f visitor/ai/weights/best.pt visitor/ai/weights/license_plate_detector.pt
ls -lh visitor/ai/weights/license_plate_detector.pt   # expect ~6 MB
```

Then restart `patrol-anpr-reader` and `patrol-anpr-celery`.

If neither local file nor env is set, code may try Ultralytics Hub
`keremberke/yolov8n-license-plate-detection` (often fails / unavailable).

**If Hub fails:** detection falls back to **vehicle YOLO** (`yolov8n.pt`)
so ANPR can still track cars; OCR uses the vehicle crop / lower band.
For best plate accuracy, add a real plate `.pt` file here.
