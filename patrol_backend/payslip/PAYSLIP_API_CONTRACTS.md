# Payslip API Contracts (Frontend Integration Guide)

Base prefix: `/payslip`

Auth: `Authorization: Bearer <access_token>`

Access: allowed for `admin`, `so`, `fo`, `superuser`; denied for `guard`.

Content type:

- JSON for all normal endpoints
- multipart only when uploading `company_logo`

---

## 1) Payroll Profiles

Endpoint base: `/payslip/payroll-profiles/`

## 1.1 Create profile

- Method: `POST`
- URL: `/payslip/payroll-profiles/`

Request body:

```json
{
  "user": "UUID",
  "location": "UUID",
  "employee_code": "EMP1001",
  "salary_type": "monthly",
  "gross_salary": "30000.00",
  "bank_name": "SBI",
  "account_holder_name": "Ravi Kumar",
  "account_number": "1234567890",
  "ifsc_code": "SBIN0001234",
  "branch_name": "Chennai",
  "pf_enabled": true,
  "esi_enabled": true,
  "pf_number": "PF123",
  "esi_number": "ESI456",
  "uan": "UAN789",
  "pan": "ABCDE1234F",
  "is_active": true,
  "effective_from": "2026-01-01",
  "effective_to": null
}
```

Success response:

- `201 Created`
- returns full profile object with `id`, timestamps, and read-only metadata.

Common errors:

- `400` duplicate `(location, employee_code)`
- `403` role not allowed

## 1.2 List profiles

- Method: `GET`
- URL: `/payslip/payroll-profiles/`

Success:

- `200 OK`
- array of profiles

## 1.3 Retrieve/Update/Delete profile

- `GET /payslip/payroll-profiles/{id}/`
- `PUT /payslip/payroll-profiles/{id}/`
- `PATCH /payslip/payroll-profiles/{id}/`
- `DELETE /payslip/payroll-profiles/{id}/`

Notes:

- audit fields are server-controlled.

---

## 2) Payslip Templates

Endpoint base: `/payslip/templates/`

## 2.1 Create template (JSON only, no logo)

- Method: `POST`
- URL: `/payslip/templates/`
- Content-Type: `application/json`

Request:

```json
{
  "location": "UUID",
  "company_name": "GTMS Pvt Ltd",
  "company_address": "Address line",
  "company_email": "hr@company.com",
  "company_phone": "9876543210",
  "company_gstin": "GSTIN123",
  "header_text": "Payslip",
  "header_color": "#1e40af",
  "header_alignment": "center",
  "footer_text": "System generated",
  "footer_color": "#64748b",
  "layout_config": {},
  "page_size": "A4",
  "orientation": "portrait",
  "font_size": 10
}
```

## 2.2 Create/Update template with logo

- Method: `POST` or `PATCH`
- URL: `/payslip/templates/` or `/payslip/templates/{id}/`
- Content-Type: `multipart/form-data`

Fields:

- all normal template fields
- `company_logo` (file)

Success:

- returns template object; `company_logo` is media URL/path.

## 2.3 List/Retrieve/Delete

- `GET /payslip/templates/`
- `GET /payslip/templates/{id}/`
- `DELETE /payslip/templates/{id}/`

Delete behavior:

- soft delete (`is_deleted=true`) internally.

---

## 3) Field Configs

Endpoint base: `/payslip/field-configs/`

## 3.1 Create config

- Method: `POST`
- URL: `/payslip/field-configs/`

Request:

```json
{
  "location": "UUID",
  "config_name": "Default Salary Config",
  "description": "Main config",
  "is_active": true
}
```

## 3.2 Create default config + default fields

- Method: `POST`
- URL: `/payslip/field-configs/create-default/`

Request:

```json
{
  "location_id": "UUID"
}
```

Success:

- `201` with created config object.

Error:

- `400` if config already exists for location.

## 3.3 List/Retrieve/Update/Delete

- `GET /payslip/field-configs/`
- `GET /payslip/field-configs/{id}/`
- `PUT/PATCH /payslip/field-configs/{id}/`
- `DELETE /payslip/field-configs/{id}/` (soft delete)

---

## 4) Fields (Salary Components)

