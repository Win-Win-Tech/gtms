# Incident Report API (mobile) — cURL examples

Base path (from `patrol_backend/urls.py`): `.../incident/`  
Incident report endpoint (from `incident/urls.py`): `POST .../incident/report/`

This API uses **multipart/form-data** (`MultiPartParser`).

## What changed (description rules)

You can provide incident description in either form:

- **Text**: `incident_description`
- **Audio**: `description_audio` (file upload)

**Validation rule:** at least one of the above must be provided. (You may also send both.)

Other media still supported:

- `photo` (image)
- `video` (file/video)

---

## Required headers

- `Authorization: Bearer <token>`

`curl` will set the multipart boundary automatically, so you typically don’t need to set `Content-Type` manually.

---

## 1) Report incident — text description only

```bash
curl -X POST "https://your-domain.com/incident/report/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -F "severity=High" \
  -F "incident_description=Fire alarm triggered near Gate 2" \
  -F "checkpoint=<checkpoint_uuid_optional>" \
  -F "photo=@/path/to/photo.jpg"
```

Notes:
- `checkpoint` is optional (if your UI sends it).
- `photo` is optional.

---

## 2) Report incident — audio description only (no text)

```bash
curl -X POST "https://your-domain.com/incident/report/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -F "severity=Medium" \
  -F "incident_description=" \
  -F "description_audio=@/path/to/voice_note.m4a"
```

---

## 3) Report incident — both text + audio + optional video

```bash
curl -X POST "https://your-domain.com/incident/report/" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -F "severity=Low" \
  -F "incident_description=Suspicious noise reported; recording attached." \
  -F "description_audio=@/path/to/voice_note.mp3" \
  -F "video=@/path/to/video.mp4"
```

---

## Success response (201 Created)

Response is the serialized `incidentreport` row (fields depend on model), for example:

```json
{
  "ticket_number": "A1B2C3D4E5F6",
  "severity": "High",
  "incident_description": "Fire alarm triggered near Gate 2",
  "description_audio": "incidents/A1B2C3D4E5F6/voice_note.m4a",
  "photo": "incidents/A1B2C3D4E5F6/photo.jpg",
  "video": "incidents/A1B2C3D4E5F6/video.mp4",
  "status": "Open",
  "created_by": "user-uuid",
  "location": "location-uuid",
  "checkpoint": "checkpoint-uuid",
  "created_on": "2026-03-17T12:34:56+05:30"
}
```

Important:
- `description_audio` in the response is the **Django FileField value** (media path).  
  The API currently uploads to Cloudinary for notifications, but it does **not** store the Cloudinary URL in DB.

---

## Error responses

### 400 Bad Request — missing description

If both text and audio are missing/empty:

```json
{
  "incident_description": "Provide incident_description text or description_audio file."
}
```

### 401 Unauthorized

Missing/invalid token.

