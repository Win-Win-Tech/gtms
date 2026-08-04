# GTMS Visitor Management: Invitation & Pre-Registration Module API Documentation

## Executive Summary & Workflow Overview

This document provides a comprehensive specification of the **Visitor Invitation & Pre-Registration Module** in the GTMS platform. It covers the full lifecycle of a visitor entry from pre-registration to final checkout, including endpoint specifications, request/response formats, ready-to-use cURL commands, QR scan response actions, notification types, and front-end/back-end state machine transitions.

---

## 1. End-to-End Lifecycle & Workflow

```
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ 1. Host Creates │  ───> │ 2. Host Edits   │  ───> │ 3. Visitor      │
│    Invitation   │       │    Invitation   │       │    Arrives &    │
│ (photos opt.)   │       │ (photos opt.)   │       │    Scans QR     │
└─────────────────┘       └─────────────────┘       └────────┬────────┘
                                                             │
                                                             ▼
┌─────────────────┐       ┌─────────────────┐       ┌─────────────────┐
│ 6. Visitor      │       │ 5. Host Approves│       │ 4. Guard Verifies│
│    Exits        │ <───  │    Entry        │ <───  │    & Uploads    │
│ (checked_out)   │       │ (checked_in)    │       │    Photos       │
└─────────────────┘       └────────┬────────┘       └─────────────────┘
                                   │
                           ┌───────┴───────┐
                           │               │
                           ▼               ▼
                    ┌────────────┐  ┌────────────┐
                    │ Revert     │  │ Reject     │
                    │ (reverted) │  │(cancelled) │
                    └────────────┘  └────────────┘
```

### Flow Breakdown:

1. **Invitation Creation (Host / Creator)**:
   - Host/Staff fills visitor name, IC/Passport number, phone, visitor type, location, visit date, ETA, ETO, and purpose of visit.
   - **Photos are optional** at this stage.
   - Entry created with `status = "scheduled"`, `entry_source = "invitation"`.
   - Pass PDF & QR code generated.

2. **Invitation Editing (Host / Creator)**:
   - Creator/Host can edit invitation metadata prior to visitor arrival.
   - **Photos remain optional**.
   - Entry remains in `status = "scheduled"`.

3. **Visitor Arrival & Verification (Guard / Staff)**:
   - Guard scans the visitor's QR code (`POST /visitors/qr-scan/`) or clicks **Verify** on the dashboard.
   - Backend performs date validations:
     - If `visit_date` is in the future $\rightarrow$ Returns `action: "too_early"` with message `"Visit is scheduled for YYYY-MM-DD. Too early to check in."`
     - If `visit_date` is today $\rightarrow$ Returns `action: "verify_entry"` with entry details.
   - Guard is redirected to the verification form with pre-filled visitor details.

4. **Guard Photos & Submission (`pending_approval`)**:
   - Guard verifies visitor identity on-site, uploads mandatory **Visitor Photo** and **IC / ID Copy**, and clicks **"Verify & Submit for Host Approval"**.
   - Status updates from `scheduled` to `pending_approval`.
   - Host receives immediate Push & Inbox Notifications (`notification_type: "visitor_pending_approval"`).

5. **Host Review & Approval / Revert / Rejection**:
   - **Approve**: Host clicks Approve. Status changes to `checked_in`, visitor checked in. (`notification_type: "visitor_approved"`).
   - **Revert**: Host clicks Revert with reason. Status changes to `reverted`, guard notified to correct details. (`notification_type: "visitor_reverted"`).
   - **Reject**: Host clicks Reject. Status changes to `cancelled`. (`notification_type: "visitor_rejected"`).

6. **Visitor Exit**:
   - Guard scans QR code or marks exit on dashboard.
   - Guard uploads exit photo (optional/required by location setting).
   - Status changes to `checked_out`. (`notification_type: "visitor_checked_out"`).

---

## 2. API Endpoint Specifications & cURL Commands

