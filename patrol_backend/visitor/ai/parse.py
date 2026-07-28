"""
Parse OCR lines into ID numbers / vehicle plate numbers.
"""

from __future__ import annotations

import re
from typing import List, Optional, Set, Tuple

# Malaysian NRIC: YYMMDD-PB-###G (prefer dashed form from real MyKad prints)
MY_IC_DASHED_RE = re.compile(
    r"(?<!\d)(\d{6})\s*-\s*(\d{2})\s*-\s*(\d{4})(?!\d)"
)

# Loose (no dashes) — only used with stronger validation + not from date text
MY_IC_LOOSE_RE = re.compile(
    r"(?<!\d)(\d{6})\s+(\d{2})\s+(\d{4})(?!\d)"
)

# OCR letter/digit confusions in dashed MyKad
MY_IC_OCR_DASHED_RE = re.compile(
    r"(?<![A-Z0-9])([0-9OILSBZ]{6})\s*-\s*([0-9OILSBZ]{2})\s*-\s*([0-9OILSBZ]{4})(?![A-Z0-9])",
    re.IGNORECASE,
)

# DD/MM/YYYY or date ranges — never treat these as MyKad
DATE_TEXT_RE = re.compile(
    r"\b\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{2,4}\b"
    r"|\b\d{1,2}\s*-\s*(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*-?\s*\d{2,4}\b",
    re.IGNORECASE,
)

# Indian Aadhaar: 12 digits, often grouped 4-4-4
AADHAAR_RE = re.compile(
    r"\b(\d{4})\s*[-\s]?\s*(\d{4})\s*[-\s]?\s*(\d{4})\b"
)

# Indian Driving Licence (longer than vehicle plates)
IN_DL_RE = re.compile(
    r"\b([A-Z]{2})\s*-?\s*(\d{2})\s*-?\s*([A-Z]?)\s*-?\s*(\d{4,11})\b",
    re.IGNORECASE,
)

# Label then value — capture value only (not "ID No" letters)
LABELED_ID_RE = re.compile(
    r"(?:DL\.?\s*No\.?|D\.?\s*L\.?\s*No\.?|Licence\s*No\.?|License\s*No\.?"
    r"|Document\s*(?:No\.?|Number)|Doc(?:ument)?\.?\s*No\.?"
    r"|ID\s*No\.?|ID\s*Number|Identity\s*No\.?"
    r"|No\.?\s*Pengenalan|Personal\s*Code|Passport\s*No\.?)"
    r"\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{4,20})",
    re.IGNORECASE,
)

# Digits right after ID No / Identity No (employee badges, etc.)
ID_NO_DIGITS_RE = re.compile(
    r"(?:ID\s*No\.?|ID\s*Number|Identity\s*No\.?|No\.?\s*Pengenalan)"
    r"\s*[:\-]?\s*(\d{6,14})",
    re.IGNORECASE,
)

# Generic passport / national ID token (letters then digits), e.g. AS0003822
PASSPORT_RE = re.compile(
    r"\b([A-Z]{1,2}\d{6,9})\b",
    re.IGNORECASE,
)

# Mixed alphanumeric document id (8–18) — last-resort
GENERIC_DOC_RE = re.compile(
    r"\b(?=[A-Z0-9]*[A-Z][A-Z0-9]*\d|\d[A-Z0-9]*[A-Z])([A-Z0-9]{8,18})\b",
    re.IGNORECASE,
)

IN_PLATE_RE = re.compile(
    r"\b([A-Z]{2})\s*[-\s]?\s*(\d{1,2})\s*[-\s]?\s*([A-Z]{1,3})\s*[-\s]?\s*(\d{1,4})\b",
    re.IGNORECASE,
)

MY_PLATE_RE = re.compile(
    r"\b([A-Z]{1,3})\s*[-\s]?\s*(\d{1,4})\s*[-\s]?\s*([A-Z]{0,3})\b",
    re.IGNORECASE,
)

# Malaysian NRIC place-of-birth codes (reject date-soup false positives like PB=17)
_MY_PB_CODES: Set[str] = {
    f"{i:02d}"
    for i in list(range(1, 17))
    + list(range(21, 40))
    + list(range(41, 60))
    + [71, 72, 73, 74, 82, 86, 87, 88]
    + list(range(91, 99))
}

