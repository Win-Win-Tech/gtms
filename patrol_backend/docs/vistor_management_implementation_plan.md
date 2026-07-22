# GTMS Visitor Management — Implementation Plan

Revised plan for the **Visitor Entry & Visitor Management** module.

**Locked decisions (updated)**
- **Iteration 1 = manual entry + host approval + reschedule** (models + APIs + web). AI/OCR is **Iteration 2**.
- **Invitations / preregister** are **Iteration 3**, but follow the **same host-approval model** (not direct QR check-in).
- `ic_passport_number` is **unique per location** (not global).
- **No `document_type` field** — AI will detect document type later when OCR lands.
- Manual submit sets **`visit_date` = today** (location timezone). Guard may also set `expected_arrival_time` / `expected_out_time`.
- **`visit_date`** is also used when an entry is **rescheduled to another day** (manual or invite).
- **Host approve = check-in** (never auto check-in on QR for pending/scheduled arrival).
- **Reject = Cancel** — same status `cancelled`, `qr_expired=true` (UI may say Reject).
- Host actions on `pending_approval`: **Approve** | **Reject/Cancel** | **Revert** | **Reschedule**.
- **Reschedule** always updates **`expected_arrival_time` + `expected_out_time`** (and `visit_date` when date changes):
  - **Same day, different time** → keep **`pending_approval`**
  - **Another day** → set new `visit_date` → status **`scheduled`**
- **Reschedule never checks anyone in.** Only **Approve** does.
- Checkout (QR or manual mark) always requires **checkout image**; then **QR expires**.
- Push notifications — **deferred** (wire later; APIs should still be host-action ready).
- When AI lands: OCR/plate results always **editable**.

Aligns with GTMS conventions: app prefix `/visitors/`, location scoping, soft delete, JWT auth, Excel export, `MOBILE_API_CURL.md`.

---

## Why this order

| Approach | Verdict |
|----------|---------|
| **A. Manual + approval + reschedule first, AI second, invite third** | **Preferred.** Gatehouse + host workflow ships first; invite reuses the same approval/reschedule rules. |
| **B. AI + manual together early** | Riskier. YOLO/PaddleOCR on critical path. |

---

## Core product rules

### Mental model

```text
CREATE
  manual  → pending_approval (+ visit_date=today, QR)
  invite  → scheduled (+ visit_date, QR)   [Iter 3]

ARRIVAL (QR scan)
  scheduled + visit day     → pending_approval + notify host → awaiting_approval
  scheduled + future day     → too_early
  pending_approval           → awaiting_approval
  reverted                   → reverted (guard resubmit)
  checked_in                 → checkout

HOST (pending_approval)
  Approve    → checked_in
  Reject     → cancelled (= cancel)
  Revert     → reverted → guard resubmits → pending_approval
  Reschedule → update arrival + out (+ visit_date if needed)
               same day → pending_approval
               other day → scheduled

CHECKOUT
  checked_in + exit_photo → checked_out + qr_expired
```

### Manual entry (walk-in) — Iteration 1
1. Guard fills form (visitor photo + IC copy required; additional images optional multi).
2. Host is **required**. Optional: expected arrival / expected out.
3. Submit → `pending_approval` + QR + `visit_date=today`.
4. Host:
   - **Approve** (optional override `expected_out_time`) → `checked_in`, `check_in_time=now()`.
   - **Reject** → `cancelled`, `qr_expired=true` (same as cancel).
   - **Revert** (+ reason) → `reverted`; guard corrects and **resubmits** (`entry_id`).
   - **Reschedule** (required: `expected_arrival_time` + `expected_out_time`; optional/explicit `visit_date`):
     - Same calendar day (location TZ) → keep `pending_approval`.
     - Later calendar day → `visit_date` = that day, status `scheduled`.
5. QR while `pending_approval` → **“Not approved yet”**.