### 2.1 Create Visitor Invitation
- **URL**: `POST /api/visitors/invitations/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Access**: Host, Admin, Superuser, Guard

#### Request Parameters:
| Parameter | Type | Required | Description |
| :--- | :--- | :--- | :--- |
| `visitor_name` | String | Yes | Name of the visitor |
| `ic_passport_number` | String | Yes | IC / Passport number |
| `phone_number` | String | No | Visitor phone number |
| `host_id` | Integer | Yes | User ID of the host |
| `location_id` | Integer | Yes | Location / Organization ID |
| `visitor_type` | String | Yes | `guest`, `contractor`, `delivery`, `vip`, etc. |
| `visit_date` | String (YYYY-MM-DD) | Yes | Scheduled date of visit |
| `expected_arrival_time` | String (ISO) | No | Expected arrival time (ETA) |
| `expected_out_time` | String (ISO) | No | Expected departure time (ETO) |
| `purpose_of_visit` | String | No | Reason for visit |
| `vehicle_number` | String | No | Vehicle plate number |
| `remarks` | String | No | Additional notes |
| `visitor_photo` | File | **No** | Optional during invitation creation |
| `id_proof` | File | **No** | Optional during invitation creation |

#### cURL Request (Create Invitation):
```bash
curl -X POST "http://localhost:8000/api/visitors/invitations/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "visitor_name": "John Doe",
    "ic_passport_number": "S9876543A",
    "phone_number": "+60123456789",
    "host_id": 12,
    "location_id": 2,
    "visitor_type": "guest",
    "visit_date": "2026-08-05",
    "expected_arrival_time": "2026-08-05T10:00:00Z",
    "expected_out_time": "2026-08-05T17:00:00Z",
    "purpose_of_visit": "Meeting",
    "vehicle_number": "ABC1234",
    "remarks": "Please prepare visitor pass"
  }'
```

#### Sample Success Response (`201 Created`):
```json
{
  "id": 505,
  "qr_token": "VT-505-ABC123XYZ",
  "status": "scheduled",
  "entry_source": "invitation",
  "visitor_name": "John Doe",
  "ic_passport_number": "S9876543A",
  "phone_number": "+60123456789",
  "host": 12,
  "host_name": "Ravi Kumar",
  "location": 2,
  "visit_date": "2026-08-05",
  "expected_arrival_time": "2026-08-05T10:00:00Z",
  "expected_out_time": "2026-08-05T17:00:00Z",
  "pass_pdf_url": "http://domain.com/media/visitor_passes/pass_505.pdf",
  "qr_code_url": "http://domain.com/media/visitor_qrs/qr_505.png"
}
```

---

### 2.2 Complete / Edit Visitor Invitation
- **URL**: `POST /api/visitors/entries/<id>/complete-invite/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: multipart/form-data`
- **Access**: Host, Admin (Edit Mode) | Guard (Verify Arrival Mode)

#### cURL Request A: Host Edit Mode (Photos Optional, Status Stays `scheduled`):
```bash
curl -X POST "http://localhost:8000/api/visitors/entries/505/complete-invite/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "visitor_name=Johnathan Doe" \
  -F "ic_passport_number=S9876543A" \
  -F "phone_number=+60123456789" \
  -F "host_id=12" \
  -F "visit_date=2026-08-05" \
  -F "purpose_of_visit=Updated Project Meeting" \
  -F "vehicle_number=XYZ9876"
```

#### cURL Request B: Guard Verify Arrival Mode (Upload Mandatory Photos, Status Updates to `pending_approval`):
```bash
curl -X POST "http://localhost:8000/api/visitors/entries/505/complete-invite/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "visitor_name=Johnathan Doe" \
  -F "ic_passport_number=S9876543A" \
  -F "host_id=12" \
  -F "visit_date=2026-08-05" \
  -F "visitor_photo=@/path/to/visitor_photo.jpg" \
  -F "id_proof=@/path/to/ic_copy.jpg"
```

#### Sample Response (`200 OK`):
```json
{
  "id": 505,
  "status": "pending_approval",
  "scanned_by": 8,
  "scanned_by_name": "Guard Duty",
  "assets": [
    { "asset_type": "visitor_photo", "url": "http://domain.com/media/photos/v505.jpg" },
    { "asset_type": "id_proof", "url": "http://domain.com/media/id/id505.jpg" }
  ]
}
```

---

### 2.3 QR Code Scan Validation Endpoint
- **URL**: `POST /api/visitors/qr-scan/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Access**: Guard, Admin, Staff

