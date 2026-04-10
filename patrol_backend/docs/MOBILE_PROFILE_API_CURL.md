# Mobile profile API — face photo (`/auth/mobile/profile/`)

Authenticated user uploads or replaces their own face photo. Same user as the JWT (no user id in the URL).

- **Methods:** `POST`, `PATCH`
- **Content-Type:** `multipart/form-data`
- **File field:** `face_photo` or `image` (one image file, required)

Replace `BASE_URL` (e.g. `http://localhost:8000`) and `ACCESS_TOKEN` with real values.

---

## Success

### cURL

```bash
curl -s -X POST "${BASE_URL}/auth/mobile/profile/" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -F "face_photo=@/path/to/photo.jpg"
```

Alias field name:

```bash
curl -s -X POST "${BASE_URL}/auth/mobile/profile/" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -F "image=@/path/to/photo.jpg"
```

### Example response — HTTP `200`

Body shape: `status`, `message`, `data` (flat — user fields, permissions, flags; **no** nested `user` object). No `status_code` field in JSON.

```json
{
  "status": "success",
  "message": "Profile updated successfully",
  "data": {
    "id": "d58252b0-dd97-49ed-883c-adf897ee54c5",
    "last_login": "2026-04-08T10:00:00+00:00",
    "is_superuser": false,
    "aadhar_no": null,
    "email": "guard2@test.com",
    "name": "Guard Two",
    "phone_no": null,
    "role": "guard",
    "location": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "employee_code": "G002",
    "timezone": "Asia/Kolkata",
    "is_active": true,
    "is_staff": false,
    "created_by": null,
    "modified_by": null,
    "is_deleted": false,
    "deleted_on": null,
    "deleted_by": null,
    "face_photo": "http://localhost:8000/media/user_faces/photo_xyz.jpg",
    "user_id": "d58252b0-dd97-49ed-883c-adf897ee54c5",
    "is_allow_webapp": false,
    "permissions": [],
    "location_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    "is_qr_scan_enabled": false
  }
}
```

Exact fields depend on your user and location; `face_photo` is an absolute URL when a file is stored.

---

## Error responses

HTTP status is on the response line. JSON uses `status: "error"`, `message`, and `data` (details or `{}`).

---

### Not authenticated — HTTP `401`

Usually returned by Django REST / JWT (format may vary):

```bash
curl -s -X POST "${BASE_URL}/auth/mobile/profile/" \
  -F "face_photo=@/path/to/photo.jpg"
```

Example:

```json
{
  "detail": "Authentication credentials were not provided."
}
```

With invalid token:

```json
{
  "detail": "Given token not valid for any token type"
}
```

---

### User not found (soft-deleted account) — HTTP `404`

```json
{
  "status": "error",
  "message": "User not found",
  "data": {}
}
```

---

### Validation — HTTP `400`

Always `message`: `"Validation failed"`. Field errors are in `data`.

#### Missing file

```bash
curl -s -X POST "${BASE_URL}/auth/mobile/profile/" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}"
```

```json
{
  "status": "error",
  "message": "Validation failed",
  "data": {
    "face_photo": ["This field is required."]
  }
}
```

#### Non-image content type

```json
{
  "status": "error",
  "message": "Validation failed",
  "data": {
    "face_photo": [
      "Only image uploads are allowed (got non-image content type)."
    ]
  }
}
```

#### Invalid or corrupted image

```json
{
  "status": "error",
  "message": "Validation failed",
  "data": {
    "face_photo": ["File is not a valid image or is corrupted."]
  }
}
```

#### Unsupported image format

Allowed: JPEG, PNG, WebP, HEIC/HEIF (HEIC needs `pillow-heif` on the server).

```json
{
  "status": "error",
  "message": "Validation failed",
  "data": {
    "face_photo": [
      "Unsupported image type. Use JPEG, PNG, WebP, or HEIC/HEIF."
    ]
  }
}
```

---

### Server error — HTTP `500`

```json
{
  "status": "error",
  "message": "<exception text>",
  "data": {}
}
```

---

## Quick reference

| HTTP | Situation |
|------|-----------|
| 200 | Photo saved; `data` contains flat profile + flags |
| 401 | Missing or invalid `Authorization: Bearer` |
| 404 | Authenticated user is soft-deleted |
| 400 | Missing/invalid image (see `data.face_photo`) |
| 500 | Unexpected server error |