_SKIP_WORDS = {
    "NAME",
    "ADDRESS",
    "BLOOD",
    "GROUP",
    "VALID",
    "TILL",
    "ISSUE",
    "DATE",
    "BIRTH",
    "DRIVING",
    "LICENCE",
    "LICENSE",
    "INDIA",
    "UNION",
    "TAMIL",
    "NADU",
    "SPECIMEN",
    "CITIZENSHIP",
    "AUTHORITY",
    "MALAYSIA",
    "SELANGOR",
    "MYKAD",
    "IDENTITY",
    "TEMPOH",
    "VALIDITY",
    "ALAMAT",
}

# Label fragments OCR often glues onto the number
_LABEL_PREFIX_RE = re.compile(
    r"^(?:IDNO|DNO|IDN0|NO|ID)(?=\d)",
    re.IGNORECASE,
)


def _join_lines(lines: List[Tuple[str, float]]) -> str:
    return " ".join(t for t, _ in lines)


def _normalize_in_dl(m: re.Match) -> Optional[str]:
    raw = f"{m.group(1)}{m.group(2)}{(m.group(3) or '')}{m.group(4)}".upper()
    if len(raw) < 13:
        return None
    return raw


def _clean_id_token(token: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", token).upper()
    raw = _LABEL_PREFIX_RE.sub("", raw)
    return raw


def _ocr_to_digits(text: str) -> str:
    t = text.upper()
    for src, dst in (
        ("O", "0"),
        ("Q", "0"),
        ("I", "1"),
        ("L", "1"),
        ("|", "1"),
        ("S", "5"),
        ("B", "8"),
        ("Z", "2"),
    ):
        t = t.replace(src, dst)
    return re.sub(r"\D", "", t)


def _looks_like_mykad(digits: str) -> bool:
    """12 digits + plausible YYMMDD + valid Malaysian place-of-birth code."""
    if len(digits) != 12 or not digits.isdigit():
        return False
    mm = int(digits[2:4])
    dd = int(digits[4:6])
    if not (1 <= mm <= 12 and 1 <= dd <= 31):
        return False
    pb = digits[6:8]
    return pb in _MY_PB_CODES


def _is_date_noise_text(text: str) -> bool:
    """True if line is (or contains) calendar dates — not an IC number."""
    if DATE_TEXT_RE.search(text):
        return True
    if text.count("/") >= 2 and re.search(r"\d/\d", text):
        return True
    return False


def _find_mykad_candidates(text: str, score: float) -> List[Tuple[str, float, str]]:
    out: List[Tuple[str, float, str]] = []
    if _is_date_noise_text(text):
        return out

    for m in MY_IC_DASHED_RE.finditer(text):
        raw = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        if _looks_like_mykad(raw):
            out.append((raw, score, "mykad"))

    for m in MY_IC_OCR_DASHED_RE.finditer(text):
        raw = _ocr_to_digits(f"{m.group(1)}{m.group(2)}{m.group(3)}")
        if _looks_like_mykad(raw):
            out.append((raw, score * 0.98, "mykad"))

    # Spaced groups without slash dates: 850122 07 4553
    for m in MY_IC_LOOSE_RE.finditer(text):
        raw = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        if _looks_like_mykad(raw):
            out.append((raw, score * 0.92, "mykad"))

    # Compact 12 digits only when the line is mostly that number (not date soup)
    digits = _ocr_to_digits(text)
    if len(digits) == 12 and _looks_like_mykad(digits):
        # Require dashes somewhere in original OR high digit ratio
        if "-" in text or re.fullmatch(r"[\d\s\-]+", text.strip()):
            out.append((digits, score * 0.9, "mykad"))
    return out


def extract_id_number(lines: List[Tuple[str, float]]) -> Optional[Tuple[str, float, str]]:
    """
    Returns (normalized_id, confidence, hint) or None.
    hint: mykad | aadhaar | indian_dl | passport | labeled | unknown
    """
    if not lines:
        return None
    blob = _join_lines(lines)
    candidates: List[Tuple[str, float, str]] = []

    def add(raw: str, score: float, hint: str) -> None:
        raw = _clean_id_token(raw)
        if len(raw) < 6 or raw in _SKIP_WORDS:
            return
        if raw.isalpha() and len(raw) > 9:
            return
        # Pure digit runs that are clearly concatenated dates (16 digits from two YYYY)
        if raw.isdigit() and len(raw) in (12, 16) and hint == "mykad":
            if not _looks_like_mykad(raw[:12] if len(raw) >= 12 else raw):
                return
        candidates.append((raw, score, hint))

    for text, score in lines:
        if _is_date_noise_text(text):
            # Still allow labeled ID on same line after a date? skip whole line for safety
            m_id = ID_NO_DIGITS_RE.search(text)
            if m_id:
                add(m_id.group(1), score, "labeled")
            continue

        compact = text.replace(" ", "")

        for raw, sc, hint in _find_mykad_candidates(text, score):
            add(raw, sc, hint)
        for raw, sc, hint in _find_mykad_candidates(compact, score):
            add(raw, sc, hint)

        m = ID_NO_DIGITS_RE.search(text)
        if m:
            add(m.group(1), score, "labeled")

        m = LABELED_ID_RE.search(text)
        if m:
            val = _clean_id_token(m.group(1))
            # Prefer digit-only values for ID No fields
            if val.isdigit() and len(val) >= 6:
                add(val, score, "labeled")
            elif len(val) >= 6 and not val.startswith("NO"):
                add(val, score * 0.9, "labeled")

        m = AADHAAR_RE.search(text)
        if m:
            raw = f"{m.group(1)}{m.group(2)}{m.group(3)}"
            if _looks_like_mykad(raw):
                add(raw, score, "mykad")
            elif not _is_date_noise_text(text):
                add(raw, score, "aadhaar")

        m = IN_DL_RE.search(text) or IN_DL_RE.search(compact)
        if m:
            dl = _normalize_in_dl(m)
            if dl:
                add(dl, score, "indian_dl")

        m = PASSPORT_RE.search(text)
        if m:
            tok = m.group(1).upper()
            # Avoid NO123456789 / DN0123456789 from label bleed
            if tok[:2] in ("NO", "DN", "ID"):
                pass
            else:
                add(tok, score, "passport")

        compact_line = _clean_id_token(text)
        m = IN_DL_RE.fullmatch(compact_line) or IN_DL_RE.search(compact_line)
        if m:
            dl = _normalize_in_dl(m)
            if dl:
                add(dl, score, "indian_dl")

    # Nearby label "IDNo" + separate line "123456789"
    label_scores = [s for t, s in lines if re.search(r"ID\s*No|Identity\s*No|Pengenalan", t, re.I)]
    if label_scores:
        for text, score in lines:
            if re.fullmatch(r"\d{6,14}", text.strip()):
                add(text.strip(), max(score, max(label_scores)), "labeled")

    # OCR sometimes glues label+number into one token: DNO123456789 / IDNO123456789
    for text, score in lines:
        cleaned = _clean_id_token(text)
        if cleaned.isdigit() and 6 <= len(cleaned) <= 14:
            if re.search(r"ID\s*No|DNO|IDNO|Identity", text, re.I) or _LABEL_PREFIX_RE.match(
                re.sub(r"[^A-Za-z0-9]", "", text)
            ):
                add(cleaned, score, "labeled")

    # Blob fallback (skip if blob is dominated by dates)
    if not _is_date_noise_text(blob):
        for raw, sc, hint in _find_mykad_candidates(blob, 0.55):
            add(raw, sc, hint)
    else:
        # Only dashed MyKad from blob even if other dates present
        for m in MY_IC_DASHED_RE.finditer(blob):
            raw = f"{m.group(1)}{m.group(2)}{m.group(3)}"
            if _looks_like_mykad(raw):
                add(raw, 0.7, "mykad")

    m = ID_NO_DIGITS_RE.search(blob)
    if m:
        add(m.group(1), 0.7, "labeled")
    m = LABELED_ID_RE.search(blob)
    if m:
        val = _clean_id_token(m.group(1))
        if val.isdigit() and len(val) >= 6:
            add(val, 0.65, "labeled")

    m = AADHAAR_RE.search(blob)
    if m and not DATE_TEXT_RE.search(blob):
        raw = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        if _looks_like_mykad(raw):
            add(raw, 0.55, "mykad")
        else:
            add(raw, 0.55, "aadhaar")

    m = IN_DL_RE.search(blob.replace(" ", "")) or IN_DL_RE.search(blob)
    if m:
        dl = _normalize_in_dl(m)
        if dl:
            add(dl, 0.6, "indian_dl")

    m = PASSPORT_RE.search(blob)
    if m:
        tok = m.group(1).upper()
        if tok[:2] not in ("NO", "DN", "ID"):
            add(tok, 0.5, "passport")

    # Last resort generic — never glue label prefixes or address/postcode junk
    if not candidates:
        # Driving licence / lesen with blank IC — do not invent from address
        blob_u = blob.upper()
        if any(k in blob_u for k in ("DRIVING LICENCE", "LESEN MEMANDU", "TEMPOH", "VALIDITY")):
            if not any(k in blob_u for k in ("MYKAD", "KAD PENGENALAN")):
                return None
        for text, score in lines:
            if _is_date_noise_text(text):
                continue
            for m in GENERIC_DOC_RE.finditer(text):
                tok = _clean_id_token(m.group(1))
                if len(tok) < 8:
                    continue
                # MY postcode (5 digits) + place name, e.g. 47150PUCHONG
                if re.match(r"^\d{5}[A-Z]{3,}$", tok):
                    continue
                # Digits then letters — usually address fragments, not document IDs
                if re.match(r"^\d+[A-Z]+$", tok):
                    continue
                if tok[:2] in ("NO", "DN") and tok[2:].isdigit():
                    tok = tok[2:]
                if len(tok) < 8:
                    continue
                if tok.isdigit():
                    continue
                if any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok):
                    add(tok, score * 0.8, "unknown")

    if not candidates:
        return None

    trust_bonus = {
        "mykad": 0.18,
        "aadhaar": 0.16,
        "indian_dl": 0.14,
        "passport": 0.08,
        "labeled": -0.04,
        "unknown": -0.12,
    }
    priority = {
        "mykad": 5,
        "aadhaar": 5,
        "indian_dl": 4,
        "passport": 3,
        "labeled": 2,
        "unknown": 1,
    }
    candidates.sort(
        key=lambda c: (
            c[1] + trust_bonus.get(c[2], 0.0),
            priority.get(c[2], 0),
            c[1],
        ),
        reverse=True,
    )
    return candidates[0]


