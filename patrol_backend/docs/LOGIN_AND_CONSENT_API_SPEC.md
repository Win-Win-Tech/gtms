# Login & Mobile Consent API Specification

This document provides API details for:
1. **Login API (`POST /auth/login/`)** with the new `is_profile_already_exist` and `is_consent_submitted` fields.
2. **Mobile Consent API (`POST /auth/consent/` & `GET /auth/consent/`)** for location and camera consent submission.

---

## 1. Login API

- **Endpoint**: `POST /auth/login/`
- **Authentication**: None (`AllowAny`)
- **Headers**:
  ```http
  Content-Type: application/json
  ```

### Request Body
```json
{
  "email": "guard@example.com",
  "password": "your_password"
}
```

### cURL Example
```bash
curl -X POST "http://127.0.0.1:8000/auth/login/" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "guard@example.com",
    "password": "your_password"
  }'
```


### Success Response (`200 OK`)
The response structure is identical to the current response, with **two extra boolean keys** added at the root level of `data`:
- **`is_profile_already_exist`** (`boolean`):
  - `true`: The user already has an enrolled profile face photo in the system.
  - `false`: The user has not enrolled/uploaded a profile face photo yet.
- **`is_consent_submitted`** (`boolean`):
  - `true`: The user has already submitted the mobile location & camera consent form.
  - `false`: The user has not yet submitted the consent form (client should prompt consent form).

```json
{
  "status": "success",
  "message": "Login successful",
  "data": {
    "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoiYWNjZXNzIiwiZXhwIjoxNzkzMzk1MTEzLCJpYXQiOjE3OTMzMDg3MTMsImp0aSI6ImJiMTJmNzM5MWEyZDQwNzM4YzYwZjAyOGE3OWI2ODdhIiwidXNlcl9pZCI6IjIyZmY3NzIyLTEyNGQtNDVmOC1iM2VlLWJjMWVmNjEyMDUxNiJ9.signature",
    "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJ0b2tlbl90eXBlIjoicmVmcmVzaCIsImV4cCI6MTc5MzkxMzUxMywiaWF0IjoxNzkzMzA4NzEzLCJqdGkiOiJmNGUzNzM4MzJmMmY0NTQ3Yjg0MDljY2NiZTcwNmQ0OSIsInVzZXJfaWQiOiIyMmZmNzcyMi0xMjRkLTQ1ZjgtYjNlZS1iYzFlZjYxMjA1MTYifQ.signature",
    "id": "22ff7722-124d-45f8-b3ee-bc1ef6120516",
    "last_login": "2026-10-28T18:30:00.000Z",
    "is_superuser": false,
    "aadhar_no": "123456789012",
    "email": "guard@example.com",
    "name": "John Doe",
    "phone_no": "9876543210",
    "role": "guard",
    "location": "259ea9c6-9e14-44b2-ad8a-52d4385060de",
    "employee_code": "EMP-001",
    "timezone": "Asia/Kolkata",
    "is_active": true,
    "is_staff": false,
    "created_by": null,
    "modified_by": null,
    "is_deleted": false,
    "deleted_on": null,
    "deleted_by": null,
    "face_photo": "https://api.example.com/media/user_faces/john_doe.jpg",
    "is_profile_already_exist": true,
    "is_consent_submitted": false,
    "role_permissions": [
      "Dashboard",
      "Live Tracking"
    ],
    "is_allow_webapp": false,
    "is_allow_edit": false,
    "is_allow_create": false,
    "is_qr_scan_enabled": true,
    "is_host_approval_enabled": true,
    "visitor_default_purpose": "",
    "visitor_default_remarks": "",
    "visitor_expected_out_hours": ""
  },
  "code": 200
}
```

### Failure Responses

#### 1. Invalid Credentials (`401 Unauthorized`)
```json
{
  "status": "error",
  "message": "Invalid credentials",
  "data": null,
  "code": 401
}
```

#### 2. Server Error (`500 Internal Server Error`)
```json
{
  "status": "error",
  "message": "<Error description>",
  "data": null,
  "code": 500
}
```

*(Note: The token refresh endpoint `POST /auth/refresh-token/` also returns this exact payload structure with updated tokens).*

---

## 2. Mobile Consent Submission API

Used by the mobile application to record that the user has accepted location and camera/image usage consent.

- **Endpoints**:
  - Primary: `POST /auth/consent/`
  - Alias: `POST /auth/mobile/consent/`
- **Authentication**: JWT Bearer Token (`IsAuthenticated`)
- **Headers**:
  ```http
  Content-Type: application/json
  Authorization: Bearer <access_token>
  ```

### Request Body
```json
{
  "is_consent_submitted": true
}
```
*(Accepts boolean `true`/`false`, or string `"true"`/`"false"`. If empty body `{}` is sent, it defaults to `true`).*

### cURL Examples

#### A. Submit Consent (Accept)
```bash
curl -X POST "http://127.0.0.1:8000/auth/consent/" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "is_consent_submitted": true
  }'
```

#### B. Decline Consent
```bash
curl -X POST "http://127.0.0.1:8000/auth/consent/" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "is_consent_submitted": false
  }'
```


### Responses

#### 1. Success Response (`200 OK`)
When consent is accepted and recorded:
```json
{
  "status": "success",
  "message": "Consent submitted successfully",
  "data": {
    "user_id": "22ff7722-124d-45f8-b3ee-bc1ef6120516",
    "is_consent_submitted": true,
    "consent_submitted_on": "2026-10-28T18:35:10.123456+00:00"
  },
  "code": 200
}
```

#### 2. Consent Declined (`400 Bad Request`)
If the client submits `{"is_consent_submitted": false}`:
```json
{
  "status": "error",
  "message": "Consent was declined",
  "data": null,
  "code": 400
}
```

#### 3. Unauthorized / Missing Token (`401 Unauthorized`)
If JWT access token is omitted or expired:
```json
{
  "detail": "Given token not valid for any token type",
  "code": "token_not_valid",
  "messages": [
    {
      "token_class": "AccessToken",
      "token_type": "access",
      "message": "Token is invalid or expired"
    }
  ]
}
```

#### 4. User Inactive / Deleted (`404 Not Found`)
If the authenticated user is marked deleted or does not exist:
```json
{
  "status": "error",
  "message": "User not found",
  "data": null,
  "code": 404
}
```

---

## 3. Check Consent Status (GET API)

Used to check whether the logged-in user has already submitted consent without submitting again.

- **Endpoint**: `GET /auth/consent/` (or `GET /auth/mobile/consent/`)
- **Headers**:
  ```http
  Authorization: Bearer <access_token>
  ```

### cURL Example
```bash
curl -X GET "http://127.0.0.1:8000/auth/consent/" \
  -H "Authorization: Bearer <access_token>"
```


### Success Response (`200 OK`)
```json
{
  "status": "success",
  "message": "Consent status",
  "data": {
    "user_id": "22ff7722-124d-45f8-b3ee-bc1ef6120516",
    "is_consent_submitted": true,
    "consent_submitted_on": "2026-10-28T18:35:10.123456+00:00"
  },
  "code": 200
}
```
*(If consent has not been submitted, `is_consent_submitted` will be `false` and `consent_submitted_on` will be `null`).*
