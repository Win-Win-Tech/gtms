"""Normalize plate + CCTV check-in / check-out gate."""

from __future__ import annotations

import logging
import os
import re
from typing import Any, Dict, Optional

from django.core.cache import cache
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from visitor.lookup_options import normalize_vehicle_type
from visitor.models import Visitor, VisitorAsset, VisitorEntry
from visitor.utils import make_qr_token

from . import settings_helpers as anpr_settings

logger = logging.getLogger(__name__)

_PLATE_CLEAN_RE = re.compile(r"[^A-Z0-9]")

# YOLO COCO vehicle classes → VisitorLookupOption vehicle_type codes
_YOLO_LABEL_TO_VEHICLE_TYPE = {
    "car": "car",
    "truck": "truck",
    "bus": "bus",
    "motorcycle": "motorcycle",
    "motorbike": "motorcycle",
    "van": "van",
}


def normalize_plate(raw: str) -> str:
    text = (raw or "").upper().strip()
    text = _PLATE_CLEAN_RE.sub("", text)
    return text


def synthetic_ic(plate: str) -> str:
    return f"CCTV-{plate}"[:64]


def _cooldown_key(site_id: str, plate: str) -> str:
    return f"anpr:cooldown:{site_id}:{plate}"


def in_cooldown(site_id: str, plate: str) -> bool:
    return bool(cache.get(_cooldown_key(str(site_id), plate)))


def set_cooldown(site_id: str, plate: str) -> None:
    cache.set(_cooldown_key(str(site_id), plate), "1", timeout=anpr_settings.cooldown_sec())


def resolve_vehicle_type_from_yolo(yolo_label: Optional[str], location_id) -> str:
    """
    Map YOLO class label to a valid org vehicle_type code.
    Falls back to 'other' (if configured) then ''.
    """
    raw = (yolo_label or "").strip().lower()
    if raw in ("full_frame", "license_plate", "plate", "number_plate"):
        raw = ""
    mapped = _YOLO_LABEL_TO_VEHICLE_TYPE.get(raw, raw if raw else "")
    code = normalize_vehicle_type(mapped, location_id) if mapped else ""
    if code:
        return code
    other = normalize_vehicle_type("other", location_id)
    return other if other else ""


def _attach_anpr_evidence(
    entry: VisitorEntry,
    *,
    asset_type: str,
    evidence_path: Optional[str],
    plate: str,
) -> Optional[str]:
    """
    Persist ANPR JPEG onto VisitorAsset. Returns asset id or None.
    Copies bytes into media/visitor_assets/ — temp anpr_pending file is deleted by the task.
    """
    if not evidence_path or not os.path.isfile(evidence_path):
        return None
    try:
        with open(evidence_path, "rb") as fh:
            data = fh.read()
        if not data:
            return None
        base = os.path.basename(evidence_path)
        # Stable-ish name under visitor_assets/
        name = f"anpr_{asset_type}_{plate}_{base}"
        name = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:180]
        asset = VisitorAsset(
            visitor_entry=entry,
            asset_type=asset_type,
        )
        asset.file.save(name, ContentFile(data), save=True)
        logger.info(
            "[ANPR_GATE] saved asset type=%s entry=%s asset=%s file=%s",
            asset_type,
            entry.id,
            asset.id,
            asset.file.name,
        )
        return str(asset.id)
    except Exception:
        logger.exception(
            "[ANPR_GATE] failed to save evidence entry=%s path=%s type=%s",
            entry.id,
            evidence_path,
            asset_type,
        )
        return None