def extract_vehicle_number(lines: List[Tuple[str, float]]) -> Optional[Tuple[str, float]]:
    """Returns (normalized_plate, confidence) or None."""
    if not lines:
        return None
    candidates = []
    for text, score in lines:
        if _is_date_noise_text(text):
            continue
        compact = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        m = IN_PLATE_RE.search(text)
        if m:
            plate = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}".upper()
            if len(plate) <= 11:
                candidates.append((plate, score))
            continue
        m2 = re.search(r"\b([A-Z]{2}\d{1,2}[A-Z]{1,3}\d{1,4})\b", compact)
        if m2 and len(m2.group(1)) <= 11:
            candidates.append((m2.group(1), score))
            continue
        m3 = MY_PLATE_RE.search(text)
        if m3:
            plate = f"{m3.group(1)}{m3.group(2)}{m3.group(3) or ''}".upper()
            if 5 <= len(plate) <= 11:
                candidates.append((plate, score * 0.9))

    if not candidates:
        blob = re.sub(r"[^A-Za-z0-9]", "", _join_lines(lines)).upper()
        m2 = re.search(r"([A-Z]{2}\d{1,2}[A-Z]{1,3}\d{1,4})", blob)
        if m2 and len(m2.group(1)) <= 11:
            candidates.append((m2.group(1), 0.5))

    if not candidates:
        return None
    candidates.sort(key=lambda c: c[1], reverse=True)
    return candidates[0]
