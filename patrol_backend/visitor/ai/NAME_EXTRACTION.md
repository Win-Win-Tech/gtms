# ID name extraction (after OCR)

This document explains how the visitor AI pipeline turns **OCR text lines** into a **correct cardholder name**.  
ID-number / plate extraction is separate; this covers **name only**.

**Main code:** `rapid_ocr_engine.py` (line order) → `parse.py` (`extract_id_name`) → called from `pipeline_v2.py`.

---

## End-to-end flow

```
Image upload
    → detect / crop ID region (YOLO / OpenCV)
    → RapidOCR → list of (text, confidence)
    → sort lines into reading order (top→bottom, left→right)
    → extract_id_number(...)  → document_hint (mykad, aadhaar, …)
    → extract_id_name(lines, document_hint)
         1. detect_id_card_type
         2. card-specific name extractor
         3. generic scorer fallback
         4. format (Malay glue split + title case)
    → API returns visitor_name
```

OCR alone does **not** pick the name. It only returns many text snippets (headers, address, DOB, blood group, name, noise).  
**Parsing** decides which snippet(s) are the person name.

---

## Why reading order matters

Older approach sorted OCR lines by **confidence**. That broke layout rules:

- Label → value on the **next** line (`Name:` then `RAVI KUMAR`)
- Name **above** DOB / Nationality
- Name **below** IC number and **above** address

`run_rapid_ocr` (`rapid_ocr_engine.py`) now:

1. Runs RapidOCR (each hit has a 4-point box + text + score).
2. Takes the box center `(cx, cy)`.
3. Clusters lines by similar `y` (same visual row).
4. Within a row, sorts left→right by `x`.
5. Returns `List[Tuple[str, float]]` in **reading order**.

Parsers assume that order.

---

## Entry point: `extract_id_name(lines, document_hint)`

```python
card_type = detect_id_card_type(lines, document_hint)
# pick extractor for mykad / my_dl / indian_dl / aadhaar / passport / labeled
name = extractor(lines) or _extract_name_generic(lines)
return _format_id_name(name)   # spacing + Title Case
```

`document_hint` comes from ID-number parsing (e.g. dashed 12-digit MyKad → `"mykad"`).  
It helps when OCR headers are weak, but **OCR keywords still override** when clear (e.g. `LESEN MEMANDU` → Malaysian DL).

---

## Step 1 — Detect card family

`detect_id_card_type` looks at joined OCR text + hint:

| Detected type   | Signals (examples)                                      |
|-----------------|---------------------------------------------------------|
| `my_dl`         | `LESEN MEMANDU`, `DRIVING LICENCE` (not Indian markers) |
| `indian_dl`     | `SON/DAUGHTER/WIFE`, Tamil Nadu / DL No, hint           |
| `mykad`         | `KAD PENGENALAN`, `MYKAD`, hint                         |
| `aadhaar`       | `AADHAAR`, `UIDAI`, `GOVERNMENT OF INDIA` + DOB/gender  |
| `passport`      | `SURNAME`, `GIVEN NAME`, Estonian labels, `PASSPORT`    |
| `labeled`       | Lines like `Name:`                                      |
| `unknown`       | → generic scorer only                                   |

Each family uses a **different layout rule** to find the name.

---

## Step 2 — Card-specific extractors

### MyKad (`_extract_name_mykad`)

Typical layout (top → bottom):

1. Header (`MALAYSIA`, `KAD PENGENALAN`)
2. **IC number** (`YYMMDD-PB-####`)
3. **Name** (1–2 lines, often with `BIN` / `BINTI`)
4. Address (`NO`, `JALAN`, postcode, state)

**Rule:** find the IC line → take following name-like lines → **stop** at address / header / long digit runs.

Rejects address stems (`JALAN`, `TAMAN`, `TOWER`, …) and known headers.

### Malaysian DL (`_extract_name_my_dl`)

- Large name under header / near Nationality.
- Long names often wrap: line 1 ends with `BIN`, line 2 is father name.
- Collects all “name-ish” lines, ranks by patronymic + length + confidence.
- Merges a `… BIN` line with a nearby Malay name line when needed.
- Soft-splits glued OCR: `NURMOHDFAZARIBINMOHD…` → spaced tokens.

### Indian DL (`_extract_name_indian_dl`)

1. Prefer text **after** a `Name` / `Nama` label (lookahead past dates / blood group).
2. Else: line **before** `Son/Daughter/Wife of` relation marker.

