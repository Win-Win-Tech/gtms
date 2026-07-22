"""
Parse OCR lines into ID numbers / vehicle plate numbers.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

# Malaysian NRIC: YYMMDD-PB-###G (with or without dashes)
MY_IC_RE = re.compile(
    r"\b(\d{6})-?(\d{2})-?(\d{4})\b"
)

# Indian Aadhaar: 12 digits, often grouped 4-4-4
AADHAAR_RE = re.compile(
    r"\b(\d{4})\s*[-\s]?\s*(\d{4})\s*[-\s]?\s*(\d{4})\b"
)

# Indian Driving Licence: TN58Y20210000359 / TN-58-2021-0000359 / TN58 20210000359
# Longer than vehicle plates (plates are typically ≤10–11 chars).
IN_DL_RE = re.compile(
    r"\b([A-Z]{2})\s*-?\s*(\d{2})\s*-?\s*([A-Z]?)\s*-?\s*(\d{4,11})\b",
    re.IGNORECASE,
)

# Labelled ID / DL / document number on real phone captures
LABELED_ID_RE = re.compile(
    r"(?:DL\.?\s*No\.?|D\.?\s*L\.?\s*No\.?|Licence\s*No\.?|License\s*No\.?"
    r"|Document\s*(?:No\.?|Number)|Doc(?:ument)?\.?\s*No\.?"
    r"|ID\s*(?:No\.?|Number)|Personal\s*Code|Passport\s*No\.?)"
    r"\s*[:\-]?\s*([A-Z0-9][A-Z0-9\-/]{5,20})",
    re.IGNORECASE,
)

# Generic passport / national ID token (letters then digits), e.g. AS0003822
PASSPORT_RE = re.compile(
    r"\b([A-Z]{1,2}\d{6,9})\b",
    re.IGNORECASE,
)

# Mixed alphanumeric document id (8–18) — last-resort for phone captures
GENERIC_DOC_RE = re.compile(
    r"\b(?=[A-Z0-9]*[A-Z][A-Z0-9]*\d|\d[A-Z0-9]*[A-Z])([A-Z0-9]{8,18})\b",
    re.IGNORECASE,
)

# Indian plate examples: TN58B8050, TN 58 B 8050, MH12AB1234
IN_PLATE_RE = re.compile(
    r"\b([A-Z]{2})\s*[-\s]?\s*(\d{1,2})\s*[-\s]?\s*([A-Z]{1,3})\s*[-\s]?\s*(\d{1,4})\b",
    re.IGNORECASE,
)

# Malaysian plate-ish: ABC1234 / W1234A / etc.
MY_PLATE_RE = re.compile(
    r"\b([A-Z]{1,3})\s*[-\s]?\s*(\d{1,4})\s*[-\s]?\s*([A-Z]{0,3})\b",
    re.IGNORECASE,
)

# Words that are clearly not document numbers
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
}


def _join_lines(lines: List[Tuple[str, float]]) -> str:
    return " ".join(t for t, _ in lines)


def _normalize_in_dl(m: re.Match) -> Optional[str]:
    raw = f"{m.group(1)}{m.group(2)}{(m.group(3) or '')}{m.group(4)}".upper()
    # Vehicle plates like TN58B8050 are shorter; DL numbers are longer.
    if len(raw) < 13:
        return None
    return raw


def _clean_id_token(token: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", token).upper()


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
        # Reject pure names-ish (all letters, no digits) unless passport-short
        if raw.isalpha() and len(raw) > 9:
            return
        candidates.append((raw, score, hint))

    for text, score in lines:
        compact = text.replace(" ", "")

        m = MY_IC_RE.search(compact) or MY_IC_RE.search(text)
        if m:
            add(f"{m.group(1)}{m.group(2)}{m.group(3)}", score, "mykad")

        m = AADHAAR_RE.search(text)
        if m:
            add(f"{m.group(1)}{m.group(2)}{m.group(3)}", score, "aadhaar")

        m = IN_DL_RE.search(text) or IN_DL_RE.search(compact)
        if m:
            dl = _normalize_in_dl(m)
            if dl:
                add(dl, score, "indian_dl")

        m = LABELED_ID_RE.search(text)
        if m:
            add(m.group(1), score, "labeled")

        m = PASSPORT_RE.search(text)
        if m:
            add(m.group(1), score, "passport")

        # Whole OCR line is often just the DL number on phone photos
        compact_line = _clean_id_token(text)
        m = IN_DL_RE.fullmatch(compact_line) or IN_DL_RE.search(compact_line)
        if m:
            dl = _normalize_in_dl(m)
            if dl:
                add(dl, score, "indian_dl")

    # Full blob fallback
    m = MY_IC_RE.search(blob.replace(" ", ""))
    if m:
        add(f"{m.group(1)}{m.group(2)}{m.group(3)}", 0.55, "mykad")
    m = AADHAAR_RE.search(blob)
    if m:
        add(f"{m.group(1)}{m.group(2)}{m.group(3)}", 0.55, "aadhaar")
    m = IN_DL_RE.search(blob.replace(" ", "")) or IN_DL_RE.search(blob)
    if m:
        dl = _normalize_in_dl(m)
        if dl:
            add(dl, 0.6, "indian_dl")
    m = LABELED_ID_RE.search(blob)
    if m:
        add(m.group(1), 0.65, "labeled")
    m = PASSPORT_RE.search(blob)
    if m:
        add(m.group(1), 0.5, "passport")

    # Last resort: mixed alphanumeric token (helps messy phone OCR)
    if not candidates:
        for text, score in lines:
            for m in GENERIC_DOC_RE.finditer(text):
                tok = _clean_id_token(m.group(1))
                # Skip short plate-shaped tokens (e.g. TN58B8050)
                if len(tok) < 12:
                    continue
                if any(ch.isdigit() for ch in tok) and any(ch.isalpha() for ch in tok):
                    add(tok, score * 0.85, "unknown")

    if not candidates:
        return None

    # Highest confidence; prefer structured national IDs over loose tokens
    priority = {
        "mykad": 4,
        "aadhaar": 4,
        "indian_dl": 4,
        "labeled": 3,
        "passport": 2,
        "unknown": 1,
    }
    candidates.sort(key=lambda c: (c[1], priority.get(c[2], 0)), reverse=True)
    return candidates[0]


def extract_vehicle_number(lines: List[Tuple[str, float]]) -> Optional[Tuple[str, float]]:
    """Returns (normalized_plate, confidence) or None."""
    if not lines:
        return None
    candidates = []
    for text, score in lines:
        compact = re.sub(r"[^A-Za-z0-9]", "", text).upper()
        m = IN_PLATE_RE.search(text)
        if m:
            plate = f"{m.group(1)}{m.group(2)}{m.group(3)}{m.group(4)}".upper()
            # Skip if this looks like a long DL number, not a plate
            if len(plate) <= 11:
                candidates.append((plate, score))
            continue
        # Compact IN-style TN58B8050
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