Endpoint base: `/payslip/fields/`

## 4.1 Create field

- Method: `POST`
- URL: `/payslip/fields/`

Request:

```json
{
  "field_config": "UUID",
  "field_name": "Basic Salary",
  "field_code": "BASIC",
  "field_type": "EARNING",
  "value_type": "PERCENTAGE",
  "value": "60",
  "display_order": 1,
  "is_visible": true
}
```

`field_type`:

- `EARNING`
- `DEDUCTION`
- `INFO`

`value_type`:

- `FIXED`
- `PERCENTAGE`
- `FORMULA`

Formula notes:

- Allowed references: system vars + field codes in same config
- Circular dependencies rejected
- Unknown references rejected
- Unsafe expression patterns rejected

Example formula field:

```json
{
  "field_config": "UUID",
  "field_name": "PF",
  "field_code": "PF",
  "field_type": "DEDUCTION",
  "value_type": "FORMULA",
  "value": "BASIC * Decimal('0.12')",
  "display_order": 5,
  "is_visible": true
}
```

## 4.2 List/Retrieve/Update/Delete

- `GET /payslip/fields/`
- `GET /payslip/fields/{id}/`
- `PUT/PATCH /payslip/fields/{id}/`
- `DELETE /payslip/fields/{id}/` (soft delete)

---

## 5) Payslip Records (Workflow-Driven)

Endpoint base: `/payslip/records/`

Important:

- Direct create/update/delete of records is blocked.
- Use workflow actions only.

Allowed:

- list
- retrieve
- generate
- generate-bulk
- approve
- reopen
- mark-paid
- download

Blocked (returns `405`):

- `POST /payslip/records/`
- `PUT/PATCH /payslip/records/{id}/`
- `DELETE /payslip/records/{id}/`

---

## 5.1 List records

- Method: `GET`
- URL: `/payslip/records/`

Query params (optional):

- `month=YYYY-MM`
- `location_id=UUID`
- `user_id=UUID`
- `status=DRAFT|APPROVED|PAID`

Success:

- `200 OK`
- array of record objects

## 5.2 Retrieve record

- Method: `GET`
- URL: `/payslip/records/{id}/`

Success:

- `200 OK`
- full payslip record

---

## 5.3 Generate single record

- Method: `POST`
- URL: `/payslip/records/generate/`

Request:

```json
{
  "user_id": "UUID",
  "month": "2026-03",
  "field_config_id": "UUID",
  "template_id": "UUID"
}
```

`field_config_id` and `template_id` are optional:

- if omitted, backend auto-picks by employee profile location.

Success:

- `201 Created`
- returns generated/updated record

Errors:

- `404` employee not found
- `400` no active payroll profile
- `400` template/config not found for location
- `400` PAID lock message if month already PAID

---

## 5.4 Generate bulk

- Method: `POST`
- URL: `/payslip/records/generate-bulk/`

Request:

```json
{
  "user_ids": ["UUID1", "UUID2"],
  "month": "2026-03",
  "field_config_id": "UUID",
  "template_id": "UUID"
}
```

Success:

- `200 OK`
- shape:

```json
{
  "generated": [{ "...record..." }],
  "skipped": [
    {
      "user_id": "UUID2",
      "reason": {
        "error": "Active payroll profile not found for employee"
      }
    }
  ]
}
```

---

## 5.5 Approve record

- Method: `POST`
- URL: `/payslip/records/{id}/approve/`

Request:

```json
{
  "notes": "Verified by FO"
}
```

Success:

- `200 OK`
- status becomes `APPROVED`

Errors:

- `400` if already APPROVED
- `400` if PAID

---

## 5.6 Reopen record

- Method: `POST`
- URL: `/payslip/records/{id}/reopen/`

Request:

```json
{
  "notes": "Need correction"
}
```

Success:

- `200 OK`
- status becomes `DRAFT`
- approval fields cleared

Errors:

- `400` if already DRAFT
- `400` if PAID (locked)

---

## 5.7 Mark Paid

- Method: `POST`
- URL: `/payslip/records/{id}/mark-paid/`

Request:

```json
{
  "paid_amount": "25000.00",
  "paid_on": "2026-03-31T12:30:00Z",
  "payment_mode": "bank",
  "payment_ref_no": "UTR123456",
  "payment_notes": "Bank transfer"
}
```

`payment_mode` allowed values:

- `cash`
- `bank`
- `upi`
- `other`

Success:

- `200 OK`
- status becomes `PAID`

Errors:

- `400` if not in APPROVED state
- `400` if already PAID

---

## 5.8 Download PDF

- Method: `GET`
- URL: `/payslip/records/{id}/download/`

Success:

- `200 OK`
- `Content-Type: application/pdf`
- file download response

Behavior:

- if PDF missing, backend generates and saves it before responding.

---

## 6) Record Response Shape (Important Fields)

A record object includes (non-exhaustive):

```json
{
  "id": "UUID",
  "user": "UUID",
  "location": "UUID",
  "payroll_profile": "UUID",
  "field_config": "UUID",
  "template": "UUID",
  "month": "2026-03",
  "month_days": 31,
  "working_days": "31.00",
  "present_days": "20.00",
  "half_days": "4.00",
  "absent_days": "7.00",
  "paid_days": "22.00",
  "gross_salary": "30000.00",
  "total_earnings": "24000.00",
  "total_deductions": "2360.00",
  "net_pay": "21640.00",
  "field_values": {
    "BASIC": "18000.00",
    "HRA": "6000.00",
    "PF": "2160.00",
    "PT": "200.00"
  },
  "attendance_snapshot": {
    "month_days": 31,
    "working_days": "31",
    "present_days": "20",
    "half_days": "4",
    "absent_days": "7",
    "paid_days": "22.0"
  },
  "status": "DRAFT",
  "paid_amount": null,
  "paid_on": null,
  "payment_mode": null,
  "payment_ref_no": null,
  "payment_notes": null,
  "generated_on": "2026-03-29T10:20:00Z",
  "modified_on": "2026-03-29T10:20:00Z",
  "generated_by": "UUID",
  "approved_on": null,
  "approved_by": null,
  "paid_marked_by": null,
  "pdf_file": "/media/payslips/payslip_<...>.pdf",
  "user_name": "Guard Name",
  "location_name": "Site Name"
}
```

Note:

- decimal-like fields may be serialized as strings depending on DRF settings.

---

## 7) Frontend Integration Notes

- For create/edit screens, keep audit/system fields hidden; backend controls them.
- For formula builder UI:
  - suggest allowed variables list:
    - `gross_salary`
    - `month_days`
    - `working_days`
    - `present_days`
    - `half_days`
    - `absent_days`
    - `paid_days`
    - existing `field_code`s
  - show backend error text directly for formula issues.
- For records screen:
  - do not call direct create/update/delete
  - use action buttons:
    - Generate
    - Approve
    - Reopen
    - Mark Paid
    - Download PDF
- After each workflow action, refresh record detail/list to reflect latest status.

---

## 8) Typical Error Payloads

Examples:

```json
{"error": "Only APPROVED payslip can be marked as PAID"}
```

```json
{"error": "This month is already marked as PAID. You cannot regenerate this payslip."}
```

```json
{"value": "Unknown formula reference(s) in PF: UNKNOWN_VAR"}
```

```json
{"value": "Circular dependency detected at field 'A'"}
```

```json
{"error": "Use /records/generate/ to create payslip records"}
```

---

## 9) Minimal Frontend API Sequence

1. Ensure payroll profile exists for employee.
2. Ensure template exists for employee location.
3. Ensure field config + fields exist.
4. Call generate.
5. Show record detail.
6. Approve.
7. Mark paid.
8. Download PDF.

---

## 10) Status Transition Rules

- `DRAFT -> APPROVED` (allowed)
- `APPROVED -> DRAFT` via reopen (allowed)
- `APPROVED -> PAID` via mark-paid (allowed)
- `PAID` is final for regeneration and reopen.

---

## 11) Security/Behavior Guarantees

- guard cannot access payslip APIs.
- direct record mutation endpoints are blocked.
- formula expressions are restricted and validated.
- month attendance computation is timezone-safe.
- PDF file is stored and replaced safely on workflow updates.

