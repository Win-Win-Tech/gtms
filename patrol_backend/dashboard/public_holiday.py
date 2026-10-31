"""Public-holiday match + attendance flag (worked that day at matching site/vendor)."""

from datetime import datetime, date

from .models import AttendanceCheckin, PublicHoliday


def attendance_counts_as_worked(attendance):
    """True when the person actually punched in (or has worked duration)."""
    if attendance is None:
        return False
    if getattr(attendance, "last_checkin_time", None) or getattr(attendance, "checkin_time", None):
        return True
    try:
        duration = int(attendance.duration_minutes or 0)
    except (TypeError, ValueError):
        duration = 0
    return duration > 0


def resolve_public_holiday(site_id, vendor_id, holiday_date):
    """Vendor-specific row wins over site-wide (vendor NULL)."""
    if not site_id or not holiday_date:
        return None
    qs = PublicHoliday.objects.filter(site_id=site_id, holiday_date=holiday_date)
    if vendor_id:
        specific = qs.filter(vendor_id=vendor_id).first()
        if specific:
            return specific
    return qs.filter(vendor_id__isnull=True).first()


def apply_public_holiday_flag(attendance, save=True):
    """Set worked_on_public_holiday + name from the matching holiday, or clear them."""
    if attendance is None:
        return None
    site_id = getattr(attendance, "site_id", None)
    holiday_date = getattr(attendance, "shift_date", None)
    guard = getattr(attendance, "guard", None)
    vendor_id = getattr(guard, "vendor_id", None) if guard is not None else None
    holiday = resolve_public_holiday(site_id, vendor_id, holiday_date)
    worked = bool(holiday) and attendance_counts_as_worked(attendance)
    attendance.worked_on_public_holiday = worked
    attendance.public_holiday_name = holiday.name if worked else None
    if save:
        attendance.save(
            update_fields=["worked_on_public_holiday", "public_holiday_name", "modified_on"]
        )
    return holiday if worked else None


def _attendance_qs_for_holiday(holiday):
    qs = AttendanceCheckin.objects.filter(
        site_id=holiday.site_id,
        shift_date=holiday.holiday_date,
    ).select_related("guard")
    if holiday.vendor_id:
        qs = qs.filter(guard__vendor_id=holiday.vendor_id)
    return qs


def backfill_for_holiday(holiday):
    """Re-evaluate matching attendance after a holiday is created or renamed."""
    updated = 0
    for att in _attendance_qs_for_holiday(holiday).iterator(chunk_size=200):
        apply_public_holiday_flag(att, save=True)
        updated += 1
    return updated


def clear_for_holiday(holiday):
    """After delete, re-resolve remaining holidays for previously matching rows."""
    qs = AttendanceCheckin.objects.filter(
        site_id=holiday.site_id,
        shift_date=holiday.holiday_date,
    ).select_related("guard")
    if holiday.vendor_id:
        qs = qs.filter(guard__vendor_id=holiday.vendor_id)
    updated = 0
    for att in qs.iterator(chunk_size=200):
        apply_public_holiday_flag(att, save=True)
        updated += 1
    return updated


def find_existing_holiday(site_id, vendor_id, holiday_date):
    qs = PublicHoliday.objects.filter(site_id=site_id, holiday_date=holiday_date)
    if vendor_id:
        return qs.filter(vendor_id=vendor_id).first()
    return qs.filter(vendor_id__isnull=True).first()


def upsert_public_holiday(*, org_location_id, site, vendor, holiday_date, name, user=None):
    """Create or update one (site, vendor-or-null, date) row. Returns (obj, created)."""
    vendor_id = getattr(vendor, "id", None) if vendor is not None else None
    existing = find_existing_holiday(site.id, vendor_id, holiday_date)
    name = (name or "").strip()
    if existing:
        fields = []
        if existing.name != name:
            existing.name = name
            fields.append("name")
        if user is not None:
            existing.modified_by = user
            fields.append("modified_by")
        if fields:
            fields.append("modified_on")
            existing.save(update_fields=fields)
        return existing, False
    obj = PublicHoliday.objects.create(
        org_location_id=org_location_id,
        site=site,
        vendor=vendor,
        holiday_date=holiday_date,
        name=name,
        created_by=user,
        modified_by=user,
    )
    return obj, True


def parse_holiday_date(raw):
    """Accept date, datetime, YYYY-MM-DD, DD-MM-YYYY, DD/MM/YYYY, Excel serial."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw.date()
    if isinstance(raw, date):
        return raw
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            from openpyxl.utils.datetime import from_excel

            parsed = from_excel(raw)
            if isinstance(parsed, datetime):
                return parsed.date()
            if isinstance(parsed, date):
                return parsed
        except Exception:
            return None
    text = str(raw).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%b-%Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "")).date()
    except ValueError:
        return None
