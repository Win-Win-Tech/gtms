# License plate YOLO weights

Place a dedicated plate detector `.pt` here, e.g.:

- `license_plate_detector.pt`
- `yolov8n-license-plate.pt`

Or set env:

```bash
export VISITOR_AI_PLATE_WEIGHTS=/path/to/your_plate_model.pt
```

If neither is set, extract-v2 downloads/uses Ultralytics Hub:
`keremberke/yolov8n-license-plate-detection` (first request may be slow).