### Aadhaar (`_extract_name_aadhaar`)

- Keep **Latin** letters only (drop Devanagari / script noise).
- Prefer lines **near DOB** (above / slightly below).
- Also check letter form after `To`, then score all Latin candidates.
- Score boosts: CamelCase (`FirdosAlam`), multi-word names.
- Score penalties: all-lowercase junk (`hellehlpt`), low vowel soup.

### Passport / EU-style (`_extract_name_passport`)

- Find `Surname` / `Given name` labels (including glued bilingual labels).
- Take next line value(s); combine `Given + Surname` when both exist.

### Labeled badge (`_extract_name_labeled_badge`)

- Inline `Name: John Doe` or label-only line then next-line value.

---

## Step 3 — Shared filters (`_candidate_ok`)

Before a line can be a name it must pass:

| Check | Rejects |
|-------|---------|
| Address heuristics | postcodes, `JALAN`, Malaysian states + ZIP |
| Header / label | `MALAYSIA`, `MYKAD`, `DRIVING LICENCE`, glued label vocab |
| Date / blood group | `12/05/1990`, `A+`, etc. |
| OCR junk | short blobs, all-lowercase Latin, very low vowel ratio |
| Sex tokens | `M`, `F`, `M/M` |
| Long digit runs | IC / Aadhaar fragments mistaken as name |
| Structure | 1–12 words, length 3–90, not pure noise words |
| Noise word list | `WARGANEGARA`, `HELLEHLPT`, `GOVERNMENTOFINDIA`, … |

Surviving text is cleaned (`_clean_name_token`) and may be soft-split for Malay glue.

---

## Step 4 — Malay / OCR glue repair

Printed Malaysian names are often OCR’d **without spaces**.

`_soft_split_glued_malay_name` / `_split_glued_malay_tokens`:

- Split on known particles (longest first): `BIN`, `BINTI`, `MOHD`, `MUHAMMAD`, `TENGKU`, `NUR`, `BUDI`, …
- CamelCase English: `FirdosAlam` → `Firdos Alam`
- Avoid false splits: `BINA` is **not** `BIN` + `A`

`_format_id_name` always runs at the end:

1. Soft-split  
2. Clean to letters + spaces  
3. Title-case words; keep `BIN` / `BINTI` upper  

Example:

```
NURMOHDFAZARIBINMOHDSHANJI
  → Nur Mohd Fazari Bin Mohd Shanji
```

---

## Step 5 — Generic fallback (`_extract_name_generic`)

Used when card type is unknown or the layout extractor returns nothing.

1. Try passport / badge / Indian-DL label rules first.
2. Score every remaining line with `_name_line_score`:
   - letter ratio, word count, case
   - near a name label / near ID number / **after** ID number
   - Malay `BIN`/`BINTI` bonus
   - first line penalty (often a header)
3. Optionally merge two adjacent high-scoring lines into one full name.

This is **last resort** — card-specific rules are preferred.

---

## What went wrong before (and why this works)

| Old behaviour | Problem | Fix |
|---------------|---------|-----|
| Sort OCR by confidence | Broke label→value and above/below anchors | Reading-order sort by box position |
| One generic scorer for all cards | Picked address / header / blood group | Per-card layout extractors |
| Ignore `document_hint` for names | Wrong family rules | Hint + OCR keywords → `detect_id_card_type` |
| No glue handling | `BUDIUTOMO`, `…BIN…` as one token | Particle split + CamelCase split |
| Treat all scripts as name | Hindi misreads / `hellehlpt` | Latin-only Aadhaar path + junk filters |

---

## Quick debug checklist

If the wrong name is returned:

1. Log `lines` from `run_rapid_ocr` — are they top→bottom?
2. Log `document_hint` and `detect_id_card_type(...)` — wrong family?
3. Check whether the true name line fails `_candidate_ok` (address/header/junk).
4. For MyKad: is the IC line detected so the search starts in the right place?
5. For glued Malay names: does `_format_id_name` split particles correctly?

---

## Related files

| File | Role |
|------|------|
| `rapid_ocr_engine.py` | OCR + reading-order sort |
| `parse.py` | ID / plate / **name** parsing |
| `pipeline_v2.py` | Orchestrates detect → OCR → parse |
| `detect.py` | ID region crop / warp |
| `preprocess.py` | Resize before OCR |
