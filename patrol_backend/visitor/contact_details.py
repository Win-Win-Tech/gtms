"""
Visitor name / phone for an entry.

CCTV entries may carry their own name + phone (``VisitorEntry.visitor_name`` /
``phone_number``) for one visit; blank means "use the linked Visitor's".
The first time a CCTV visitor gets real details they go on the Visitor itself
(see ``master_is_placeholder``).
"""

from __future__ import annotations

from django.db.models import Q

CCTV_IC_PREFIX = "CCTV-"


def _norm(text) -> str:
    return "".join(str(text or "").upper().split())


def _clean(text) -> str:
    return str(text or "").strip()


def cctv_ic_plate(visitor) -> str:
    """Plate inside a CCTV synthetic IC (``CCTV-<plate>``), else ''."""
    ic = _clean(getattr(visitor, "ic_passport_number", None))
    if ic.upper().startswith(CCTV_IC_PREFIX):
        return ic[len(CCTV_IC_PREFIX):]
    return ""


def entry_visitor_name(entry) -> str:
    """The entry's own visitor name when set, else the linked Visitor's."""
    own = _clean(getattr(entry, "visitor_name", None))
    if own:
        return own
    visitor = getattr(entry, "visitor", None)
    return getattr(visitor, "visitor_name", None) or "" if visitor else ""


def entry_phone_number(entry) -> str:
    """The entry's own phone number when set, else the linked Visitor's."""
    own = _clean(getattr(entry, "phone_number", None))
    if own:
        return own
    visitor = getattr(entry, "visitor", None)
    return getattr(visitor, "phone_number", None) or "" if visitor else ""


def contact_search_q(search: str) -> Q:
    """
    VisitorEntry filter: name or phone contains ``search``, using the entry's
    own value when set, else the linked Visitor's.
    """
    return (
        Q(visitor_name__icontains=search)
        | (Q(visitor_name="") & Q(visitor__visitor_name__icontains=search))
        | Q(phone_number__icontains=search)
        | (Q(phone_number="") & Q(visitor__phone_number__icontains=search))
    )


def master_is_placeholder(visitor, plate: str = "") -> bool:
    """
    True while a CCTV Visitor has no real details yet: no phone, and the name is
    blank or the plate (ANPR stores the plate as the name). ``plate`` is the
    entry's vehicle number; the plate inside the synthetic IC also counts, so an
    OCR spelling variant on the entry still matches.
    """
    if visitor is None or _clean(getattr(visitor, "phone_number", None)):
        return False
    name = _norm(getattr(visitor, "visitor_name", None))
    if not name:
        return True
    plates = {_norm(plate), _norm(cctv_ic_plate(visitor))} - {""}
    return name in plates
