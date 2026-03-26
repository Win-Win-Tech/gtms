# GTMS Payslip Backend - Detailed Implementation Guide

This document explains what was implemented in the payslip backend, why each part exists, and how it works in real flows.

It is intentionally long and practical so new developers can onboard quickly.

---

## 1) What Was Built

The backend now has a dedicated `payslip` app with:

- Payroll profile per employee (`EmployeePayrollProfile`)
- Payslip template per location (`PayslipTemplate`)
- Salary field configuration per location (`PayslipFieldConfig` + `PayslipField`)
- Monthly payslip record with workflow (`PayslipRecord`)
- Attendance-based day summary using `AttendanceCheckin` master table
- Formula-driven salary calculation engine
- Formula validation (syntax + unknown dependencies + circular dependencies + AST safety)
- Workflow endpoints:
  - generate
  - generate-bulk
  - approve
  - reopen
  - mark-paid
  - download PDF
- PDF file storage/replacement in media
- Guard-blocked access policy (admin/so/fo/superuser only)
- Hardening for production:
  - no direct record CRUD mutation
  - location-safe template/config selection
  - timezone-safe month boundary handling
  - safer formula evaluation (no raw `eval` execution path)

---

## 2) App Wiring

### 2.1 Installed app

`payslip` is added to Django `INSTALLED_APPS`, so migrations/models/views are loaded.

### 2.2 URL routing

Project routes include:

- `path('payslip/', include('payslip.urls'))`

Inside `payslip/urls.py`, DRF router exposes:

- `/payslip/payroll-profiles/`
- `/payslip/templates/`
- `/payslip/field-configs/`
- `/payslip/fields/`
- `/payslip/records/`

and custom actions on records/config:

- `/payslip/field-configs/create-default/`
- `/payslip/records/generate/`
- `/payslip/records/generate-bulk/`
- `/payslip/records/{id}/approve/`
- `/payslip/records/{id}/reopen/`
- `/payslip/records/{id}/mark-paid/`
- `/payslip/records/{id}/download/`

---

## 3) Data Model Deep Dive (`models.py`)

## 3.1 `EmployeePayrollProfile`

Purpose: keep payroll-specific fields separate from the `User` table.

Key fields:

- `user` (OneToOne): one payroll profile per employee
- `location`: payroll/location ownership
- `employee_code`: unique per location
- `salary_type`: currently monthly
- `gross_salary`: base gross pay
- bank details: account/bank/IFSC etc.
- statutory flags: `pf_enabled`, `esi_enabled`, related numbers
- validity window: `effective_from`, `effective_to`
- audit fields: created/modified by + timestamps

Constraints:

- `unique_together = (location, employee_code)`

Example:

- Location A can have `EMP001`
- Location B can also have `EMP001` (allowed)
- Same location cannot duplicate same code

---

## 3.2 `PayslipTemplate`

Purpose: location-level branding/layout settings.

Important details:

- One template per location (`OneToOneField`)
- Optional company logo (`ImageField`) saved in media
- Header/footer text, colors, layout metadata
- Soft-delete via `is_deleted`

Why soft delete:

- old references remain valid
- admin can hide templates without hard data loss

---

## 3.3 `PayslipFieldConfig` and `PayslipField`

Purpose: configurable salary structure.

`PayslipFieldConfig`:

- belongs to location
- has `is_active`, `is_deleted`
- named config (for future multiple sets)

`PayslipField`:

- belongs to config
- `field_code` as machine key (e.g. `BASIC`, `PF`)
- `field_type`: EARNING / DEDUCTION / INFO
- `value_type`: FIXED / PERCENTAGE / FORMULA
- `value`: raw configured value or formula expression
- `display_order`, visibility flags

Constraint:

- `unique_together = (field_config, field_code)`

Example set:

- `BASIC`: 60% of gross
- `HRA`: 20% of gross
- `PF`: formula `BASIC * Decimal('0.12')`

---

## 3.4 `PayslipRecord`

Purpose: immutable monthly payroll output (except allowed workflow transitions).

Core columns:

- identity: `user`, `location`, `month`
- source references: profile/template/config used
- attendance snapshot: month_days, present/half/absent/paid days
- salary outputs: gross, total_earnings, total_deductions, net_pay
- `field_values` JSON (final amount per field code)
- `attendance_snapshot` JSON (frozen calculation input)
- workflow: `status` (`DRAFT`, `APPROVED`, `PAID`)
- payment info: paid amount/date/mode/ref/notes
- `pdf_file` (`FileField`) stores generated PDF path

Constraint:

- `unique_together = (user, month)` so only one record per month per user

Business lock:

- If status becomes `PAID`, generation/update is blocked for that month.

---

## 4) Service Layer (`services.py`)

This file has 2 major responsibilities:

1. Attendance summary extraction from master table
2. Salary field calculation engine

---

## 4.1 `parse_month(month_str)`

Input:

- `YYYY-MM` string

Output:

- `year`, `month`, `first_day`, `last_day`

Why:

- All monthly operations need deterministic date boundaries.

