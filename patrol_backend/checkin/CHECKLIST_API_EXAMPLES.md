# Checklist APIs – cURL examples (newly created APIs)

**Base URL:** `https://your-domain.com/checkin/checkins/`  
**Auth:** All requests need `Authorization: Bearer <token>` and `Content-Type: application/json`.

---

## 1. Scan checkpoint (v2)

**URL:** `POST {BASE_URL}scan_v2/`  
**Purpose:** Validate scan; either return “checklist required” (no check-in) or create check-in when no checklist.

### cURL – Request

```bash
curl -X POST "https://your-domain.com/checkin/checkins/scan_v2/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "guard": "guard-user-uuid",
    "shift": "shift-uuid",
    "assign_id": "assignment-uuid",
    "checkpoint": "checkpoint-uuid",
    "timestamp": "2025-02-27T10:30:00.000Z",
    "latitude": 12.345,
    "longitude": 67.890,
    "data": "QR_CODE_STRING_FROM_SCAN"
  }'
```

### Request body (JSON)

| Field        | Type   | Required | Description |
|-------------|--------|----------|-------------|
| `guard`     | UUID   | Yes      | Guard (user) ID |
| `shift`     | UUID   | Yes      | Shift ID |
| `assign_id` | UUID   | Yes      | Assignment ID |
| `checkpoint`| UUID   | Yes      | Checkpoint ID |
| `timestamp` | string | Yes      | ISO 8601 (e.g. UTC) |
| `latitude`  | number | Yes      | Device latitude |
| `longitude` | number | Yes      | Device longitude |
| `data`      | string | Yes      | QR code payload (must match checkpoint) |

---

### Response type 1: Checklist required — **200 OK**

No check-in is created. App should show checklist and then call **submit_checklist_v2**.

```json
{
  "requires_checklist": true,
  "checklist_template_id": "template-uuid",
  "checklist_template_name": "Gate Round Checklist",
  "items": [
    {
      "checklist_item_id": "item-uuid-1",
      "label": "Gate locked",
      "sort_order": 1
    },
    {
      "checklist_item_id": "item-uuid-2",
      "label": "No damage observed",
      "sort_order": 2
    }
  ],
  "scan_context": {
    "guard_id": "guard-user-uuid",
    "shift_id": "shift-uuid",
    "assign_id": "assignment-uuid",
    "checkpoint_id": "checkpoint-uuid",
    "latitude": 12.345,
    "longitude": 67.890,
    "timestamp": "2025-02-27T10:30:00.000Z",
    "data": "QR_CODE_STRING_FROM_SCAN"
  }
}
```

---

### Response type 2: No checklist — **201 Created**

Check-in is created immediately.

```json
{
  "id": "checkin-uuid",
  "guard": "guard-user-uuid",
  "checkpoint": "checkpoint-uuid",
  "shift": "shift-uuid",
  "timestamp": "2025-02-27T10:30:00+05:30",
  "latitude": 12.345,
  "longitude": 67.890,
  "synced": true,
  "has_checklist": false,
  "requires_checklist": false,
  "delayed": false,
  "message": "Checkpoint Successfully Scanned",
  "success": true,
  "distance_from_checkpoint_m": 8.5
}
```

---

### Response type 3: Validation error — **400 Bad Request**

```json
{
  "error": "Invalid guard ID"
}
```

Other examples: `"Checkpoint not found"`, `"Checkpoint time not found in assignment"`, `"Check-in not allowed. Delayed."`

**QR mismatch (400):**

```json
{
  "detail": "Check-in not allowed. QR code mismatch."
}
```

---

### Response type 4: Forbidden — **403 Forbidden**

```json
{
  "error": "No matching assignment found for guard, shift, and checkpoint"
}
```

Or:

```json
{
  "error": "Check-in location is too far from checkpoint (120m)"
}
```

---

### Response type 5: Server error — **500 Internal Server Error**

```json
{
  "error": "An unexpected error occurred during check-in."
}
```

---

## 2. Submit checklist (v2)

**URL:** `POST {BASE_URL}submit_checklist_v2/`  
**Purpose:** After user completes checklist (when scan_v2 returned `requires_checklist: true`), re-validate and create check-in + checklist answers.

### cURL – Request