#### cURL Request:
```bash
curl -X POST "http://localhost:8000/api/visitors/qr-scan/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "qr_token": "VT-505-ABC123XYZ"
  }'
```

#### QR Scan Response Type Matrix:

| Action (`action`) | HTTP Status | Description | Trigger Condition |
| :--- | :--- | :--- | :--- |
| `too_early` | `400 Bad Request` | Visit date is in the future | `visit_date > today` |
| `verify_entry` | `200 OK` | Guard must verify details & upload photos | `visit_date == today` & `status == scheduled` |
| `missing_document` | `200 OK` | Photos missing, proceed to upload form | `status == pending_approval` & photos missing |
| `checkout` | `200 OK` | Visitor is currently checked in, mark exit | `status == checked_in` |
| `expired` | `400 Bad Request` | QR code token expired or already checked out | `status == checked_out` or `qr_expired == true` |

#### Sample Responses:

##### Response: `too_early` (`400 Bad Request`)
```json
{
  "action": "too_early",
  "message": "Visit is scheduled for 2026-08-05. Too early to check in.",
  "entry": {
    "id": 505,
    "visitor_name": "John Doe",
    "visit_date": "2026-08-05",
    "status": "scheduled"
  }
}
```

##### Response: `verify_entry` (`200 OK`)
```json
{
  "action": "verify_entry",
  "message": "Visit date verified. Proceed to verify details and upload required photos.",
  "entry": {
    "id": 505,
    "visitor_name": "John Doe",
    "ic_passport_number": "S9876543A",
    "host_id": 12,
    "host_name": "Ravi Kumar",
    "visit_date": "2026-08-04",
    "status": "scheduled"
  }
}
```

---

### 2.4 Host Approve & Check-In Entry
- **URL**: `POST /api/visitors/entries/<id>/approve/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Access**: Assigned Host, Superuser

#### cURL Request:
```bash
curl -X POST "http://localhost:8000/api/visitors/entries/505/approve/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "expected_out_time": "2026-08-05T18:00:00Z"
  }'
```

#### Asset Validation Rule:
The backend validates that both `visitor_photo` and `id_proof` assets exist on the entry. If missing, HTTP `400 Bad Request` is returned:
```json
{
  "error": "Visitor photo and IC/ID proof copy must be provided by visitor/guard before host approval"
}
```

#### Success Response (`200 OK`):
```json
{
  "id": 505,
  "status": "checked_in",
  "check_in_time": "2026-08-04T10:15:22Z",
  "message": "Approved and checked in successfully"
}
```

---

### 2.5 Host Revert Entry
- **URL**: `POST /api/visitors/entries/<id>/revert/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Access**: Assigned Host, Superuser

#### cURL Request:
```bash
curl -X POST "http://localhost:8000/api/visitors/entries/505/revert/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "revert_reason": "IC copy is blurry. Please re-upload a clear photo."
  }'
```

#### Success Response (`200 OK`):
```json
{
  "id": 505,
  "status": "reverted",
  "revert_reason": "IC copy is blurry. Please re-upload a clear photo.",
  "message": "Entry reverted for guard correction"
}
```

---

### 2.6 Host Reject / Cancel Entry
- **URL**: `POST /api/visitors/entries/<id>/cancel/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: application/json`
- **Access**: Assigned Host, Superuser

#### cURL Request:
```bash
curl -X POST "http://localhost:8000/api/visitors/entries/505/cancel/" \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json" \
  -d '{
    "cancellation_reason": "Host not available today"
  }'
```

#### Success Response (`200 OK`):
```json
{
  "id": 505,
  "status": "cancelled",
  "cancellation_reason": "Host not available today",
  "message": "Visitor entry cancelled"
}
```

---

### 2.7 Guard Mark Exit (Checkout)
- **URL**: `POST /api/visitors/entries/<id>/checkout/`
- **Headers**: `Authorization: Bearer <token>`, `Content-Type: multipart/form-data`
- **Access**: Guard, Admin, Superuser

#### cURL Request:
```bash
curl -X POST "http://localhost:8000/api/visitors/entries/505/checkout/" \
  -H "Authorization: Bearer <TOKEN>" \
  -F "exit_photo=@/path/to/exit_photo.jpg"
```