@transaction.atomic
def apply_gate_event(
    *,
    plate: str,
    site,
    location,
    direction_mode: str,
    cross_dir: int = 0,
    confidence: Optional[float] = None,
    evidence_path: Optional[str] = None,
    yolo_label: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Create check-in or check-out for a CCTV plate event.

    direction_mode: toggle | in | out (SiteCamera.direction)

    Evidence JPEG (if provided) is stored on VisitorAsset:
      check_in  → check_in_photo
      check_out → exit_photo

    vehicle_type comes from YOLO class (car/truck/bus/motorcycle), mapped to org lookup codes.
    """
    plate = normalize_plate(plate)
    if len(plate) < 4:
        return {"ok": False, "reason": "invalid_plate", "plate": plate}

    if in_cooldown(str(site.id), plate):
        logger.info("[ANPR_GATE] cooldown skip plate=%s site=%s", plate, site.id)
        return {"ok": False, "reason": "cooldown", "plate": plate}

    vehicle_type = resolve_vehicle_type_from_yolo(yolo_label, getattr(location, "id", None))

    ic = synthetic_ic(plate)
    visitor = (
        Visitor.objects.select_for_update()
        .filter(location=location, ic_passport_number=ic, is_deleted=False)
        .first()
    )
    if not visitor:
        visitor = Visitor.objects.create(
            location=location,
            ic_passport_number=ic,
            visitor_name=plate,
            phone_number="",
        )
    elif visitor.visitor_name != plate:
        visitor.visitor_name = plate
        visitor.save(update_fields=["visitor_name", "modified_on"])

    open_entry = (
        VisitorEntry.objects.select_for_update()
        .filter(
            visitor=visitor,
            site=site,
            status=VisitorEntry.STATUS_CHECKED_IN,
            is_deleted=False,
        )
        .order_by("-check_in_time", "-created_on")
        .first()
    )

    mode = (direction_mode or "toggle").lower()
    if mode not in ("toggle", "in", "out"):
        mode = "toggle"

    if mode == "in":
        if open_entry:
            return {"ok": False, "reason": "already_in", "plate": plate}
        action = "check_in"
    elif mode == "out":
        if not open_entry:
            return {"ok": False, "reason": "no_open_entry", "plate": plate}
        action = "check_out"
    else:
        action = "check_out" if open_entry else "check_in"

    now = timezone.now()
    if action == "check_in":
        entry = VisitorEntry.objects.create(
            visitor=visitor,
            host=None,
            location=location,
            site=site,
            entry_source=VisitorEntry.ENTRY_CCTV,
            visitor_type=VisitorEntry.TYPE_GUEST,
            status=VisitorEntry.STATUS_CHECKED_IN,
            purpose_of_visit="CCTV gate",
            vehicle_number=plate,
            vehicle_type=vehicle_type,
            visit_date=now.date(),
            check_in_time=now,
            qr_token=make_qr_token(),
            qr_expired=True,
        )
        asset_id = _attach_anpr_evidence(
            entry,
            asset_type=VisitorAsset.ASSET_CHECK_IN_PHOTO,
            evidence_path=evidence_path,
            plate=plate,
        )
        set_cooldown(str(site.id), plate)
        logger.info(
            "[ANPR_GATE] CHECK_IN plate=%s entry=%s site=%s conf=%s cross_dir=%s "
            "yolo=%s vehicle_type=%s evidence=%s asset=%s",
            plate,
            entry.id,
            site.id,
            confidence,
            cross_dir,
            yolo_label,
            vehicle_type,
            evidence_path,
            asset_id,
        )
        return {
            "ok": True,
            "action": "check_in",
            "plate": plate,
            "entry_id": str(entry.id),
            "vehicle_type": vehicle_type,
            "asset_id": asset_id,
            "asset_type": VisitorAsset.ASSET_CHECK_IN_PHOTO if asset_id else None,
        }

    open_entry.status = VisitorEntry.STATUS_CHECKED_OUT
    open_entry.check_out_time = now
    open_entry.qr_expired = True
    open_entry.save(
        update_fields=["status", "check_out_time", "qr_expired", "modified_on"]
    )
    asset_id = _attach_anpr_evidence(
        open_entry,
        asset_type=VisitorAsset.ASSET_EXIT_PHOTO,
        evidence_path=evidence_path,
        plate=plate,
    )
    set_cooldown(str(site.id), plate)
    logger.info(
        "[ANPR_GATE] CHECK_OUT plate=%s entry=%s site=%s conf=%s cross_dir=%s asset=%s",
        plate,
        open_entry.id,
        site.id,
        confidence,
        cross_dir,
        asset_id,
    )
    return {
        "ok": True,
        "action": "check_out",
        "plate": plate,
        "entry_id": str(open_entry.id),
        "asset_id": asset_id,
        "asset_type": VisitorAsset.ASSET_EXIT_PHOTO if asset_id else None,
    }