```bash
curl -X POST "https://your-domain.com/checkin/checkins/submit_checklist_v2/" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer YOUR_ACCESS_TOKEN" \
  -d '{
    "guard": "guard-user-uuid",
    "shift": "shift-uuid",
    "assign_id": "assignment-uuid",
    "checkpoint": "checkpoint-uuid",
    "timestamp": "2025-02-27T10:30:00.000Z",
    "latitude": 12.345,
    "longitude": 67.890,
    "data": "QR_CODE_STRING_FROM_SCAN",
    "checklist_template_id": "template-uuid",
    "checklist_template_name": "Gate Round Checklist",
    "answers": [
      { "checklist_item_id": "item-uuid-1", "label": "Gate locked", "checked": true },
      { "checklist_item_id": "item-uuid-2", "label": "No damage observed", "checked": false }
    ],
    "remarks": "All good."
  }'
```

### Request body (JSON)

| Field                    | Type   | Required | Description |
|--------------------------|--------|----------|-------------|
| `guard`                  | UUID   | Yes      | Same as scan_v2 (or scan_context.guard_id) |
| `shift`                  | UUID   | Yes      | Same as scan_v2 (or scan_context.shift_id) |
| `assign_id`              | UUID   | Yes      | Same as scan_v2 (or scan_context.assign_id) |
| `checkpoint`             | UUID   | Yes      | Same as scan_v2 (or scan_context.checkpoint_id) |
| `timestamp`              | string | Yes      | From scan_context.timestamp |
| `latitude`               | number | Yes      | From scan_context.latitude |
| `longitude`              | number | Yes      | From scan_context.longitude |
| `data`                   | string | Yes      | From scan_context.data (QR payload) |
| `checklist_template_id`  | UUID   | Yes      | From scan_v2 response |
| `checklist_template_name`| string | No       | Label from scan_v2 (optional) |
| `answers`                | array  | Yes      | One object per item: `checklist_item_id`, `label`, `checked` |
| `remarks`                | string | No       | Global remarks (optional) |

**Each item in `answers`:**

| Field               | Type    | Description |
|---------------------|---------|-------------|
| `checklist_item_id` | UUID    | From `items[].checklist_item_id` |
| `label`             | string  | From `items[].label` |
| `checked`           | boolean | true = checked, false = not checked |

**Validation:** Each `checklist_item_id` in `answers` must be one of the items in the checklist template (same as in scan_v2 response `items`). You must send exactly one answer per template item—no missing items, no duplicate item IDs, no extra items. Otherwise the API returns **400** with an error message.

---

### Response type 1: Success — **201 Created**

```json
{
  "id": "checkin-uuid",
  "guard": "guard-user-uuid",
  "checkpoint": "checkpoint-uuid",
  "shift": "shift-uuid",
  "timestamp": "2025-02-27T10:30:00+05:30",
  "latitude": 12.345,
  "longitude": 67.890,
  "synced": true,
  "has_checklist": true,
  "message": "Checklist submitted successfully",
  "success": true
}
```

---

### Response type 2: Validation error — **400 Bad Request**

Missing required field:

```json
{
  "error": "checklist_template_id is required"
}
```

```json
{
  "error": "answers is required"
}
```

Other examples: `"Invalid guard ID"`, `"Checklist template not found"`, `"Checklist template has no items"`, `"Each answer must have checklist_item_id"`, `"checklist_item_id <id> is not part of this template"`, `"Duplicate answer for checklist_item_id <id>"`, `"Missing answers for template items: ['<id>', ...]"`, `"Check-in not allowed. Delayed."`, `"Checkpoint not found"`

**QR mismatch (400):**

```json
{
  "detail": "Check-in not allowed. QR code mismatch."
}
```

---

### Response type 3: Forbidden — **403 Forbidden**

```json
{
  "error": "No matching assignment found for guard, shift, and checkpoint"
}
```

```json
{
  "error": "Check-in location is too far from checkpoint (150m)"
}
```

---

### Response type 4: Server error — **500 Internal Server Error**

```json
{
  "error": "An unexpected error occurred during check-in."
}
```

---

## Summary

| API              | Method | URL (relative to base)   | When to use |
|------------------|--------|---------------------------|-------------|
| Scan v2          | POST   | `checkins/scan_v2/`       | Every checkpoint scan; use response to show checklist or treat as done |
| Submit checklist v2 | POST | `checkins/submit_checklist_v2/` | Only when scan_v2 returned `requires_checklist: true` |

**Flow:** Call **scan_v2** → if `requires_checklist: true`, show checklist then call **submit_checklist_v2** with scan_context + `checklist_template_id` + `answers` + optional `remarks`. If `requires_checklist: false`, check-in is already created.