### Invitation / preregister — Iteration 3 (same approval rules)
1. Create invitation with **`visit_date`** (+ visitor details, host, QR) → status **`scheduled`**.
2. On visit day, mobile scans QR → status **`pending_approval`**, host notified → **not** auto check-in.
3. Host uses the **same** Approve / Reject / Revert / Reschedule actions.
4. After reschedule to another day → back to **`scheduled`**; next arrival scan again enters approval.

### Checkout (both methods)
| Method | Rule |
|--------|------|
| Scan QR while `checked_in` | Opens checkout; **checkout image required** |
| Manual “Mark Exit” | Same; **checkout image required** |

After checkout → `checked_out`, `qr_expired=true`.

### QR lifecycle (single token, status-driven)
| Status | QR scan result |
|--------|----------------|
| `pending_approval` | `awaiting_approval` — not approved yet |
| `reverted` | `reverted` — guard must resubmit |
| `scheduled` + visit day | → `pending_approval` + `awaiting_approval` (start host approval) |
| `scheduled` + future day | `too_early` |
| `checked_in` | `checkout` — proceed with image |
| `checked_out` / `cancelled` / `qr_expired` | Expired / invalid |

---

## 1. Database design

Three tables in Django app `visitor` (existing). Key fields on `VisitorEntry`:
- `status`: pending_approval | reverted | scheduled | checked_in | checked_out | cancelled
- `expected_arrival_time`, `expected_out_time`, `visit_date`
- `entry_source`: manual | invitation
- QR: `qr_token`, `qr_image`, `qr_expired`

**Reject** is a UI label for **cancel** (`cancelled`). No new status.

---

## 2. API surface (`/visitors/`)

### Iteration 1 — Manual + approval + reschedule + checkout

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/visitors/search/` | Lookup by IC |
| `POST` | `/visitors/entries/checkin/` | Manual submit / resubmit |
| `POST` | `/visitors/entries/<id>/approve/` | Host approve = check-in |
| `POST` | `/visitors/entries/<id>/revert/` | Host revert |
| `POST` | `/visitors/entries/<id>/cancel/` | Reject/Cancel |
| `POST` | `/visitors/entries/<id>/reschedule/` | Host reschedule arrival + out (+ date) |
| `POST` | `/visitors/entries/<id>/checkout/` | Checkout + exit photo |
| `POST` | `/visitors/qr-scan/` | Status-driven scan |
| `GET` | `/visitors/entries/` | List / history |
| `GET` | `/visitors/entries/export/` | Excel |

### Reschedule body
```json
{
  "expected_arrival_time": "2026-07-22T14:00",
  "expected_out_time": "2026-07-22T18:00",
  "visit_date": "2026-07-22"
}
```
- `expected_arrival_time` + `expected_out_time` **required**
- `visit_date` optional (else from arrival local date)
- same day → `pending_approval`; future day → `scheduled`

### QR scan (updated)
- `scheduled` on visit day → set `pending_approval`, return `awaiting_approval`
- `scheduled` future day → `too_early`
- No auto check-in from `scheduled`

---

## 3–5. AI / Web / Mobile

- Iter 2: OCR helpers (unchanged intent)
- Web: Visitors tab + Manual Entry; host actions include **Reschedule** and **Reject**
- Mobile: document in `visitor/MOBILE_API_CURL.md` including reschedule
- Push notifications: later

---

## 6. Schedules

1. **Now:** plan + reschedule API + QR arrival-approval rules + web host Reschedule/Reject on manual  
2. **Next:** invitation create UI/APIs using the same host actions  
3. **Later:** Invitations (Iter 3), push notifications

### Iter 2 AI (backend ready for Postman)
- Endpoint: `POST /visitors/ai/extract/` (`type=id|vehicle`)
- Docs + Ubuntu/Windows setup: `visitor/docs/VISITOR_AI_OCR.md`
- Extra pip: `visitor/requirements-visitor-ai.txt`
