# Visitor Management Implementation Plan & Rules

## Summary of Workflow Rules (Updated)

### 1. Unified Form with Toggle
- Single registration form with **"Invite (Pre-Reg)"** toggle.
- **Manual Walk-in**: Photos & ID copy required at creation.
- **Pre-Reg (Invite)**: Visit date mandatory ($\ge$ today). Photos & ID copy optional at creation. No host notification on creation. Status: `scheduled`.

### 2. QR Scan on Visit Date (Invited Entry)
- In **BOTH cases (whether fields/assets are filled or missing)**, scanning an invited visitor's QR code on visit date ALWAYS redirects/navigates the guard to the verification form.
- QR scan response:
  ```json
  {
    "action": "verify_entry",
    "type": "verify_entry",
    "message": "Redirect to verification form. Guard must verify details and capture required photos.",
    "entry": { ... }
  }
  ```
- **No notification is sent to the host upon QR scan alone**.

### 3. Guard Verification & Field Editing
- On the verification form, **guards ARE ALLOWED to edit/verify all fields** (visitor name, IC/Passport, phone, host, visitor type, purpose, vehicle number, remarks, visit date, ETA, ETO).
- Guard uploads mandatory assets (`visitor_photo`, `id_proof`).
- Guard clicks **"Verify & Submit for Host Approval"**.

### 4. Complete Invite Submit (`POST /visitors/entries/<entry_id>/complete-invite/`)
- Backend updates any edited metadata fields and attaches uploaded assets.
- Enforces mandatory assets (`visitor_photo`, `id_proof`).
- Sets status to `pending_approval`, sets `scanned_by = guard`.
- **Sends push & inbox notification to the host** (`notify_visitor_pending(entry)`).

### 5. Host Approval Asset Verification
- Host approval (`POST /visitors/entries/<entry_id>/approve/`) checks that mandatory assets (`visitor_photo` and `id_proof`) exist on entry.
- If missing, blocks approval with HTTP 400 error.

### 6. Reschedule
- Host reschedule does not require assets. Updates visit date, ETA, ETO, and pass images.
