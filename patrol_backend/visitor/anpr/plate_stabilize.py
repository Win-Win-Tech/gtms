"""Stabilize OCR plate noise so the same vehicle does not become many near-duplicate plates.

Country-agnostic: no national plate-format assumptions. Uses fuzzy match + track/site memory.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Iterable, List, Optional, Tuple

from django.core.cache import cache
from django.utils import timezone

from visitor.models import VisitorEntry

from .gate import normalize_plate

logger = logging.getLogger(__name__)


def plate_quality_score(plate: str) -> int:
    """
    Generic preference only (not country-specific):
    - longer strings beat truncated OCR
    - mixed letters+digits slightly preferred over all-one-type noise
    """
    p = normalize_plate(plate)
    if not p:
        return 0
    has_alpha = any(c.isalpha() for c in p)
    has_digit = any(c.isdigit() for c in p)
    mix = 2 if (has_alpha and has_digit) else 0
    return len(p) * 10 + mix


def edit_distance(a: str, b: str) -> int:
    """Levenshtein distance (small strings only)."""
    a, b = a or "", b or ""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    if abs(len(a) - len(b)) > 3:
        return 99
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            ins = cur[j - 1] + 1
            delete = prev[j] + 1
            sub = prev[j - 1] + (0 if ca == cb else 1)
            cur.append(min(ins, delete, sub))
        prev = cur
    return prev[-1]


def _max_dist(a: str, b: str) -> int:
    """Allow small OCR mutations only; scale with plate length."""
    if abs(len(a) - len(b)) > 2:
        return 0
    # Short plates: 1 char only. Longer: up to 2 (swap / drop).
    if min(len(a), len(b)) >= 8:
        return 2 if abs(len(a) - len(b)) <= 1 else 1
    return 1


def _similar(a: str, b: str) -> bool:
    a, b = normalize_plate(a), normalize_plate(b)
    if not a or not b:
        return False
    return edit_distance(a, b) <= _max_dist(a, b)


def _prefer(a: str, b: str) -> str:
    """When two reads are the same car, keep the more complete one."""
    a, b = normalize_plate(a), normalize_plate(b)
    sa, sb = plate_quality_score(a), plate_quality_score(b)
    if sa != sb:
        return a if sa > sb else b
    return a if a <= b else b  # stable tie-break


def _best_candidate(raw: str, candidates: Iterable[str]) -> Optional[str]:
    """
    Fuzzy match raw OCR to a known plate.
    Never remaps to a *worse* (shorter/weaker) known plate — that caused
    TN59… → TN9… when junk open entries existed.
    """
    raw = normalize_plate(raw)
    raw_q = plate_quality_score(raw)
    best: Optional[str] = None
    best_dist = 99
    best_q = -1
    for c in candidates:
        c = normalize_plate(c or "")
        if not c:
            continue
        if c == raw:
            return c
        d = edit_distance(raw, c)
        if d > _max_dist(raw, c):
            continue
        cq = plate_quality_score(c)
        # Do not downgrade a stronger OCR read to a weaker known plate
        if cq < raw_q:
            continue
        if d < best_dist or (d == best_dist and cq > best_q):
            best, best_dist, best_q = c, d, cq
        elif d == best_dist and cq == best_q and best is not None:
            best = _prefer(best, c)
    return best


def _track_key(track_id: str) -> str:
    return f"anpr:track_plate:{track_id}"


def _votes_key(track_id: str) -> str:
    return f"anpr:track_votes:{track_id}"


def _site_recent_key(site_id: str) -> str:
    return f"anpr:site_plates:{site_id}"


def _ttl_sec() -> int:
    from . import settings_helpers as anpr_settings

    return max(300, int(anpr_settings.cooldown_sec()) * 5)


def _remember(track_id: str, site_id: str, plate: str) -> None:
    plate = normalize_plate(plate)
    ttl = _ttl_sec()
    cache.set(_track_key(track_id), plate, timeout=ttl)
    votes: List[str] = cache.get(_votes_key(track_id)) or []
    votes = [normalize_plate(v) for v in votes if v][-7:]
    votes.append(plate)
    cache.set(_votes_key(track_id), votes[-8:], timeout=ttl)
    recent: List[str] = cache.get(_site_recent_key(str(site_id))) or []
    recent = [normalize_plate(p) for p in recent if p]
    if plate not in recent:
        recent.append(plate)
    cache.set(_site_recent_key(str(site_id)), recent[-40:], timeout=ttl)


def _vote_winner(track_id: str) -> Optional[str]:
    votes: List[str] = cache.get(_votes_key(track_id)) or []
    votes = [normalize_plate(v) for v in votes if v]
    if not votes:
        return None
    clusters: List[List[str]] = []
    for v in votes:
        placed = False
        for cluster in clusters:
            if _similar(v, cluster[0]):
                cluster.append(v)
                placed = True
                break
        if not placed:
            clusters.append([v])
    clusters.sort(
        key=lambda c: (-len(c), -plate_quality_score(c[0]), -len(c[0]))
    )
    top = clusters[0]
    canon = top[0]
    for v in top[1:]:
        canon = _prefer(canon, v)
    return canon


def _open_cctv_plates(site_id) -> List[str]:
    qs = (
        VisitorEntry.objects.filter(
            site_id=site_id,
            status=VisitorEntry.STATUS_CHECKED_IN,
            entry_source=VisitorEntry.ENTRY_CCTV,
            is_deleted=False,
        )
        .exclude(vehicle_number="")
        .order_by("-check_in_time")
        .values_list("vehicle_number", flat=True)[:30]
    )
    return [normalize_plate(p) for p in qs if p]


def _recent_cctv_plates(site_id, hours: int = 24) -> List[str]:
    since = timezone.now() - timedelta(hours=hours)
    qs = (
        VisitorEntry.objects.filter(
            site_id=site_id,
            entry_source=VisitorEntry.ENTRY_CCTV,
            is_deleted=False,
            created_on__gte=since,
        )
        .exclude(vehicle_number="")
        .order_by("-created_on")
        .values_list("vehicle_number", flat=True)[:50]
    )
    return [normalize_plate(p) for p in qs if p]


def stabilize_plate(
    raw: str,
    *,
    site_id,
    track_id: str,
    confidence: Optional[float] = None,
) -> Tuple[str, str]:
    """
    Return (canonical_plate, reason). Country-agnostic.

    Priority:
      1) Open CCTV check-ins at site (fuzzy)
      2) Track vote / memory
      3) Recent site CCTV plates (fuzzy)
      4) Raw OCR
    """
    plate = normalize_plate(raw)
    if len(plate) < 4:
        return plate, "invalid"

    open_match = _best_candidate(plate, _open_cctv_plates(site_id))
    if open_match:
        chosen = open_match
        if chosen != plate:
            logger.info(
                "[ANPR_PLATE] stabilize raw=%s -> %s via=open_entry conf=%s track=%s",
                plate,
                chosen,
                confidence,
                track_id,
            )
        _remember(track_id, site_id, chosen)
        return chosen, "open_entry"

    cached = cache.get(_track_key(track_id))
    vote = _vote_winner(track_id)
    sticky = None
    if vote and _similar(plate, vote):
        if edit_distance(plate, vote) > 0:
            sticky = (
                vote
                if plate_quality_score(vote) >= plate_quality_score(plate)
                else _prefer(vote, plate)
            )
        else:
            sticky = vote
    elif cached and _similar(plate, cached):
        sticky = (
            cached
            if plate_quality_score(cached) >= plate_quality_score(plate)
            else _prefer(cached, plate)
        )

    if sticky:
        if sticky != plate:
            logger.info(
                "[ANPR_PLATE] stabilize raw=%s -> %s via=track_memory conf=%s track=%s",
                plate,
                sticky,
                confidence,
                track_id,
            )
        _remember(track_id, site_id, sticky)
        return sticky, "track_memory"

    recent_cache: List[str] = cache.get(_site_recent_key(str(site_id))) or []
    recent_db = _recent_cctv_plates(site_id)
    hist_match = _best_candidate(plate, list(recent_cache) + recent_db)
    if hist_match:
        chosen = hist_match
        if chosen != plate:
            logger.info(
                "[ANPR_PLATE] stabilize raw=%s -> %s via=site_history conf=%s track=%s",
                plate,
                chosen,
                confidence,
                track_id,
            )
        _remember(track_id, site_id, chosen)
        return chosen, "site_history"

    _remember(track_id, site_id, plate)
    return plate, "ocr"
