"""Posted site helpers for v5 assign / my-sites. Live assignment APIs do not use this."""

from datetime import timedelta

from django.db.models import Q
from rest_framework.exceptions import PermissionDenied, ValidationError

from authapp.site_access import assert_caller_can_access_site, caller_can_access_site, get_site_or_error
from scheduler.models import AssignmentDailySite, GuardSiteCache


def _iter_dates(start, end):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def attendance_marked_on(guard_id, on_date):
    """True if the guard has a punch / attendance row for that shift date."""
    from dashboard.models import AttendanceCheckin, CheckInLog

    if AttendanceCheckin.objects.filter(
        guard_id=guard_id,
        shift_date=on_date,
    ).filter(Q(checkin_time__isnull=False) | Q(checkin_count__gt=0)).exists():
        return True
    return CheckInLog.objects.filter(
        guard_id=guard_id,
        assignment__start_date__lte=on_date,
        assignment__end_date__gte=on_date,
        type="checkin",
    ).exists()


def fill_daily_sites(assignment, site_id, date=None, caller=None):
    """
    Write AssignmentDailySite for one day or every day in the assignment range.
    Does not write GuardSiteCache.
    """
    site = get_site_or_error(site_id)
    if caller is not None:
        assert_caller_can_access_site(caller, site)

    location_id = assignment.location_id or getattr(assignment.shift, "location_id", None)
    if location_id and str(site.location_id) != str(location_id):
        raise ValidationError({"site_id": "Site does not belong to the assignment organisation."})

    guard = assignment.guard
    if guard and not caller_can_access_site(guard, site) and not getattr(guard, "is_superuser", False):
        raise ValidationError({"site_id": "This user is not assigned to that site."})

    if date is not None:
        if date < assignment.start_date or date > assignment.end_date:
            raise ValidationError({"date": "Date is outside the assignment range."})
        dates = [date]
    else:
        dates = list(_iter_dates(assignment.start_date, assignment.end_date))
    loc_id = location_id or site.location_id

    existing = {
        row.date: row
        for row in AssignmentDailySite.objects.filter(guard=guard, date__in=dates)
    }
    to_create = []
    to_update = []
    for d in dates:
        row = existing.get(d)
        if row:
            row.site = site
            row.location_id = loc_id
            row.assignment = assignment
            to_update.append(row)
        else:
            to_create.append(
                AssignmentDailySite(
                    guard=guard,
                    date=d,
                    site=site,
                    location_id=loc_id,
                    assignment=assignment,
                )
            )
    if to_create:
        AssignmentDailySite.objects.bulk_create(to_create)
    if to_update:
        AssignmentDailySite.objects.bulk_update(to_update, ["site", "location", "assignment"])
    return site


def clear_daily_sites(assignment, date=None):
    """Remove posted-site rows for this assignment (header All / un-post / delete)."""
    if not assignment or not getattr(assignment, "pk", None):
        return 0
    qs = AssignmentDailySite.objects.filter(assignment=assignment)
    if date is not None:
        qs = qs.filter(date=date)
    deleted, _ = qs.delete()
    return deleted


def current_site_for(guard, on_date):
    """
    Latest GuardSiteCache for guard+date if still allowed,
    else AssignmentDailySite, else None.
    Returns dict {id, name, date} or None.
    """
    if not guard or not on_date:
        return None

    cache = (
        GuardSiteCache.objects.filter(guard=guard, date=on_date)
        .select_related("site")
        .order_by("-created_on", "-id")
        .first()
    )
    if cache and cache.site_id:
        if caller_can_access_site(guard, cache.site) or getattr(guard, "is_superuser", False):
            return {
                "id": str(cache.site_id),
                "name": cache.site.name,
                "date": str(on_date),
            }

    daily = (
        AssignmentDailySite.objects.filter(guard=guard, date=on_date)
        .select_related("site")
        .first()
    )
    if daily and daily.site_id:
        return {
            "id": str(daily.site_id),
            "name": daily.site.name,
            "date": str(on_date),
        }
    return None


def assigned_site_for(guard, on_date):
    """Roster site from AssignmentDailySite only (ignores GuardSiteCache)."""
    if not guard or not on_date:
        return None
    daily = (
        AssignmentDailySite.objects.filter(guard=guard, date=on_date)
        .select_related("site")
        .first()
    )
    if daily and daily.site_id:
        return {
            "id": str(daily.site_id),
            "name": daily.site.name,
            "date": str(on_date),
        }
    return None


def site_ids_payload(guard, on_date):
    """Flat ids for shift_today_v5: roster vs last selected (cache then daily)."""
    assigned = assigned_site_for(guard, on_date)
    selected = current_site_for(guard, on_date)
    return {
        "assigned_site_id": assigned["id"] if assigned else None,
        "assigned_site_name": assigned["name"] if assigned else None,
        "last_selected_site_id": selected["id"] if selected else None,
        "last_selected_site_name": selected["name"] if selected else None,
    }


def posted_site_for(guard_id, on_date):
    daily = (
        AssignmentDailySite.objects.filter(guard_id=guard_id, date=on_date)
        .select_related("site")
        .first()
    )
    if not daily:
        return None, None
    return str(daily.site_id), daily.site.name


def posted_site_for_assignment(assignment):
    """
    Posted site(s) written for this assignment row only.
    All / omitted site_id creates no daily rows, so this returns empty.
    """
    if not assignment or not getattr(assignment, "pk", None):
        return None, None
    rows = assignment.daily_sites.select_related("site").all()
    names = []
    ids = []
    seen = set()
    for row in rows:
        if not row.site_id:
            continue
        key = str(row.site_id)
        if key in seen:
            continue
        seen.add(key)
        ids.append(key)
        names.append(row.site.name if row.site else key)
    if not names:
        return None, None
    if len(names) == 1:
        return ids[0], names[0]
    return None, ", ".join(names)


def apply_select_site(user, site_id, on_date, assignment=None, caller=None):
    """
    Mobile site select / switch (POST my-sites, create_assignment_v5).
    No daily row → insert. Daily + no attendance → update daily.
    Daily + attendance marked → insert GuardSiteCache.
    """
    site = get_site_or_error(site_id)
    actor = caller or user
    assert_caller_can_access_site(actor, site)
    if user.location_id and str(site.location_id) != str(user.location_id) and not user.is_superuser:
        raise PermissionDenied("Site does not belong to your organisation.")
    if not caller_can_access_site(user, site) and not getattr(user, "is_superuser", False):
        raise ValidationError({"site_id": "This user is not assigned to that site."})

    daily = AssignmentDailySite.objects.filter(guard=user, date=on_date).first()
    loc_id = site.location_id
    if daily is None:
        AssignmentDailySite.objects.create(
            guard=user,
            date=on_date,
            site=site,
            location_id=loc_id,
            assignment=assignment,
        )
    elif attendance_marked_on(user.id, on_date):
        GuardSiteCache.objects.create(
            guard=user,
            date=on_date,
            site=site,
            location_id=loc_id,
        )
    else:
        daily.site = site
        daily.location_id = loc_id
        if assignment is not None:
            daily.assignment = assignment
        daily.save(update_fields=["site", "location", "assignment", "modified_on"])

    return current_site_for(user, on_date)