Example:

- Input: `2026-03`
- Output:
  - year: 2026
  - month: 3
  - first_day: 2026-03-01
  - last_day: 2026-03-31

---

## 4.2 `calculate_attendance_from_master(user, month_str, user_tz)`

### Step-by-step logic

1. Parse month boundaries.
2. Convert local month start/end to UTC using:
   - `convert_date_range_to_utc(month_start, month_end, user_tz)`
3. Query `AttendanceCheckin` for this user in that UTC window.
4. For each row:
   - choose `checkin_time` or fallback `last_checkin_time`
   - convert that datetime to user timezone via `to_user_timezone(...)`
   - use local date as day key
5. If multiple records map to same day:
   - keep latest by `modified_on`
6. Walk every calendar day in month:
   - `P` => present +1
   - `HA` => half +1
   - `A` => absent +1
   - no record => absent +1 (current business rule)
7. Compute:
   - `working_days = month_days`
   - `paid_days = present_days + half_days * 0.5`

### Why this is important

- Uses attendance master table directly (fast, aligned with your V3 model)
- No heavy raw log recomputation
- Timezone-safe month slicing and day attribution

### Example

For March (31 days):

- P = 20
- HA = 4
- A = 5
- no-entry days = 2 (treated absent)

Then:

- `absent_days = 7`
- `paid_days = 20 + (4 * 0.5) = 22`

---

## 4.3 Safe Formula Evaluation (`_eval_formula_safely`)

This replaced the unsafe runtime formula path.

Allowed expression elements:

- constants (`100`, `1.5`)
- known names from context (`gross_salary`, `BASIC`, etc.)
- math operators (`+ - * / % // **`)
- unary (`-x`, `+x`)
- `Decimal(...)` function only

Rejected:

- arbitrary function calls
- attribute access
- subscripting
- comprehensions/lambdas
- imports or execution primitives

If expression fails, value falls back to `0`.

---

## 4.4 `calculate_salary_fields(gross_salary, field_rows, attendance_snapshot)`

### Calculation flow

1. Build base context:
   - gross salary
   - month_days, working_days, present/half/absent/paid days
2. Iterate fields in display order.
3. Per field:
   - `FIXED`: parse numeric directly
   - `PERCENTAGE`: `gross * pct / 100`
   - `FORMULA`: evaluate safely using current context + already computed fields
4. Round each amount to 2 decimals.
5. Add to totals by field type:
   - EARNING => `total_earnings`
   - DEDUCTION => `total_deductions`
6. `net_pay = total_earnings - total_deductions`
7. Return field-wise map + totals.

### Example

- gross = 30000
- BASIC = 60% => 18000
- HRA = 20% => 6000
- PF formula `BASIC * Decimal('0.12')` => 2160
- PT fixed 200

Totals:

- earnings = 24000 (if only BASIC+HRA)
- deductions = 2360 (PF+PT)
- net = 21640

---

## 5) Serializer Layer (`serializers.py`)

This layer validates input and controls read/write exposure.

---

## 5.1 Read-only hardening

For model serializers, sensitive/system fields were marked `read_only_fields`:

- audit fields (`created_by`, `modified_by`, timestamps)
- deletion flags (`is_deleted`)
- record-computed fields (days, totals, snapshot, status, PDF, workflow actors)

Why:

- client cannot spoof server-owned values
- prevents accidental or malicious state tampering

---

## 5.2 `PayslipFieldSerializer` formula validation

Validation now includes:

1. Numeric check for `FIXED`/`PERCENTAGE`
2. Formula non-empty check
3. Syntax check using AST parse
4. AST safety check (only allowed node types)
5. Unknown dependency check:
   - symbols must be either system vars or existing field codes
6. Circular dependency detection across formulas

### Circular example

- `A = B + 1`
- `B = A + 1`

Result:

- validation error: circular dependency detected

### Unknown symbol example

- formula `UNKNOWN_VAR * 2`

Result:

- validation error: unknown reference

---

## 5.3 Action serializers

Dedicated serializers exist for workflow payloads:

- `PayslipGenerateSerializer`
- `PayslipBulkGenerateSerializer`
- `PayslipMarkPaidSerializer`
- `PayslipApproveSerializer`
- `PayslipReopenSerializer`

Why:

- clean endpoint contracts
- easier field-level validation

---

## 6) View Layer (`views.py`)

This is the workflow and permission core.

---

## 6.1 Access policy (`AdminOnlyMixin`)

`initial()` denies non-admin-like roles.

Allowed:

- superuser
- role in `{admin, so, fo}`

Denied:

- guard and others

---

## 6.2 Profile/template/config CRUD

These are standard model viewsets with:

- soft-delete for template/config/field
- server-side audit assignment on create/update:
  - `created_by`
  - `modified_by`

`create-default` action builds a starter salary config for a location.

Default fields include:

- BASIC, HRA, CONVEYANCE, MEDICAL, PF, PT, ABSENT_DEDUCTION

---

## 6.3 `PayslipRecordViewSet` hard workflow mode

### What is blocked

