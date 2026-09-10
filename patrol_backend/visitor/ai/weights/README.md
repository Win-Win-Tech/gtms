# License plate YOLO weights

Place a dedicated plate detector `.pt` here, e.g.:

- `license_plate_detector.pt`
- `yolov8n-license-plate.pt`

Or set env:

```bash
export VISITOR_AI_PLATE_WEIGHTS=/path/to/your_plate_model.pt
```

If neither is set, code may try Ultralytics Hub
`keremberke/yolov8n-license-plate-detection` (often fails / unavailable).

**If Hub fails:** detection automatically falls back to **vehicle YOLO**
(`yolov8n.pt` already in the repo) so ANPR Reader can still track cars.
OCR then runs on the vehicle crop / full frame. For best plate accuracy,
add a real plate `.pt` file here.