#### Success Response (`200 OK`):
```json
{
  "id": 505,
  "status": "checked_out",
  "check_out_time": "2026-08-04T16:30:00Z",
  "message": "Visitor checked out successfully"
}
```

---

## 3. Notification Specifications

The system delivers real-time notifications via **Push Notifications (FCM)** and **In-App Inbox WebSocket Messages** across key status transition events.

### Notification Event Summary:

| Event / Action | Status Transition | Target Audience | Notification Type (`notification_type`) | Message Summary |
| :--- | :--- | :--- | :--- | :--- |
| Guard submits verified details | `scheduled` $\rightarrow$ `pending_approval` | Assigned Host | `visitor_pending_approval` | "Visitor John Doe requires your check-in approval." |
| Host approves visitor | `pending_approval` $\rightarrow$ `checked_in` | Guard / Host | `visitor_approved` | "Visitor John Doe has been approved and checked in." |
| Host reverts entry | `pending_approval` $\rightarrow$ `reverted` | Guard / Creator | `visitor_reverted` | "Entry reverted by host: [Reason]." |
| Host rejects visitor | `pending_approval` $\rightarrow$ `cancelled` | Guard / Creator | `visitor_rejected` | "Visitor John Doe check-in rejected." |
| Visitor exits premises | `checked_in` $\rightarrow$ `checked_out` | Host | `visitor_checked_out` | "Visitor John Doe has checked out." |

---

### 3.1 Push Notification Payload Format (FCM)

```json
{
  "to": "<FCM_DEVICE_TOKEN>",
  "notification": {
    "title": "Visitor Approval Required",
    "body": "John Doe has arrived and requires your approval to check in.",
    "sound": "default"
  },
  "data": {
    "notification_type": "visitor_pending_approval",
    "entry_id": 505,
    "visitor_name": "John Doe",
    "ic_passport_number": "S9876543A",
    "host_id": 12,
    "status": "pending_approval",
    "click_action": "FLUTTER_NOTIFICATION_CLICK"
  }
}
```

---

## 4. Status Machine & Matrix

| Initial Status | Permitted Action | Next Status | Actor Allowed | Photos Required |
| :--- | :--- | :--- | :--- | :--- |
| *None* | `Create Invite` | `scheduled` | Host, Guard, Admin | **No** |
| `scheduled` | `Edit Invite` | `scheduled` | Host, Creator, Superadmin | **No** |
| `scheduled` | `Verify & Submit` | `pending_approval` | Guard, Staff | **Yes** (`photo` & `id`) |
| `pending_approval` | `Approve` | `checked_in` | Host, Superadmin | **Yes** (enforced by backend) |
| `pending_approval` | `Revert` | `reverted` | Host, Superadmin | **No** |
| `pending_approval` | `Reject` | `cancelled` | Host, Superadmin | **No** |
| `reverted` | `Resubmit` | `pending_approval` | Guard, Creator | **Yes** |
| `checked_in` | `Checkout` | `checked_out` | Guard, Admin | Optional / Configurable |

---

## 5. File Location Reference

- **Documentation File**: [VISITOR_INVITE_WORKFLOW_API_DOCUMENTATION.md](file:///var/www/html/babu/c/czip/GTMS/backendnew/gtms/patrol_backend/docs/VISITOR_INVITE_WORKFLOW_API_DOCUMENTATION.md)
- **Backend Controllers & Views**: [patrol_backend/visitor/views.py](file:///var/www/html/babu/c/czip/GTMS/backendnew/gtms/patrol_backend/visitor/views.py)
- **Frontend Registration Form**: [VisitorManualEntry.jsx](file:///var/www/html/babu/c/czip/GTMS/GTMS_NEw/src/pages/visitor/VisitorManualEntry.jsx)
- **Frontend Table & Actions**: [VisitorHistory.jsx](file:///var/www/html/babu/c/czip/GTMS/GTMS_NEw/src/pages/visitor/VisitorHistory.jsx)
- **Frontend Detail Modal**: [VisitorEntryDetailModal.jsx](file:///var/www/html/babu/c/czip/GTMS/GTMS_NEw/src/components/visitor/VisitorEntryDetailModal.jsx)