Direct CRUD mutations are blocked:

- `POST /records/` -> 405
- `PUT/PATCH /records/{id}/` -> 405
- `DELETE /records/{id}/` -> 405

Reason:

- records must move only through audited workflow endpoints

### What remains allowed

- list/retrieve records
- workflow actions (`generate`, `approve`, `reopen`, `mark-paid`, `download`)

---

## 6.4 `_resolve_template` and `_resolve_field_config`

If explicit IDs are passed, they are now validated against profile location.

Why:

- prevents using template/config from wrong location.

---

## 6.5 `_generate_for_user` lifecycle

Step-by-step:

1. load active payroll profile for employee
2. resolve template and field config for profile location
3. compute attendance snapshot from master
4. load visible fields and calculate salary totals
5. fetch existing record for `(user, month)`
6. enforce PAID lock
7. build payload (attendance + salary + references)
8. create or overwrite record (if editable)
9. save/replace PDF snapshot

Overwrite behavior:

- existing `DRAFT`/`APPROVED` can be regenerated
- existing `PAID` cannot be regenerated

---

## 6.6 Workflow actions

### `generate`

- one user, one month
- returns created/updated payslip record

### `generate-bulk`

- multiple users
- response has:
  - `generated` list
  - `skipped` list with reasons

### `approve`

- allowed only from `DRAFT`
- sets `APPROVED`, approver and timestamp
- refreshes PDF snapshot

### `reopen`

- allowed only from `APPROVED`
- moves back to `DRAFT`
- clears approval metadata
- refreshes PDF

### `mark-paid`

- allowed only from `APPROVED`
- saves paid amount/mode/ref/date/notes
- sets `PAID`, marks actor
- refreshes PDF

### `download`

- returns PDF file
- if missing, auto-generates before download

---

## 7) PDF Handling

PDF is generated by `build_simple_payslip_pdf_bytes(record)`.

Storage behavior:

1. when regenerating, old file is deleted
2. new file stored in `payslips/` via `FileField`
3. DB keeps the file path in `record.pdf_file`

This matches your requirement similar to incident/attendance attachment storage style.

---

## 8) End-to-End Example (Realistic)

Scenario:

- Employee gross salary = 30000
- March month
- Attendance summary:
  - present = 20
  - half = 4
  - absent = 7
  - paid_days = 22

Fields:

- BASIC 60%
- HRA 20%
- PF `BASIC * 0.12`
- PT fixed 200
- ABSENT_DEDUCTION `(gross_salary / month_days) * absent_days`

Flow:

1. Admin calls generate
2. Record created in `DRAFT`, PDF generated
3. Admin calls approve
4. Record becomes `APPROVED`, PDF refreshed
5. Finance calls mark-paid with actual paid amount
6. Record becomes `PAID`
7. Later generate for same month:
   - blocked with PAID-lock error

---

## 9) Production Hardening Done vs Pending

Done:

- workflow lock-down for record mutation
- location-safe template/config selection
- serializer read-only guard for system fields
- safe formula evaluation path
- AST safety validation in formula input
- timezone-safe attendance month windows

Pending (as per your instruction):

- tests restoration/execution in this workspace

---

## 10) Quick API Usage Examples

## 10.1 Generate

`POST /payslip/records/generate/`

```json
{
  "user_id": "USER_UUID",
  "month": "2026-03",
  "field_config_id": "CONFIG_UUID",
  "template_id": "TEMPLATE_UUID"
}
```

## 10.2 Approve

`POST /payslip/records/{id}/approve/`

```json
{
  "notes": "Validated by FO"
}
```

## 10.3 Mark Paid

`POST /payslip/records/{id}/mark-paid/`

```json
{
  "paid_amount": "25000.00",
  "payment_mode": "bank",
  "payment_ref_no": "UTR12345",
  "payment_notes": "Monthly transfer"
}
```

## 10.4 Reopen

`POST /payslip/records/{id}/reopen/`

```json
{
  "notes": "Need correction"
}
```

## 10.5 Download PDF

`GET /payslip/records/{id}/download/`

Returns `application/pdf`.

---

## 11) Why This Design Fits GTMS

- Uses existing attendance master table (`AttendanceCheckin`) for fast monthly math
- Keeps payroll concerns isolated in one app
- Supports no-LMS phase now, extendable later
- Prevents accidental workflow bypass
- Keeps generated month output traceable and downloadable

---

## 12) File Map

- `payslip/models.py`: data schema
- `payslip/serializers.py`: validation + field exposure
- `payslip/services.py`: attendance + salary engine
- `payslip/views.py`: workflow, permissions, API logic
- `payslip/urls.py`: routing
- `payslip/pdf_utils.py`: PDF bytes generation
- `payslip/migrations/0001_initial.py`: initial DB setup

---

## 13) Final Notes for Team

1. Do not re-enable direct `PayslipRecord` CRUD mutations.
2. Keep formula parser restricted (never switch back to free eval).
3. Keep month calculations timezone-safe for global/customer deployments.
4. Restore and run test coverage before release freeze.

