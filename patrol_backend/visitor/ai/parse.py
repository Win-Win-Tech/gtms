"""
Parse OCR lines into ID numbers / vehicle plate numbers / cardholder names.

Name extraction uses card-layout rules once the document family is known
(MyKad, Malaysian DL, Indian DL, Aadhaar, passport/EU ID, labeled badge).
RapidOCR lines must be in reading order (top→bottom) so label→value and
"above DOB / above Nationality" anchors work. A generic scorer remains as
fallback when the layout is unknown.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence, Set, Tuple

# =====================================================================
# ID NUMBER / VEHICLE PLATE EXTRACTION -- UNCHANGED (working correctly)
# =====================================================================

# Malaysian NRIC: YYMMDD-PB-###G (prefer dashed form from real MyKad prints)
MY_IC_DASHED_RE = re.compile(
    r"(?<!\d)(\d{6})\s*-\s*(\d{2})\s*-\s*(\d{4})(?!\d)"
)

# Loose (no dashes) -- only used with stronger validation + not from date text
MY_IC_LOOSE_RE = re.compile(
    r"(?<!\d)(\d{6})\s+(\d{2})\s+(\d{4})(?!\d)"
)

# OCR letter/digit confusions in dashed MyKad
MY_IC_OCR_DASHED_RE = re.compile(
    r"(?<![A-Z0-9])([0-9OILSBZ]{6})\s*-\s*([0-9OILSBZ]{2})\s*-\s*([0-9OILSBZ]{4})(?![A-Z0-9])",
    re.IGNORECASE,
)

# DD/MM/YYYY or date ranges -- never treat these as MyKad
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

# Label then value -- capture value only (not "ID No" letters)
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

# Mixed alphanumeric document id (8-18) -- last-resort
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
    """True if line is (or contains) calendar dates -- not an IC number."""
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

    for m in MY_IC_LOOSE_RE.finditer(text):
        raw = f"{m.group(1)}{m.group(2)}{m.group(3)}"
        if _looks_like_mykad(raw):
            out.append((raw, score * 0.92, "mykad"))

    digits = _ocr_to_digits(text)
    if len(digits) == 12 and _looks_like_mykad(digits):
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
        if len(raw) < 6:
            return
        if raw.isalpha() and len(raw) > 9:
            return
        if raw.isdigit() and len(raw) in (12, 16) and hint == "mykad":
            if not _looks_like_mykad(raw[:12] if len(raw) >= 12 else raw):
                return
        candidates.append((raw, score, hint))

    for text, score in lines:
        if _is_date_noise_text(text):
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

    label_scores = [s for t, s in lines if re.search(r"ID\s*No|Identity\s*No|Pengenalan", t, re.I)]
    if label_scores:
        for text, score in lines:
            if re.fullmatch(r"\d{6,14}", text.strip()):
                add(text.strip(), max(score, max(label_scores)), "labeled")

    for text, score in lines:
        cleaned = _clean_id_token(text)
        if cleaned.isdigit() and 6 <= len(cleaned) <= 14:
            if re.search(r"ID\s*No|DNO|IDNO|Identity", text, re.I) or _LABEL_PREFIX_RE.match(
                re.sub(r"[^A-Za-z0-9]", "", text)
            ):
                add(cleaned, score, "labeled")

    if not _is_date_noise_text(blob):
        for raw, sc, hint in _find_mykad_candidates(blob, 0.55):
            add(raw, sc, hint)
    else:
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

    if not candidates:
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
                if re.match(r"^\d{5}[A-Z]{3,}$", tok):
                    continue
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
    """
    Returns (normalized_plate, confidence) or None.

    Country-agnostic: accepts common letter+digit plate patterns worldwide.
    Prefers longer, higher-confidence candidates (avoids truncated OCR).
    """
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
            if 5 <= len(plate) <= 12:
                candidates.append((plate, float(score)))
        m2 = re.search(r"([A-Z]{2}\d{1,2}[A-Z]{1,3}\d{1,4})", compact)
        if m2 and 5 <= len(m2.group(1)) <= 12:
            candidates.append((m2.group(1), float(score)))
        m3 = MY_PLATE_RE.search(text)
        if m3:
            plate = f"{m3.group(1)}{m3.group(2)}{m3.group(3) or ''}".upper()
            if 5 <= len(plate) <= 12:
                candidates.append((plate, float(score) * 0.9))
        # Generic worldwide: mixed alnum token (no national layout required)
        if 5 <= len(compact) <= 12:
            has_a = any(c.isalpha() for c in compact)
            has_d = any(c.isdigit() for c in compact)
            if has_a and has_d:
                candidates.append((compact, float(score) * 0.85))

    if not candidates:
        blob = re.sub(r"[^A-Za-z0-9]", "", _join_lines(lines)).upper()
        # Prefer longest mixed alnum run in the blob
        for m in re.finditer(r"[A-Z0-9]{5,12}", blob):
            plate = m.group(0)
            if any(c.isalpha() for c in plate) and any(c.isdigit() for c in plate):
                candidates.append((plate, 0.5))

    if not candidates:
        return None
    # Prefer confidence, then length (truncated OCR loses)
    candidates.sort(key=lambda c: (c[1], len(c[0])), reverse=True)
    return candidates[0]


# =====================================================================
# NAME EXTRACTION -- card-layout aware (production)
# =====================================================================
#
# Layouts we support (from real sample cards):
#   mykad      — name below IC / chip, above address (no "Name" label)
#   my_dl      — name below MALAYSIA header, above Warganegara/Nationality
#   indian_dl  — "Name" label → value; ignore Son/Daughter/Wife of
#   aadhaar    — English name immediately above DOB line
#   passport   — SURNAME + GIVEN NAME labels (EU / travel docs)
#   labeled    — "Name :" inline (badges)
#   generic    — last-resort scorer
#
# RapidOCR lines MUST be in reading order (top→bottom). Confidence order
# breaks every label→next-line and "above DOB" rule.

_NAME_LABEL_RE = re.compile(
    r"(?:FULL\s*NAME|HOLDER'?S?\s*NAME|NAME\s*OF\s*HOLDER|CARDHOLDER\s*NAME"
    r"|GIVEN\s*NAME[S]?|FIRST\s*NAME|SURNAME|FAMILY\s*NAME|LAST\s*NAME"
    r"|NAME|NAMA|NOM(?:\s*DE\s*FAMILLE)?|PRENOM|NAAM|ISM"
    r"|EESNIMI|PEREKONNANIMI)"
    r"\s*[:\-]?\s*([A-Za-z][A-Za-z\s\.\'\-]{1,45})",
    re.IGNORECASE,
)

# Glued bilingual labels (OCR often drops spaces): PEREKONNANIMI/SURNAME
_GLUED_SURNAME_LABEL_RE = re.compile(
    r"(?:PEREKONNANIMI|FAMILYNAME|LASTNAME).*?(?:SURNAME|FAMILY)|"
    r"(?:SURNAME|FAMILYNAME).*(?:PEREKONNANIMI)|"
    r"^SURNAME$|^FAMILY\s*NAME$|^LAST\s*NAME$|^PEREKONNANIMI$",
    re.IGNORECASE,
)
_GLUED_GIVEN_LABEL_RE = re.compile(
    r"(?:EESNIMI|GIVENNAME|FIRSTNAME).*?(?:GIVEN|FIRST|NAME)|"
    r"(?:GIVEN\s*NAMES?|FIRST\s*NAME).*(?:EESNIMI)|"
    r"^GIVEN\s*NAMES?$|^FIRST\s*NAME$|^EESNIMI$|^PRENOM$",
    re.IGNORECASE,
)
_GLUED_FULL_NAME_LABEL_RE = re.compile(
    r"^(?:FULL\s*NAME|NAME|NAMA|HOLDER'?S?\s*NAME|CARDHOLDER\s*NAME)$",
    re.IGNORECASE,
)

_LABEL_WORD_VOCAB = {
    "FULL", "HOLDER", "HOLDERS", "OF", "CARDHOLDER", "GIVEN", "FIRST",
    "SUR", "FAMILY", "LAST", "NAME", "NAMES", "NAMA", "NOM", "PRENOM",
    "NAAM", "ISM", "EESNIMI", "PEREKONNANIMI", "SURNAME",
}

_RELATION_MARKER_RE = re.compile(
    r"(?:SON|DAUGHTER|WIFE)\s*/?\s*(?:OF)?|S/O|D/O|W/O",
    re.IGNORECASE,
)

_GENERIC_NON_NAME_WORDS = {
    "NAME", "NAMES", "NAMA", "NOM", "PRENOM", "NAAM", "ISM",
    "SURNAME", "GIVEN", "FULL", "HOLDER", "HOLDERS", "CARDHOLDER",
    "FAMILY", "FIRST", "LAST", "SUR", "EESNIMI", "PEREKONNANIMI",
    "DATE", "BIRTH", "DOB", "EXPIRY", "VALID", "VALIDITY", "ISSUE", "ISSUED",
    "ADDRESS", "ALAMAT", "SIGNATURE", "SPECIMEN", "CONTOH",
    "AUTHORITY", "GOVERNMENT", "GOVT", "REPUBLIC", "MINISTRY", "DEPARTMENT",
    "STATE", "DISTRICT", "COUNTRY", "UNION", "TERRITORY", "INDIA",
    "LICENCE", "LICENSE", "DRIVING", "PASSPORT", "IDENTITY", "CARD", "NUMBER",
    "GENDER", "SEX", "MALE", "FEMALE", "NATIONALITY", "CITIZEN", "CITIZENSHIP",
    "BLOOD", "GROUP", "CLASS", "PERSONAL", "CODE", "TEMPOH", "KELAS",
    "ROAD", "STREET", "JALAN", "LORONG", "PERSIARAN", "LANE", "AVENUE",
    "BLOCK", "BLOK", "FLOOR", "TINGKAT", "TOWER", "APT", "LOT", "NO",
    "TAMAN", "KAMPUNG", "DESA", "PUSAT", "POSTCODE", "PIN",
    "KAD", "PENGENALAN", "WARGANEGARA", "MYKAD", "LELAKI", "PEREMPUAN", "ISLAM",
    "LESEN", "MEMANDU", "MALAYSIA", "EESTI", "VABARIIK", "ISIKUTUNNISTUS",
    "GOVERNMENTOFINDIA", "GOVERNMENTGFINDIA", "UNIQUE", "IDENTIFICATION",
    "AUTHORITY", "ENROLMENT", "VID", "SALE", "WWW", "MOBILE",
    "WARGANEGARA", "NATIONALITY", "PENGENALAN", "IDENTITY",
    "GAMBAR", "SEKADAR", "HIASAN", "SEKADARHIASAN",
}

_ADDRESS_HINT_RE = re.compile(
    r"\b(?:ROAD|STREET|JALAN|LORONG|PERSIARAN|LANE|AVENUE|BLOCK|BLOK|FLOOR"
    r"|TINGKAT|TOWER|APT|LOT|TAMAN|KAMPUNG|DESA|DISTRICT|PANGSAPURI"
    r"|ALAMAT|ADDRESS|POSTCODE|PIN\s*CODE|C/?O)\b",
    re.IGNORECASE,
)

_MY_ADDRESS_START_RE = re.compile(
    r"^(?:NO\.?\s*\d|\d{1,5}\s+[A-Z]|LOT\s*\d|BLOK|BLOCK|JALAN|LORONG|"
    r"PERSIARAN|TAMAN|KAMPUNG|DESA|PANGSAPURI|PUSAT|BT\.?\s*\d)",
    re.IGNORECASE,
)

_MY_STATE_RE = re.compile(
    r"\b(?:JOHOR|KEDAH|KELANTAN|MELAKA|MALACCA|NEGERI\s*SEMBILAN|PAHANG|"
    r"PERAK|PERLIS|PULAU\s*PINANG|PENANG|SABAH|SARAWAK|SELANGOR|"
    r"TERENGGANU|KUALA\s*LUMPUR|PUTRAJAYA|LABUAN|WP)\b",
    re.IGNORECASE,
)

_DOB_LINE_RE = re.compile(
    r"(?:DOB|DATE\s*OF\s*BIRTH|JNM\s*TITHI|JANM|जन्म|/DOB)",
    re.IGNORECASE,
)

_MY_DL_HEADER_RE = re.compile(
    r"LESEN\s*MEMANDU|DRIVING\s*LICENCE",
    re.IGNORECASE,
)

_MY_DL_ANCHOR_RE = re.compile(
    r"Warganegara|Narganegara|Nationality|Pengenalan|Identity\s*No|Kelas|Class|"
    r"Tempoh|Validity|Alamat|Alemat|Address|Addret",
    re.IGNORECASE,
)

_AADHAAR_HEADER_RE = re.compile(
    r"GOVERNMENT\s*OF\s*INDIA|UNIQUE\s*IDENTIFICATION|AADHAAR|UIDAI|"
    r"GOVERNMENTOFINDIA|GOVERNMENTGFINDIA",
    re.IGNORECASE,
)

_MYKAD_HEADER_RE = re.compile(
    r"KAD\s*PENGENALAN|MYKAD|IDENTITY\s*CA?RD|IDENTITYCAED|DENTIT",
    re.IGNORECASE,
)

_BLOOD_GROUP_RE = re.compile(
    r"^(?:AB|A|B|O)\s*[+\-]?$|BLOOD\s*GROU",
    re.IGNORECASE,
)

_SEX_TOKEN_RE = re.compile(r"^(?:M|F|M\s*/\s*M|F\s*/\s*F|MALE|FEMALE)$", re.IGNORECASE)

_MALAY_PATRONYMIC_RE = re.compile(
    r"\b(?:BIN|BINTI|A/L|A/P|AL|AP)\b",
    re.IGNORECASE,
)


def _is_label_only_line(text: str) -> bool:
    stripped = re.sub(r"[/:\-]+", " ", text).strip().upper()
    if not stripped:
        return False
    # Glued bilingual labels without spaces
    compact = re.sub(r"[^A-Z]", "", stripped)
    if compact in {
        "PEREKONNANIMISURNAME", "SURNAMEPEREKONNANIMI", "EESNIMIGIVENNAME",
        "GIVENNAMEEESNIMI", "GIVENNAMES", "FULLNAME", "CARDHOLDERNAME",
        "HOLDERNAME", "HOLDERSNAME",
    }:
        return True
    if _GLUED_SURNAME_LABEL_RE.fullmatch(stripped.replace(" ", "")):
        return True
    if _GLUED_GIVEN_LABEL_RE.fullmatch(stripped.replace(" ", "")):
        return True
    tokens = stripped.split()
    if not tokens or len(tokens) > 4:
        return False
    return all(t in _LABEL_WORD_VOCAB for t in tokens)


def _clean_name_token(text: str) -> str:
    """Keep letters and single spaces; recover common OCR glues."""
    s = text.strip()
    # TitleCase glue: FirdosAlam → Firdos Alam
    s = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", s)
    # Malay patronymic glue — require a real next name token (2+ letters),
    # so BINA / BINTANG are not split into BIN A / BIN TANG.
    s = re.sub(r"(?i)([A-Za-z])(BINTI|BIN)(?=[A-Za-z]{2,})", r"\1 \2 ", s)
    s = re.sub(r"[^A-Za-z\s]", " ", s)
    s = re.sub(r"\s+", " ", s).strip().upper()
    return s


def _prettify_name(cleaned: str) -> str:
    """Title-case for display, keep BIN/BINTI upper."""
    words = []
    for w in cleaned.split():
        if w in {"BIN", "BINTI", "A/L", "A/P", "AL", "AP"}:
            words.append(w)
        else:
            words.append(w.title())
    return " ".join(words)


def _alpha_ratio(text: str) -> float:
    chars = [c for c in text if not c.isspace()]
    if not chars:
        return 0.0
    return sum(1 for c in chars if c.isalpha()) / len(chars)


def _is_structurally_valid_name(cleaned: str) -> bool:
    if not cleaned:
        return False
    words = cleaned.split()
    if not (1 <= len(words) <= 12):
        return False
    if not (3 <= len(cleaned) <= 90):
        return False
    # Reject short junk tokens (HRR, NOI, AK)
    if len(words) == 1 and len(words[0]) < 4:
        return False
    if _BLOOD_GROUP_RE.match(cleaned):
        return False
    return True


def _has_noise_word(cleaned: str) -> bool:
    words = cleaned.split()
    if any(w in _GENERIC_NON_NAME_WORDS for w in words):
        return True
    compact = re.sub(r"[^A-Z]", "", cleaned.upper())
    if compact in _GENERIC_NON_NAME_WORDS:
        return True
    for noise in (
        "LESENMEMANDU", "DRIVINGLICENCE", "KADPENGENALAN", "IDENTITYCARD",
        "GOVERNMENTOFINDIA", "WARGANEGARA", "GAMBARSEKADARHIASAN",
        "MALAYSIA", "MALAYSLA", "TYCARD", "MCARD", "NARGANEGARA",
        "HELLEHLPT", "ABCDEFGHI",
    ):
        if compact == noise or compact.startswith(noise):
            return True
    return False


# Structural Malay / Malaysian name particles (longest-first greedy split).
# Not a person gazetteer — only connectors + very common given-name stems.
_MY_NAME_PARTICLES: Tuple[str, ...] = (
    "MUHAMMAD", "MUHAMMED", "MOHAMMAD", "MOHAMMED", "MUHAMAD", "MOHAMAD",
    "MOHAMED", "ABDULLAH", "ABDUL",
    "TENGKU", "SHARIFAH", "PUTERI", "PUTRA",
    "BINTI", "BIN",
    "AHMAD", "AHMED", "MOHD", "SYED", "SITI", "NOOR", "NOR", "NUR",
    "WAN", "NIK", "CHE", "DATO", "DATUK", "TUN", "TAN", "LIM", "LEE",
    "ONG", "YAP", "TEO", "GOH", "KOH", "NG", "BUDI", "AMIR", "AIMAN",
    "ABD", "MD",
)


def _looks_like_ocr_junk_name(text: str) -> bool:
    """Reject background / Hindi-misread OCR garbage (e.g. hellehlpt)."""
    t = text.strip()
    if not t:
        return True
    letters = re.sub(r"[^A-Za-z]", "", t)
    if len(letters) < 4:
        return True
    # All-lowercase Latin blobs are almost never printed ID names
    if letters.islower() and len(letters) >= 5:
        return True
    # Very low vowel ratio → random consonant soup
    vowels = sum(1 for c in letters.lower() if c in "aeiou")
    if len(letters) >= 6 and vowels / len(letters) < 0.18:
        return True
    return False


def _split_glued_malay_tokens(text: str) -> str:
    """
    Split OCR-glued Malaysian names using known particles:
    BUDIUTOMO → BUDI UTOMO
    NURMOHDFAZARIBINMOHDSHANJI → NUR MOHD FAZARI BIN MOHD SHANJI
    TENGKUMUHAMMADSYAHZANBIN → TENGKU MUHAMMAD SYAHZAN BIN
    """
    stripped = text.strip()
    # Already well spaced (2+ words, none longer than ~14) — leave alone
    words = stripped.split()
    if len(words) >= 2 and all(len(w) <= 14 for w in words):
        # Still ensure BIN/BINTI spacing is clean
        return re.sub(r"\s+", " ", stripped).strip()

    compact = re.sub(r"[^A-Za-z]", "", stripped).upper()
    if len(compact) < 6:
        return stripped

    particles = sorted(_MY_NAME_PARTICLES, key=len, reverse=True)
    out: List[str] = []
    i = 0
    unknown: List[str] = []

    def flush_unknown() -> None:
        nonlocal unknown
        if unknown:
            out.append("".join(unknown))
            unknown = []

    while i < len(compact):
        matched = None
        for tok in particles:
            if not compact.startswith(tok, i):
                continue
            end = i + len(tok)
            # Don't treat BINA as BIN+A (BIN must be end or followed by 2+ letters)
            if tok in {"BIN", "BINTI"} and end < len(compact) and end + 1 == len(compact):
                continue
            if tok in {"BIN", "BINTI"} and end < len(compact) and not (len(compact) - end >= 2 or end == len(compact)):
                continue
            # Avoid matching short particles inside unknown stems too early when
            # a longer particle also fits — already handled by longest-first.
            matched = tok
            break
        if matched:
            flush_unknown()
            out.append(matched)
            i += len(matched)
        else:
            unknown.append(compact[i])
            i += 1
    flush_unknown()
    return " ".join(out) if out else stripped


def _soft_split_glued_malay_name(text: str) -> str:
    """Insert spaces into glued Malaysian / Malay names from OCR."""
    stripped = text.strip()
    if not stripped:
        return stripped
    # CamelCase / TitleCase English (Aadhaar): FirdosAlam → Firdos Alam
    if re.search(r"[a-z]", stripped) and re.search(r"[A-Z]", stripped):
        return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stripped).strip()
    # Preserve already-spaced non-Malay names (e.g. HUGO MARTIN)
    if " " in stripped and not re.search(r"(?i)BIN|BINTI|MOHD|MUHAMMAD|TENGKU|NUR", stripped):
        if all(len(w) <= 14 for w in stripped.split()):
            return stripped
    return _split_glued_malay_tokens(stripped)


def _format_id_name(text: str) -> str:
    """Soft-split Malay glues then prettify for API response."""
    spaced = _soft_split_glued_malay_name(text)
    cleaned = _clean_name_token(spaced)
    # Re-run particle split if clean collapsed spaces on a long glue
    if len(cleaned.split()) == 1 and len(cleaned) >= 10:
        cleaned = _clean_name_token(_split_glued_malay_tokens(cleaned))
    if not cleaned:
        return ""
    return _prettify_name(cleaned)


def _looks_like_address_line(text: str) -> bool:
    if _ADDRESS_HINT_RE.search(text) or _MY_ADDRESS_START_RE.search(text.strip()):
        return True
    if _MY_STATE_RE.search(text) and re.search(r"\d{5}", text):
        return True
    if re.match(r"^\d{5}\b", text.strip()):
        return True
    return False


def _looks_like_header_or_label(text: str) -> bool:
    u = text.upper().replace(" ", "")
    if _MY_DL_HEADER_RE.search(text) or _MYKAD_HEADER_RE.search(text):
        return True
    if _AADHAAR_HEADER_RE.search(text):
        return True
    if _MY_DL_ANCHOR_RE.search(text):
        return True
    if _is_label_only_line(text):
        return True
    if u in {
        "MALAYSIA", "MALAYSLA", "WARGANEGARA", "LELAKI", "PEREMPUAN", "ISLAM",
        "MYKAD", "IDENTITYCARD", "KADPENGENALAN", "DRIVINGLICENCE",
        "LESENMEMANDU", "GOVERNMENTOFINDIA", "SPECIMEN", "CONTOH",
        "TYCARD", "MCARD", "NARGANEGARA",
    }:
        return True
    return False


def _candidate_ok(text: str) -> Optional[str]:
    if _looks_like_address_line(text) or _looks_like_header_or_label(text):
        return None
    if _is_date_noise_text(text) or _BLOOD_GROUP_RE.search(text.strip()):
        return None
    if _looks_like_ocr_junk_name(text):
        return None
    if _SEX_TOKEN_RE.match(re.sub(r"[\s/]", "", text.strip())):
        return None
    if re.fullmatch(r"M\s*/\s*M|F\s*/\s*F", text.strip(), re.I):
        return None
    if re.search(r"\d{5,}", text):  # long digit runs (IDs / postcodes)
        return None
    # Soft-split glued Malay names before validation
    candidate = _clean_name_token(_soft_split_glued_malay_name(text))
    if not candidate:
        candidate = _clean_name_token(text)
    if not _is_structurally_valid_name(candidate):
        return None
    if _has_noise_word(candidate):
        return None
    # Reject sex codes / spaced single letters ("M M", "A B C D")
    parts = candidate.split()
    if parts and all(len(p) == 1 for p in parts):
        return None
    return candidate


def _aadhaar_name_score(raw: str, cand: str, conf: float) -> float:
    """Prefer real English names over Hindi-misread / background junk."""
    score = conf
    # CamelCase / TitleCase from Latin OCR (FirdosAlam, Basant Raj)
    if re.search(r"[a-z][A-Z]", raw) or (raw[:1].isupper() and any(c.islower() for c in raw)):
        score += 0.45
    if len(cand.split()) >= 2:
        score += 0.35
    elif len(cand) >= 8:
        score += 0.10
    # Penalize all-caps single tokens that look like OCR soup
    if raw.isupper() and " " not in raw and len(raw) <= 10:
        score -= 0.15
    if raw.islower():
        score -= 0.80
    return score


def _extract_name_aadhaar(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    """
    Aadhaar (including mini / rotated): English name is Latin text near DOB.
    Hindi / other-script lines are ignored; OCR junk like 'hellehlpt' is scored down.
    """
    raw = [t for t, _ in lines]
    scored: List[Tuple[float, str]] = []

    def consider(text: str, conf: float) -> None:
        if _AADHAAR_HEADER_RE.search(text) or _looks_like_address_line(text):
            return
        if _DOB_LINE_RE.search(text) or re.search(r"/?\s*MALE|/?\s*FEMALE", text, re.I):
            return
        digits_only = re.sub(r"\D", "", text)
        if len(digits_only) >= 8:
            return
        # Keep Latin letters only — drop Devanagari / CJK OCR noise
        latin = re.sub(r"[^A-Za-z\s]", "", text).strip()
        if len(latin) < 4:
            return
        if _looks_like_ocr_junk_name(latin):
            return
        cand = _candidate_ok(latin)
        if not cand or len(cand) < 4:
            return
        scored.append((_aadhaar_name_score(latin, cand, conf), cand))

    # 1) Lines near DOB (above and slightly below — mini cards / rotation)
    for i, text in enumerate(raw):
        if not _DOB_LINE_RE.search(text):
            continue
        for j in range(max(0, i - 8), min(len(raw), i + 3)):
            if j == i:
                continue
            conf = lines[j][1] if j < len(lines) else 0.5
            consider(raw[j], conf)

    # 2) Letter form: after "To"
    for i, text in enumerate(raw):
        if re.fullmatch(r"To|TO", text.strip()):
            for j in range(i + 1, min(i + 5, len(raw))):
                consider(raw[j], lines[j][1])

    # 3) Score every Latin name-like line (mini cards / noisy OCR order)
    for text, conf in lines:
        consider(text, conf)

    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    return scored[0][1]


def _merge_my_name_lines(parts: List[str]) -> Optional[str]:
    if not parts:
        return None
    # Soft-split each part then join
    spaced_parts = [_soft_split_glued_malay_name(p) for p in parts]
    merged = _clean_name_token(" ".join(spaced_parts))
    if len(merged.split()) == 1 and len(merged) >= 10:
        merged = _clean_name_token(_split_glued_malay_tokens(merged))
    if not _is_structurally_valid_name(merged) or _has_noise_word(merged):
        merged = _clean_name_token(_soft_split_glued_malay_name(parts[0]))
        if not _is_structurally_valid_name(merged) or _has_noise_word(merged):
            return None
    return merged


def _extract_name_my_dl(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    """
    Malaysian driving licence: large name below header / above Nationality.
    Long names often wrap to 2 lines (... BIN / father name). OCR may also
    scramble reading order on rotated photos — collect all name-like lines.
    """
    raw = [t for t, _ in lines]
    confs = [c for _, c in lines]

    def is_nameish(text: str) -> Optional[str]:
        if re.fullmatch(r"\s*MALAYSIA\s*", text, re.I):
            return None
        if _looks_like_header_or_label(text) or _looks_like_address_line(text):
            return None
        if re.search(r"(?i)kelas|keler|closs|cle\b|tempoh|alamat|addret|wargan|nargan|abcdef", text):
            return None
        if _MY_STATE_RE.search(text) or re.search(r"\d{5}", text):
            return None
        # Address stems glued without spaces
        compact = re.sub(r"[^A-Za-z]", "", text).upper()
        if any(k in compact for k in (
            "JALAN", "LORONG", "TAMAN", "PANGSAPURI", "BLOK", "PUSAT",
            "KUALA", "TERENGGANU", "SELANGOR", "LANGAT", "PETALING",
        )):
            return None
        return _candidate_ok(text)

    # Gather all name-like lines (rotated cards put name after Nationality in OCR order)
    found: List[Tuple[int, str, float]] = []
    for i, text in enumerate(raw):
        cand = is_nameish(text)
        if cand:
            found.append((i, cand, confs[i] if i < len(confs) else 0.5))

    if not found:
        return None

    def rank(item: Tuple[int, str, float]) -> Tuple[int, int, float]:
        _, c, conf = item
        return (
            1 if _MALAY_PATRONYMIC_RE.search(c) else 0,
            len(re.sub(r"\s+", "", c)),
            conf,
        )

    found.sort(key=rank, reverse=True)
    best_idx, best, _ = found[0]

    # Pair a "... BIN" line with the other Tengku/Muhammad name line
    bin_lines = [(i, c) for i, c, _ in found if c.rstrip().endswith("BIN") or c.rstrip().endswith("BINTI")]
    other_lines = [(i, c) for i, c, _ in found if not (c.rstrip().endswith("BIN") or c.rstrip().endswith("BINTI"))]

    if bin_lines:
        bi, bc = max(bin_lines, key=lambda t: len(t[1]))
        # Prefer another long Malay name line (often the line after BIN on card)
        partner = None
        for i, c in other_lines:
            if abs(i - bi) <= 3 or (
                re.search(r"TENGKU|MUHAMMAD|MOHD|SYED|WAN", c)
                and len(re.sub(r"\s+", "", c)) >= 8
            ):
                partner = c
                break
        if partner is None and other_lines:
            # nearest other candidate by index
            partner = min(other_lines, key=lambda t: abs(t[0] - bi))[1]
        if partner and partner != bc:
            merged = _merge_my_name_lines([bc, partner])
            if merged and len(merged) > len(bc):
                return merged
        return bc

    # Single-line glued name (BUDIUTOMO / NURMOHD...)
    return best


def detect_id_card_type(lines: Sequence[Tuple[str, float]], document_hint: str = "") -> str:
    """Infer card family from OCR text + ID-number hint."""
    blob = " ".join(t for t, _ in lines).upper()
    compact = re.sub(r"\s+", "", blob)
    hint = (document_hint or "").lower().strip()

    if "LESENMEMANDU" in compact or "DRIVINGLICENCE" in compact:
        if "TAMILNADU" in compact or "UNIONOFINDIA" in compact or "DL.NO" in compact or "DLNO" in compact:
            return "indian_dl"
        return "my_dl"
    if "SON/DAUGHTER/WIFE" in compact or "SONDAUGHTERWIFEOF" in compact:
        return "indian_dl"
    if hint == "indian_dl":
        return "indian_dl"
    if "KADPENGENALAN" in compact or "MYKAD" in compact:
        return "mykad"
    if hint == "mykad":
        return "mykad"
    if (
        "AADHAAR" in compact
        or "UIDAI" in compact
        or "UNIQUEIDENTIFICATION" in compact
        or "GOVERNMENTOFINDIA" in compact
        or "GOVERNMENTGFINDIA" in compact
        or hint == "aadhaar"
    ):
        # Mini Aadhaar / letter: DOB or gender markers, or 12-digit hint
        if re.search(r"DOB|DATEOFBIRTH|/MALE|/FEMALE", compact) or hint == "aadhaar":
            return "aadhaar"
    if (
        "PEREKONNANIMI" in compact
        or "EESNIMI" in compact
        or "GIVENNAME" in compact
        or "SURNAME" in compact
        or "PASSPORT" in compact
    ):
        return "passport"
    if hint == "passport":
        return "passport"
    if any(re.search(r"^\s*Name\s*:", t, re.I) for t, _ in lines):
        return "labeled"
    return hint or "unknown"


def _name_after_label(
    lines: Sequence[Tuple[str, float]],
    label_pred,
    stop_pred=None,
    max_lookahead: int = 3,
) -> Optional[str]:
    """Return first valid name after a label line matching label_pred."""
    raw = [t for t, _ in lines]
    for i, text in enumerate(raw):
        if not label_pred(text):
            continue
        # Inline value on same line?
        m = _NAME_LABEL_RE.search(text)
        if m:
            cand = _candidate_ok(m.group(1))
            if cand:
                return cand
        for j in range(i + 1, min(i + 1 + max_lookahead, len(raw))):
            if stop_pred and stop_pred(raw[j]):
                break
            if label_pred(raw[j]):
                continue
            cand = _candidate_ok(raw[j])
            if cand:
                return cand
    return None


def _extract_name_indian_dl(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    def is_name_label(t: str) -> bool:
        u = re.sub(r"[^A-Za-z]", "", t).upper()
        return u in {"NAME", "NAMA"} or bool(re.fullmatch(r"Name\s*:?", t.strip(), re.I))

    def is_relation(t: str) -> bool:
        return bool(_RELATION_MARKER_RE.search(t))

    # Look ahead past dates / blood group sitting between label and value
    name = _name_after_label(lines, is_name_label, stop_pred=is_relation, max_lookahead=5)
    if name:
        return name
    # Fallback: line before relation marker
    raw = [t for t, _ in lines]
    for i, text in enumerate(raw):
        if not is_relation(text):
            continue
        for j in range(i - 1, max(-1, i - 4), -1):
            cand = _candidate_ok(raw[j])
            if cand and not is_name_label(raw[j]):
                return cand
    return None


def _extract_name_labeled_badge(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    for text, _ in lines:
        m = re.search(r"Name\s*:\s*([A-Za-z][A-Za-z\s\.\'\-]{1,45})", text, re.I)
        if m:
            cand = _candidate_ok(m.group(1))
            if cand:
                return cand
    return _name_after_label(
        lines,
        lambda t: bool(re.match(r"^\s*Name\s*:?\s*$", t, re.I)) or _is_label_only_line(t),
    )


def _extract_name_passport(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    raw = [t for t, _ in lines]
    surname = given = None

    def kind(text: str) -> Optional[str]:
        compact = re.sub(r"[^A-Za-z]", "", text).upper()
        if _GLUED_SURNAME_LABEL_RE.search(text) or compact in {
            "PEREKONNANIMISURNAME", "SURNAMEPEREKONNANIMI", "SURNAME", "PEREKONNANIMI",
            "FAMILYNAME", "LASTNAME",
        }:
            return "surname"
        if _GLUED_GIVEN_LABEL_RE.search(text) or compact in {
            "EESNIMIGIVENNAME", "GIVENNAMEEESNIMI", "GIVENNAME", "GIVENNAMES",
            "EESNIMI", "FIRSTNAME", "PRENOM",
        }:
            return "given"
        if _GLUED_FULL_NAME_LABEL_RE.match(text.strip()):
            return "full"
        return None

    for i, text in enumerate(raw):
        k = kind(text)
        if not k:
            # Inline "SURNAME: X"
            m = _NAME_LABEL_RE.search(text)
            if m:
                kk = "surname" if re.search(r"SURNAME|FAMILY|LAST|PEREKONNANIMI", text, re.I) else (
                    "given" if re.search(r"GIVEN|FIRST|EESNIMI|PRENOM", text, re.I) else "full"
                )
                cand = _candidate_ok(m.group(1))
                if cand:
                    if kk == "surname" and not surname:
                        surname = cand
                    elif kk == "given" and not given:
                        given = cand
                    elif kk == "full":
                        return cand
            continue
        for j in range(i + 1, min(i + 3, len(raw))):
            if kind(raw[j]):
                continue
            cand = _candidate_ok(raw[j])
            if not cand:
                continue
            if k == "surname" and not surname:
                surname = cand
            elif k == "given" and not given:
                given = cand
            elif k == "full":
                return cand
            break

    if given and surname:
        return f"{given} {surname}"
    return given or surname


def _extract_name_mykad(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    """
    MyKad: name is the text block below the IC number / chip area and
    above the address (NO / JALAN / postcode / state).
    """
    raw = [t for t, _ in lines]
    id_idx = None
    for i, text in enumerate(raw):
        digits = re.sub(r"\D", "", text)
        if len(digits) == 12 and _looks_like_mykad(digits):
            id_idx = i
            break
        if MY_IC_DASHED_RE.search(text):
            id_idx = i
            break

    start = (id_idx + 1) if id_idx is not None else 0
    # Also skip pure header lines at top
    while start < len(raw) and _looks_like_header_or_label(raw[start]):
        start += 1

    name_parts: List[str] = []
    for i in range(start, len(raw)):
        text = raw[i]
        if _looks_like_address_line(text) or _MY_STATE_RE.search(text):
            if name_parts:
                break
            continue
        if _looks_like_header_or_label(text):
            if name_parts:
                break
            continue
        if re.search(r"\d{5,}", text) and not _MALAY_PATRONYMIC_RE.search(text):
            if name_parts:
                break
            continue
        # Address-ish OCR without clear keywords (tower / jalan glued)
        compact = re.sub(r"[^A-Za-z]", "", text).upper()
        if any(k in compact for k in ("TOWER", "JALAN", "LORONG", "TAMAN", "PANGSAPURI", "MCMC")):
            if name_parts:
                break
            continue
        cand = _candidate_ok(text)
        if cand:
            name_parts.append(cand)
            joined = " ".join(name_parts)
            # Stop once we have a complete Malay name or 2 solid lines
            if _MALAY_PATRONYMIC_RE.search(joined) and len(joined.split()) >= 3:
                # allow one more line for father surname after BIN
                if len(name_parts) >= 2:
                    break
                continue
            if len(name_parts) >= 2:
                break
            if len(joined) > 45:
                break
        elif name_parts:
            break

    merged = _merge_my_name_lines(name_parts)
    if merged:
        return merged

    # Fallback without IC anchor: first non-header name-like block before address
    parts = []
    for text in raw:
        if _looks_like_header_or_label(text):
            continue
        if _looks_like_address_line(text) or _MY_STATE_RE.search(text):
            if parts:
                break
            continue
        cand = _candidate_ok(text)
        if cand:
            parts.append(cand)
            if len(parts) >= 2:
                break
    return _merge_my_name_lines(parts)


def _name_line_score(
    raw_text: str,
    ocr_confidence: float,
    line_index: int,
    near_label: bool,
    near_id_number: bool,
    after_id_number: Optional[bool],
) -> float:
    cleaned = _clean_name_token(raw_text)
    if not _is_structurally_valid_name(cleaned):
        return -1.0
    if _looks_like_header_or_label(raw_text) or _looks_like_address_line(raw_text):
        return -1.0
    if _has_noise_word(cleaned):
        return -1.0

    words = cleaned.split()
    substantive = [w for w in words if len(w) >= 2]
    score = 0.0
    score += 0.35 * _alpha_ratio(raw_text)
    if 2 <= len(substantive) <= 5:
        score += 0.25
    elif len(substantive) == 1:
        score += 0.05
    if raw_text.strip().isupper():
        score += 0.08
    elif raw_text.strip().istitle():
        score += 0.10
    if any(ch.isdigit() for ch in raw_text):
        score -= 0.6
    if near_label:
        score += 0.35
    if near_id_number:
        score += 0.15
    if after_id_number is True:
        score += 0.15
    elif after_id_number is False:
        score -= 0.08
    score += 0.15 * max(0.0, min(ocr_confidence, 1.0))
    if line_index == 0:
        score -= 0.20
    if _MALAY_PATRONYMIC_RE.search(cleaned):
        score += 0.15
    return score


def _extract_name_generic(lines: Sequence[Tuple[str, float]]) -> Optional[str]:
    """Last-resort scorer when card type / layout rules miss."""
    raw_strings = [t for t, _ in lines]
    total = len(raw_strings)
    label_only_idx: Set[int] = set()
    label_inline: dict = {}
    relation_idx: Set[int] = set()
    id_number_idx: Set[int] = set()

    for i, text in enumerate(raw_strings):
        if _is_label_only_line(text):
            label_only_idx.add(i)
            continue
        m = _NAME_LABEL_RE.search(text)
        if m:
            candidate = _clean_name_token(m.group(1))
            if candidate and not set(candidate.split()).issubset(_LABEL_WORD_VOCAB):
                if _is_structurally_valid_name(candidate) and not _has_noise_word(candidate):
                    label_inline[i] = candidate
        if _RELATION_MARKER_RE.search(text):
            relation_idx.add(i)
        if (
            re.search(r"\b\d{6}[-\s]?\d{2}[-\s]?\d{4}\b", text)
            or AADHAAR_RE.search(text)
            or ID_NO_DIGITS_RE.search(text)
            or LABELED_ID_RE.search(text)
        ):
            id_number_idx.add(i)

    relative_name_idx: Set[int] = set()
    for r in relation_idx:
        for j in (r + 1, r + 2):
            if 0 <= j < total and j not in relation_idx:
                relative_name_idx.add(j)
                break

    # Prefer explicit labels first
    labeled = _extract_name_passport(lines) or _extract_name_labeled_badge(lines)
    if labeled:
        return labeled
    name_dl = _extract_name_indian_dl(lines)
    if name_dl:
        return name_dl

    def _is_near(idx: int, anchors: Set[int], window: int = 2) -> bool:
        return any(abs(idx - a) <= window and idx != a for a in anchors)

    def _after_id(idx: int) -> Optional[bool]:
        if not id_number_idx:
            return None
        return idx > min(id_number_idx)

    scored: List[Tuple[float, int, str]] = []
    for i, text in enumerate(raw_strings):
        if i in label_only_idx or i in relation_idx or i in relative_name_idx:
            continue
        if _is_date_noise_text(text):
            continue
        cand = _candidate_ok(text)
        if not cand:
            continue
        s = _name_line_score(
            raw_text=text,
            ocr_confidence=lines[i][1],
            line_index=i,
            near_label=_is_near(i, label_only_idx) or _is_near(i, set(label_inline)),
            near_id_number=_is_near(i, id_number_idx),
            after_id_number=_after_id(i),
        )
        if s > 0.40:
            scored.append((s, i, cand))

    if not scored:
        return None
    scored.sort(key=lambda t: t[0], reverse=True)
    best_score, best_idx, best_name = scored[0]
    if len(scored) > 1:
        second_score, second_idx, second_name = scored[1]
        if (
            abs(second_idx - best_idx) == 1
            and second_score > 0.45
            and second_name != best_name
            and len(f"{best_name} {second_name}") <= 60
        ):
            ordered = (
                (best_name, second_name)
                if best_idx < second_idx
                else (second_name, best_name)
            )
            return f"{ordered[0]} {ordered[1]}".strip()
    return best_name


def extract_id_name(
    lines: List[Tuple[str, float]], document_hint: str = ""
) -> Optional[str]:
    """
    Extract cardholder name using layout rules for the detected card type.

    Lines must be in reading order (see run_rapid_ocr).
    """
    if not lines:
        return None

    card_type = detect_id_card_type(lines, document_hint)
    extractors = {
        "indian_dl": _extract_name_indian_dl,
        "my_dl": _extract_name_my_dl,
        "mykad": _extract_name_mykad,
        "aadhaar": _extract_name_aadhaar,
        "passport": _extract_name_passport,
        "labeled": _extract_name_labeled_badge,
    }
    name = None
    extractor = extractors.get(card_type)
    if extractor:
        name = extractor(lines)
    if not name:
        name = _extract_name_generic(lines)
    if not name:
        return None
    # Always run Malay particle spacing + title-case for API output
    formatted = _format_id_name(name)
    return formatted or _prettify_name(name)