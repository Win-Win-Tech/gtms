import mimetypes
import os
import re
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.db import transaction
from django.db.models import Count
from django.utils import timezone
from django.utils.text import get_valid_filename

from authapp.site_access import caller_can_access_site
from scheduler.models import LocationSite

from .models import (
    SupportTicket,
    TicketActivityLog,
    TicketAssignment,
    TicketAttachment,
    TicketComment,
)
from .permissions import (
    can_view_ticket,
    is_organisation_admin,
    is_super_admin_user,
    is_support_user,
)
from .sla import ACTIVE_SLA_STATUSES, compute_due_at

User = get_user_model()


def visible_tickets_queryset(user, *, site_id=None):
    """
    List scope for GET /support/tickets/my/:

    - Org Admin → all tickets for their organisation (optional site filter)
    - Everyone else → tickets they created (optional site filter)

    SUPPORT / Super Admin use GET /support/support/tickets/ for the global queue.
    """
    qs = SupportTicket.objects.select_related(
        "category", "organisation", "site", "created_by", "current_assignee"
    ).order_by("-created_on")

    if is_organisation_admin(user):
        org_id = getattr(user, "location_id", None)
        if not org_id:
            return qs.none()
        qs = qs.filter(organisation_id=org_id)
    else:
        qs = qs.filter(created_by=user)

    if site_id and str(site_id).lower() not in ("all", "null", "undefined"):
        qs = qs.filter(site_id=site_id)
    return qs

# Private attachment limits (Phase 3 + Phase 6 media parity)
MAX_ATTACHMENT_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_MEDIA_FILES_PER_REQUEST = 10

ASSET_EXTENSIONS = {
    ".jpg",
    ".jpeg",
    ".png",
    ".gif",
    ".webp",
    ".mp4",
    ".mov",
    ".webm",
}
AUDIO_EXTENSIONS = {
    ".mp3",
    ".wav",
    ".m4a",
    ".aac",
    ".ogg",
    ".oga",
    ".3gp",
    ".amr",
}
# .webm allowed as audio only when uploaded under an audio field (preferred_kind=AUDIO).

DOC_EXTENSIONS = {
    ".pdf",
    ".txt",
    ".csv",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
}
ALLOWED_ATTACHMENT_EXTENSIONS = ASSET_EXTENSIONS | AUDIO_EXTENSIONS | DOC_EXTENSIONS
ALLOWED_ATTACHMENT_MIME_PREFIXES = (
    "image/",
    "audio/",
    "application/pdf",
    "text/plain",
    "text/csv",
    "application/msword",
    "application/vnd.openxmlformats",
    "application/vnd.ms-excel",
    "video/mp4",
    "video/quicktime",
    "video/webm",
)


class TicketWorkflowError(Exception):
    """Business-rule failure for ticket workflow actions."""

    def __init__(self, message, *, http_status=400, code=None):
        super().__init__(message)
        self.message = message
        self.http_status = http_status
        self.code = code or "workflow_error"


def log_activity(
    *,
    ticket,
    actor,
    action,
    old_value="",
    new_value="",
    comment="",
    metadata=None,
    is_public=True,
):
    return TicketActivityLog.objects.create(
        ticket=ticket,
        actor=actor,
        action=action,
        old_value=old_value or "",
        new_value=new_value or "",
        comment=comment or "",
        metadata_json=metadata or {},
        is_public=is_public,
    )


def resolve_ticket_org_and_site(user, site_id=None):
    """
    Derive organisation from authenticated user.
    Site: optional; must belong to user's org and be accessible.
    SUPPORT users may create tickets without org/site (rare); normal users use their location.
    """
    organisation = getattr(user, "location", None)
    site = None
    if site_id:
        site = LocationSite.objects.filter(id=site_id, is_active=True).select_related("location").first()
        if not site:
            raise ValueError("Site not found or inactive.")
        if organisation and str(site.location_id) != str(organisation.id):
            raise ValueError("Site does not belong to your organisation.")
        if not caller_can_access_site(user, site) and not getattr(user, "is_superuser", False):
            raise ValueError("You are not allowed to use this site.")
        if not organisation:
            organisation = site.location
    return organisation, site


def _normalize_media_uploads(uploaded_files=None, uploaded_file=None):
    """
    Normalize create/close media into a list of (file, kind) tuples.

    Accepts:
    - uploaded_files: iterable of UploadedFile or (UploadedFile, kind)
    - uploaded_file: legacy single file (kind inferred)
    """
    items = []
    if uploaded_files:
        for entry in uploaded_files:
            if entry is None:
                continue
            if isinstance(entry, (list, tuple)) and len(entry) >= 1:
                f = entry[0]
                kind = entry[1] if len(entry) > 1 else None
            else:
                f = entry
                kind = None
            if f is None:
                continue
            items.append((f, kind))
    if uploaded_file is not None:
        items.append((uploaded_file, None))
    if len(items) > MAX_MEDIA_FILES_PER_REQUEST:
        raise TicketWorkflowError(
            f"At most {MAX_MEDIA_FILES_PER_REQUEST} media files are allowed per request.",
            code="too_many_files",
        )
    return items


def infer_attachment_kind(uploaded, preferred_kind=None):
    """Map upload to TicketAttachment.Kind (ASSET / AUDIO / FILE)."""
    if preferred_kind in TicketAttachment.Kind.values:
        return preferred_kind
    original_name = getattr(uploaded, "name", "") or ""
    ext = os.path.splitext(original_name)[1].lower()
    content_type = (getattr(uploaded, "content_type", None) or "").lower()
    if content_type.startswith("audio/") or ext in AUDIO_EXTENSIONS:
        return TicketAttachment.Kind.AUDIO
    if (
        ext in ASSET_EXTENSIONS
        or content_type.startswith("image/")
        or content_type.startswith("video/")
    ):
        return TicketAttachment.Kind.ASSET
    return TicketAttachment.Kind.FILE


@transaction.atomic
def create_ticket(
    *,
    user,
    subject,
    description,
    category,
    priority,
    site_id=None,
    uploaded_file=None,
    uploaded_files=None,
    due_at=None,
):
    """
    Create an OPEN ticket. Optional media (Incident-style assets/audio, multi-file)
    attaches in the same transaction before SUPPORT is notified.

    due_at: optional explicit SLA deadline; otherwise derived from priority defaults.
    """
    media_items = _normalize_media_uploads(
        uploaded_files=uploaded_files, uploaded_file=uploaded_file
    )
    # Validate before inserting so a bad file never leaves an empty ticket + notify.
    for uploaded, preferred_kind in media_items:
        kind = infer_attachment_kind(uploaded, preferred_kind)
        _validate_attachment_file(uploaded, expected_kind=kind)

    organisation, site = resolve_ticket_org_and_site(user, site_id=site_id)
    ticket_number = SupportTicket.generate_ticket_number()
    resolved_priority = priority or SupportTicket.Priority.MEDIUM
    resolved_due = due_at or compute_due_at(priority=resolved_priority)
    ticket = SupportTicket.objects.create(
        ticket_number=ticket_number,
        subject=subject.strip(),
        description=description.strip(),
        category=category,
        priority=resolved_priority,
        status=SupportTicket.Status.OPEN,
        created_by=user,
        organisation=organisation,
        site=site,
        current_assignee=None,
        due_at=resolved_due,
    )
    log_activity(
        ticket=ticket,
        actor=user,
        action="TICKET_CREATED",
        new_value=SupportTicket.Status.OPEN,
        comment=f"Ticket {ticket.ticket_number} created",
        metadata={
            "ticket_number": ticket.ticket_number,
            "priority": ticket.priority,
            "category_id": str(category.id),
            "due_at": ticket.due_at.isoformat() if ticket.due_at else None,
        },
        is_public=True,
    )
    for uploaded, preferred_kind in media_items:
        kind = infer_attachment_kind(uploaded, preferred_kind)
        _attach_file_to_ticket(
            ticket=ticket,
            actor=user,
            uploaded_file=uploaded,
            kind=kind,
            purpose=TicketAttachment.Purpose.CREATE,
        )
    from .notifications import notify_ticket_created

    notify_ticket_created(ticket)
    return ticket


def _lock_ticket(ticket_id):
    ticket = (
        SupportTicket.objects.select_for_update()
        .select_related("category", "organisation", "site", "created_by", "current_assignee")
        .filter(pk=ticket_id)
        .first()
    )
    if not ticket:
        raise TicketWorkflowError("Ticket not found", http_status=404, code="not_found")
    return ticket


def _require_support_actor(actor):
    if not is_support_user(actor):
        raise TicketWorkflowError("SUPPORT role required.", http_status=403, code="forbidden")


def _get_support_assignee(user_id):
    user = User.objects.filter(pk=user_id, is_active=True, is_deleted=False).first()
    if not user:
        raise TicketWorkflowError("Assignee not found.", http_status=400, code="invalid_assignee")
    role = (getattr(user, "role", None) or "").strip().lower()
    if role != "support" and not getattr(user, "is_superuser", False):
        raise TicketWorkflowError(
            "Assignee must be a SUPPORT user.",
            http_status=400,
            code="invalid_assignee",
        )
    return user


def _record_assignment(*, ticket, actor, to_user, action, comment=""):
    return TicketAssignment.objects.create(
        ticket=ticket,
        from_user=ticket.current_assignee,
        to_user=to_user,
        action=action,
        comment=comment or "",
        assigned_by=actor,
    )


@transaction.atomic
def pickup_ticket(*, ticket_id, actor):
    """Claim an unassigned OPEN ticket → IN_PROGRESS (row-locked)."""
    _require_support_actor(actor)
    ticket = _lock_ticket(ticket_id)

    if ticket.status in (SupportTicket.Status.CLOSED, SupportTicket.Status.INVALID):
        raise TicketWorkflowError("Cannot pick up a closed or invalid ticket.")

    if ticket.status == SupportTicket.Status.RESOLVED:
        raise TicketWorkflowError("Resolved tickets cannot be picked up.")

    if ticket.current_assignee_id:
        if str(ticket.current_assignee_id) == str(actor.id):
            raise TicketWorkflowError(
                "You already own this ticket.",
                http_status=409,
                code="already_owned",
            )
        raise TicketWorkflowError(
            "Ticket is already assigned to another agent.",
            http_status=409,
            code="already_assigned",
        )

    if ticket.status not in (SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS):
        raise TicketWorkflowError(f"Cannot pick up ticket in status {ticket.status}.")

    old_status = ticket.status
    old_assignee = ticket.current_assignee_id
    ticket.current_assignee = actor
    ticket.status = SupportTicket.Status.IN_PROGRESS
    ticket.save(update_fields=["current_assignee", "status", "updated_on"])

    _record_assignment(
        ticket=ticket,
        actor=actor,
        to_user=actor,
        action=TicketAssignment.Action.PICKUP,
    )
    log_activity(
        ticket=ticket,
        actor=actor,
        action="PICKED_UP",
        old_value=old_status,
        new_value=SupportTicket.Status.IN_PROGRESS,
        comment="Ticket picked up",
        metadata={
            "from_assignee": str(old_assignee) if old_assignee else None,
            "to_assignee": str(actor.id),
        },
        is_public=True,
    )
    from .notifications import notify_ticket_assigned

    notify_ticket_assigned(ticket, actor=actor)
    return ticket


@transaction.atomic
def assign_ticket(*, ticket_id, actor, assignee_id, comment=""):
    """Assign/reassign to a SUPPORT user. OPEN → IN_PROGRESS when first assigned."""
    _require_support_actor(actor)
    ticket = _lock_ticket(ticket_id)
    assignee = _get_support_assignee(assignee_id)

    if ticket.status in (
        SupportTicket.Status.CLOSED,
        SupportTicket.Status.INVALID,
        SupportTicket.Status.RESOLVED,
    ):
        raise TicketWorkflowError(f"Cannot assign a ticket in status {ticket.status}.")

    if str(ticket.current_assignee_id or "") == str(assignee.id):
        raise TicketWorkflowError(
            "Ticket is already assigned to this user.",
            http_status=409,
            code="already_assigned",
        )

    old_status = ticket.status
    old_assignee = ticket.current_assignee
    had_assignee = bool(ticket.current_assignee_id)
    action = (
        TicketAssignment.Action.REASSIGN
        if had_assignee
        else TicketAssignment.Action.ASSIGN
    )
    comment = (comment or "").strip()
    if had_assignee and not comment:
        raise TicketWorkflowError("A comment is required when reassigning a ticket.")

    ticket.current_assignee = assignee
    if ticket.status == SupportTicket.Status.OPEN:
        ticket.status = SupportTicket.Status.IN_PROGRESS
    ticket.save(update_fields=["current_assignee", "status", "updated_on"])

    _record_assignment(
        ticket=ticket,
        actor=actor,
        to_user=assignee,
        action=action,
        comment=comment,
    )
    log_activity(
        ticket=ticket,
        actor=actor,
        action="ASSIGNED" if action == TicketAssignment.Action.ASSIGN else "REASSIGNED",
        old_value=old_status,
        new_value=ticket.status,
        comment=comment or f"Assigned to {assignee.name or assignee.email}",
        metadata={
            "from_assignee": str(old_assignee.id) if old_assignee else None,
            "to_assignee": str(assignee.id),
            "assignment_action": action,
        },
        is_public=True,
    )
    from .notifications import notify_ticket_assigned

    notify_ticket_assigned(ticket, actor=actor)
    return ticket


@transaction.atomic
def transfer_ticket(*, ticket_id, actor, assignee_id, comment):
    """Transfer ownership to another SUPPORT user (mandatory comment)."""
    comment = (comment or "").strip()
    if not comment:
        raise TicketWorkflowError("A comment is required when transferring a ticket.")
    return assign_ticket(
        ticket_id=ticket_id,
        actor=actor,
        assignee_id=assignee_id,
        comment=comment,
    )


@transaction.atomic
def invalidate_ticket(*, ticket_id, actor, reason):
    """OPEN | IN_PROGRESS → INVALID. Reason is visible to the creator."""
    _require_support_actor(actor)
    ticket = _lock_ticket(ticket_id)
    reason = (reason or "").strip()
    if not reason:
        raise TicketWorkflowError("Invalidation reason is required.")

    if ticket.status not in (
        SupportTicket.Status.OPEN,
        SupportTicket.Status.IN_PROGRESS,
    ):
        raise TicketWorkflowError(
            f"Only OPEN or IN_PROGRESS tickets can be invalidated (current: {ticket.status})."
        )

    old_status = ticket.status
    ticket.status = SupportTicket.Status.INVALID
    ticket.invalidation_reason = reason
    ticket.save(update_fields=["status", "invalidation_reason", "updated_on"])

    log_activity(
        ticket=ticket,
        actor=actor,
        action="INVALIDATED",
        old_value=old_status,
        new_value=SupportTicket.Status.INVALID,
        comment=reason,
        metadata={"reason": reason},
        is_public=True,
    )
    from .notifications import notify_ticket_invalidated

    notify_ticket_invalidated(ticket)
    return ticket


@transaction.atomic
def resolve_ticket(*, ticket_id, actor, summary):
    """IN_PROGRESS → RESOLVED with resolution summary."""
    _require_support_actor(actor)
    ticket = _lock_ticket(ticket_id)
    summary = (summary or "").strip()
    if not summary:
        raise TicketWorkflowError("Resolution summary is required.")

    if ticket.status != SupportTicket.Status.IN_PROGRESS:
        raise TicketWorkflowError(
            f"Only IN_PROGRESS tickets can be resolved (current: {ticket.status})."
        )

    old_status = ticket.status
    ticket.status = SupportTicket.Status.RESOLVED
    ticket.resolution_summary = summary
    ticket.resolved_at = timezone.now()
    ticket.save(
        update_fields=["status", "resolution_summary", "resolved_at", "updated_on"]
    )

    log_activity(
        ticket=ticket,
        actor=actor,
        action="RESOLVED",
        old_value=old_status,
        new_value=SupportTicket.Status.RESOLVED,
        comment=summary,
        metadata={"summary": summary},
        is_public=True,
    )
    from .notifications import notify_ticket_resolved

    notify_ticket_resolved(ticket)
    return ticket


@transaction.atomic
def close_ticket(
    *,
    ticket_id,
    actor,
    comment="",
    uploaded_files=None,
    uploaded_file=None,
):
    """
    RESOLVED → CLOSED (SUPPORT closes in v1).

    Optional proof/evidence media (assets/audio) is attached in the same
    transaction before status becomes CLOSED.
    """
    _require_support_actor(actor)
    ticket = _lock_ticket(ticket_id)

    if ticket.status != SupportTicket.Status.RESOLVED:
        raise TicketWorkflowError(
            f"Only RESOLVED tickets can be closed (current: {ticket.status})."
        )

    media_items = _normalize_media_uploads(
        uploaded_files=uploaded_files, uploaded_file=uploaded_file
    )
    for uploaded, preferred_kind in media_items:
        kind = infer_attachment_kind(uploaded, preferred_kind)
        _validate_attachment_file(uploaded, expected_kind=kind)

    attached_ids = []
    for uploaded, preferred_kind in media_items:
        kind = infer_attachment_kind(uploaded, preferred_kind)
        att = _attach_file_to_ticket(
            ticket=ticket,
            actor=actor,
            uploaded_file=uploaded,
            kind=kind,
            purpose=TicketAttachment.Purpose.CLOSE,
            notify=False,
        )
        attached_ids.append(str(att.id))

    old_status = ticket.status
    ticket.status = SupportTicket.Status.CLOSED
    ticket.closed_at = timezone.now()
    ticket.save(update_fields=["status", "closed_at", "updated_on"])

    log_activity(
        ticket=ticket,
        actor=actor,
        action="CLOSED",
        old_value=old_status,
        new_value=SupportTicket.Status.CLOSED,
        comment=(comment or "").strip() or "Ticket closed",
        metadata={"close_attachment_ids": attached_ids} if attached_ids else {},
        is_public=True,
    )
    from .notifications import notify_ticket_closed

    notify_ticket_closed(ticket, actor=actor)
    return ticket


@transaction.atomic
def reopen_ticket(*, ticket_id, actor, comment="", target_status=None):
    """
    Reopen rules (Phase 7):
    - Creator: RESOLVED → IN_PROGRESS (if assignee) or OPEN.
    - SUPPORT / Super Admin: RESOLVED, CLOSED, or INVALID → OPEN or IN_PROGRESS
      (CLOSED/INVALID require a comment). Optional target_status OPEN|IN_PROGRESS.
    """
    ticket = _lock_ticket(ticket_id)
    is_creator = str(ticket.created_by_id) == str(actor.id)
    support_actor = is_support_user(actor)
    note = (comment or "").strip()
    old_status = ticket.status

    if old_status == SupportTicket.Status.RESOLVED:
        if not is_creator and not support_actor:
            raise TicketWorkflowError(
                "Only the ticket creator or SUPPORT can reopen a resolved ticket.",
                http_status=403,
                code="forbidden",
            )
        if not note:
            note = (
                "Ticket reopened by support"
                if support_actor and not is_creator
                else "Ticket reopened by creator"
            )
    elif old_status in (
        SupportTicket.Status.CLOSED,
        SupportTicket.Status.INVALID,
    ):
        if not support_actor:
            code = (
                "cannot_reopen_closed"
                if old_status == SupportTicket.Status.CLOSED
                else "cannot_reopen_invalid"
            )
            raise TicketWorkflowError(
                f"{old_status.title()} tickets can only be reopened by SUPPORT or Super Admin.",
                http_status=403 if not is_creator else 400,
                code=code if is_creator else "forbidden",
            )
        if not note:
            raise TicketWorkflowError(
                "A comment is required when reopening a CLOSED or INVALID ticket.",
                code="comment_required",
            )
    else:
        raise TicketWorkflowError(
            f"Only RESOLVED, CLOSED, or INVALID tickets can be reopened (current: {old_status}).",
            code="invalid_status",
        )

    allowed_targets = {
        SupportTicket.Status.OPEN,
        SupportTicket.Status.IN_PROGRESS,
    }
    requested = (target_status or "").strip().upper() or None
    if requested and support_actor:
        if requested not in allowed_targets:
            raise TicketWorkflowError(
                "target_status must be OPEN or IN_PROGRESS.",
                code="invalid_target_status",
            )
        new_status = requested
    else:
        new_status = (
            SupportTicket.Status.IN_PROGRESS
            if ticket.current_assignee_id
            else SupportTicket.Status.OPEN
        )

    assignee_changed = False
    if (
        new_status == SupportTicket.Status.IN_PROGRESS
        and not ticket.current_assignee_id
        and support_actor
    ):
        # Reopen into active ownership when SUPPORT asks for IN_PROGRESS unassigned.
        ticket.current_assignee = actor
        assignee_changed = True

    update_fields = ["status", "resolved_at", "updated_on"]
    ticket.status = new_status
    ticket.resolved_at = None
    if old_status == SupportTicket.Status.CLOSED:
        ticket.closed_at = None
        update_fields.append("closed_at")
    if assignee_changed:
        update_fields.append("current_assignee")
    ticket.save(update_fields=update_fields)

    log_activity(
        ticket=ticket,
        actor=actor,
        action="REOPENED",
        old_value=old_status,
        new_value=new_status,
        comment=note,
        metadata={
            "reopened_by": str(actor.id),
            "from_status": old_status,
            "support_reopen": bool(support_actor and not is_creator),
        },
        is_public=True,
    )
    from .notifications import notify_ticket_reopened

    notify_ticket_reopened(ticket, actor=actor, comment=note)
    return ticket


def build_dashboard_metrics(*, actor, organisation_id=None, site_id=None):
    """
    Global / org / site ticket metrics for SUPPORT console.
    Optional organisation_id / site_id narrow the breakdowns and totals.
    """
    _require_support_actor(actor)

    qs = SupportTicket.objects.all()
    if organisation_id:
        qs = qs.filter(organisation_id=organisation_id)
    if site_id:
        qs = qs.filter(site_id=site_id)

    by_status = {s: 0 for s, _ in SupportTicket.Status.choices}
    for row in qs.values("status").annotate(count=Count("id")):
        by_status[row["status"]] = row["count"]

    by_priority = {p: 0 for p, _ in SupportTicket.Priority.choices}
    for row in qs.values("priority").annotate(count=Count("id")):
        by_priority[row["priority"]] = row["count"]

    total = sum(by_status.values())
    unassigned = qs.filter(
        current_assignee__isnull=True,
        status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS],
    ).count()
    my_assigned = qs.filter(
        current_assignee=actor,
        status__in=[SupportTicket.Status.OPEN, SupportTicket.Status.IN_PROGRESS],
    ).count()
    now = timezone.now()
    overdue_active = qs.filter(
        status__in=ACTIVE_SLA_STATUSES,
        due_at__isnull=False,
        due_at__lt=now,
    ).count()
    due_soon_active = qs.filter(
        status__in=ACTIVE_SLA_STATUSES,
        due_at__isnull=False,
        due_at__gte=now,
        due_at__lte=now + timedelta(hours=2),
    ).count()

    # Best-effort SLA breach/approaching notifications (capped per request).
    try:
        from .notifications import maybe_notify_sla_for_queryset

        maybe_notify_sla_for_queryset(
            qs.filter(status__in=ACTIVE_SLA_STATUSES, due_at__isnull=False),
            limit=25,
        )
    except Exception:
        pass

    overdue_filter = {
        "status__in": ACTIVE_SLA_STATUSES,
        "due_at__isnull": False,
        "due_at__lt": now,
    }

    org_rows = (
        qs.exclude(organisation_id__isnull=True)
        .values("organisation_id", "organisation__name", "status")
        .annotate(count=Count("id"))
        .order_by("organisation__name")
    )
    orgs = {}
    for row in org_rows:
        oid = str(row["organisation_id"])
        entry = orgs.setdefault(
            oid,
            {
                "organisation_id": oid,
                "organisation_name": row["organisation__name"] or "",
                "total": 0,
                "overdue": 0,
                "by_status": {s: 0 for s, _ in SupportTicket.Status.choices},
            },
        )
        entry["by_status"][row["status"]] = row["count"]
        entry["total"] += row["count"]

    for row in (
        qs.exclude(organisation_id__isnull=True)
        .filter(**overdue_filter)
        .values("organisation_id")
        .annotate(count=Count("id"))
    ):
        oid = str(row["organisation_id"])
        if oid in orgs:
            orgs[oid]["overdue"] = row["count"]

    organisations = sorted(orgs.values(), key=lambda x: (-x["total"], x["organisation_name"]))

    site_qs = qs.exclude(site_id__isnull=True)
    site_rows = (
        site_qs.values(
            "site_id",
            "site__name",
            "organisation_id",
            "organisation__name",
            "status",
        )
        .annotate(count=Count("id"))
        .order_by("site__name")
    )
    sites_map = {}
    for row in site_rows:
        sid = str(row["site_id"])
        entry = sites_map.setdefault(
            sid,
            {
                "site_id": sid,
                "site_name": row["site__name"] or "",
                "organisation_id": str(row["organisation_id"]) if row["organisation_id"] else None,
                "organisation_name": row["organisation__name"] or "",
                "total": 0,
                "overdue": 0,
                "by_status": {s: 0 for s, _ in SupportTicket.Status.choices},
            },
        )
        entry["by_status"][row["status"]] = row["count"]
        entry["total"] += row["count"]

    for row in site_qs.filter(**overdue_filter).values("site_id").annotate(count=Count("id")):
        sid = str(row["site_id"])
        if sid in sites_map:
            sites_map[sid]["overdue"] = row["count"]

    sites = sorted(sites_map.values(), key=lambda x: (-x["total"], x["site_name"]))

    return {
        "totals": {
            "all": total,
            "by_status": by_status,
            "by_priority": by_priority,
            "unassigned_open": unassigned,
            "my_assigned_active": my_assigned,
            "overdue_active": overdue_active,
            "due_soon_active": due_soon_active,
        },
        "organisations": organisations,
        "sites": sites,
        "filters": {
            "organisation_id": str(organisation_id) if organisation_id else None,
            "site_id": str(site_id) if site_id else None,
        },
    }


def list_support_agents():
    """Active SUPPORT agents for assign/transfer pickers."""
    return (
        User.objects.filter(role__iexact="support", is_active=True, is_deleted=False)
        .order_by("name", "email")
    )


_EXT_MIME_FALLBACKS = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp4": "video/mp4",
    ".mov": "video/quicktime",
    ".webm": "video/webm",
    ".m4v": "video/x-m4v",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".amr": "audio/amr",
    ".3gp": "audio/3gpp",
    ".pdf": "application/pdf",
}


def resolve_attachment_content_type(attachment) -> str:
    """
    Prefer stored mime_type; otherwise guess from filename so browsers can
    render private blob previews (octet-stream breaks <img>/<video>/<audio>).
    """
    stored = (getattr(attachment, "mime_type", None) or "").strip().lower()
    if stored and stored not in ("application/octet-stream", "binary/octet-stream", "binary"):
        return stored
    name = getattr(attachment, "file_name", None) or ""
    guessed, _ = mimetypes.guess_type(name)
    if guessed:
        return guessed
    ext = os.path.splitext(name)[1].lower()
    return _EXT_MIME_FALLBACKS.get(ext) or stored or "application/octet-stream"


def filter_support_tickets_queryset(qs, params, *, actor=None):
    """Apply common queue/report filters from query params."""
    status_filter = (params.get("status") or "").strip()
    if status_filter and status_filter.lower() not in ("all", ""):
        qs = qs.filter(status=status_filter.upper())

    org_id = params.get("organisation_id") or params.get("location_id")
    if org_id and str(org_id).lower() not in ("all", "null", "undefined", ""):
        qs = qs.filter(organisation_id=org_id)

    site_id = params.get("site_id")
    if site_id and str(site_id).lower() not in ("all", "null", "undefined", ""):
        qs = qs.filter(site_id=site_id)

    priority = (params.get("priority") or "").strip()
    if priority and priority.lower() not in ("all", ""):
        qs = qs.filter(priority=priority.upper())

    assignee = (params.get("assignee") or "").strip()
    assignee_id = (params.get("assignee_id") or "").strip()
    if assignee == "me" and actor is not None:
        qs = qs.filter(current_assignee=actor)
    elif assignee == "unassigned":
        qs = qs.filter(current_assignee__isnull=True)
    elif assignee_id:
        qs = qs.filter(current_assignee_id=assignee_id)
    elif assignee and assignee.lower() not in ("all", "me", "unassigned"):
        qs = qs.filter(current_assignee_id=assignee)

    overdue = (params.get("overdue") or "").strip().lower()
    if overdue in ("1", "true", "yes"):
        qs = qs.filter(
            status__in=ACTIVE_SLA_STATUSES,
            due_at__isnull=False,
            due_at__lt=timezone.now(),
        )

    date_filter = (params.get("date_filter") or "").strip().lower()
    date_from = (params.get("date_from") or params.get("start_date") or "").strip()
    date_to = (params.get("date_to") or params.get("end_date") or "").strip()

    if date_filter and date_filter not in ("all", "custom", ""):
        today = timezone.localdate()
        if date_filter == "today":
            date_from = date_to = today.isoformat()
        elif date_filter == "yesterday":
            day = today - timedelta(days=1)
            date_from = date_to = day.isoformat()
        elif date_filter == "this_week":
            start = today - timedelta(days=today.weekday())
            date_from = start.isoformat()
            date_to = (start + timedelta(days=6)).isoformat()
        elif date_filter == "last_week":
            start = today - timedelta(days=today.weekday() + 7)
            date_from = start.isoformat()
            date_to = (start + timedelta(days=6)).isoformat()
        elif date_filter == "this_month":
            start = today.replace(day=1)
            if today.month == 12:
                end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
            else:
                end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
            date_from = start.isoformat()
            date_to = end.isoformat()
        elif date_filter == "last_month":
            first_this = today.replace(day=1)
            last_prev = first_this - timedelta(days=1)
            date_from = last_prev.replace(day=1).isoformat()
            date_to = last_prev.isoformat()

    if date_from:
        qs = qs.filter(created_on__date__gte=date_from)
    if date_to:
        qs = qs.filter(created_on__date__lte=date_to)

    return qs


def list_support_users(*, include_inactive=True):
    """SUPPORT-role users for Super Admin management tab."""
    qs = User.objects.filter(role__iexact="support", is_deleted=False).order_by(
        "name", "email"
    )
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return qs


@transaction.atomic
def create_support_user(*, actor, name, email, password):
    if not is_super_admin_user(actor):
        raise TicketWorkflowError(
            "Only Super Admin can manage support users.",
            http_status=403,
            code="forbidden",
        )
    name = (name or "").strip()
    email = (email or "").strip().lower()
    if not name:
        raise TicketWorkflowError("Name is required.", code="name_required")
    if not email:
        raise TicketWorkflowError("Email is required.", code="email_required")
    if not password:
        raise TicketWorkflowError("Password is required.", code="password_required")
    if User.objects.filter(email__iexact=email, is_deleted=False).exists():
        raise TicketWorkflowError(
            "A user with this email already exists.",
            code="email_taken",
        )
    user = User.objects.create_user(
        email=email,
        password=password,
        name=name,
        role="support",
        location=None,
        all_org_sites=False,
        created_by=actor,
    )
    return user


@transaction.atomic
def update_support_user(
    *,
    actor,
    user_id,
    name=None,
    email=None,
    password=None,
    is_active=None,
):
    if not is_super_admin_user(actor):
        raise TicketWorkflowError(
            "Only Super Admin can manage support users.",
            http_status=403,
            code="forbidden",
        )
    user = (
        User.objects.filter(pk=user_id, role__iexact="support", is_deleted=False)
        .first()
    )
    if not user:
        raise TicketWorkflowError(
            "Support user not found.",
            http_status=404,
            code="not_found",
        )
    update_fields = ["modified_on"]
    if name is not None:
        name = str(name).strip()
        if not name:
            raise TicketWorkflowError("Name cannot be empty.", code="name_required")
        user.name = name
        update_fields.append("name")
    if email is not None:
        email = str(email).strip().lower()
        if not email:
            raise TicketWorkflowError("Email cannot be empty.", code="email_required")
        exists = (
            User.objects.filter(email__iexact=email, is_deleted=False)
            .exclude(pk=user.pk)
            .exists()
        )
        if exists:
            raise TicketWorkflowError(
                "A user with this email already exists.",
                code="email_taken",
            )
        user.email = email
        update_fields.append("email")
    if is_active is not None:
        user.is_active = bool(is_active)
        update_fields.append("is_active")
    if password:
        user.set_password(password)
        update_fields.append("password")
    user.modified_by = actor
    update_fields.append("modified_by")
    # Keep SUPPORT users global (no org binding).
    if user.location_id is not None:
        user.location = None
        update_fields.append("location")
    if user.all_org_sites:
        user.all_org_sites = False
        update_fields.append("all_org_sites")
    user.save(update_fields=list(dict.fromkeys(update_fields)))
    return user


def _sanitize_attachment_name(filename: str) -> str:
    name = get_valid_filename(os.path.basename(filename or "") or "attachment")
    name = re.sub(r"\s+", "_", name)
    return name[:200] or "attachment"


def _validate_attachment_file(uploaded, expected_kind=None):
    if not uploaded:
        raise TicketWorkflowError(
            "File is required.",
            http_status=400,
            code="file_required",
        )
    size = getattr(uploaded, "size", None) or 0
    if size <= 0:
        raise TicketWorkflowError("Empty files are not allowed.", code="empty_file")
    if size > MAX_ATTACHMENT_BYTES:
        raise TicketWorkflowError(
            f"File exceeds maximum size of {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB.",
            code="file_too_large",
        )
    original_name = getattr(uploaded, "name", "") or "attachment"
    ext = os.path.splitext(original_name)[1].lower()
    if ext not in ALLOWED_ATTACHMENT_EXTENSIONS:
        raise TicketWorkflowError(
            f"File type '{ext or 'unknown'}' is not allowed.",
            code="file_type_not_allowed",
        )
    if expected_kind == TicketAttachment.Kind.AUDIO:
        content_type_l = (getattr(uploaded, "content_type", None) or "").lower()
        # Explicit audio field may also accept .webm / audio/* mime.
        if (
            ext not in AUDIO_EXTENSIONS
            and ext != ".webm"
            and not content_type_l.startswith("audio/")
        ):
            raise TicketWorkflowError(
                f"Audio field does not accept '{ext or 'unknown'}'.",
                code="file_type_not_allowed",
            )
    elif expected_kind == TicketAttachment.Kind.ASSET:
        if ext not in ASSET_EXTENSIONS:
            raise TicketWorkflowError(
                f"Asset field does not accept '{ext or 'unknown'}'.",
                code="file_type_not_allowed",
            )

    content_type = (getattr(uploaded, "content_type", None) or "").lower()
    if content_type and not any(
        content_type.startswith(p) or content_type == p
        for p in ALLOWED_ATTACHMENT_MIME_PREFIXES
    ):
        # Soft-check: some browsers send octet-stream; allow if extension is known.
        if content_type not in ("application/octet-stream", ""):
            raise TicketWorkflowError(
                f"MIME type '{content_type}' is not allowed.",
                code="mime_not_allowed",
            )
    if not content_type or content_type in (
        "application/octet-stream",
        "binary/octet-stream",
    ):
        guessed, _ = mimetypes.guess_type(original_name)
        if guessed:
            content_type = guessed
        else:
            content_type = _EXT_MIME_FALLBACKS.get(ext, content_type)
    return original_name, content_type, size


@transaction.atomic
def add_ticket_attachment(*, ticket_id, actor, uploaded_file, comment_id=None):
    """
    Attach a private file to a ticket (creator, org admin of ticket org, or SUPPORT).
    Blocked for CLOSED / INVALID tickets.
    """
    ticket = _lock_ticket(ticket_id)
    if not can_view_ticket(actor, ticket):
        raise TicketWorkflowError(
            "You do not have permission to attach files to this ticket.",
            http_status=403,
            code="forbidden",
        )
    if ticket.status in (SupportTicket.Status.CLOSED, SupportTicket.Status.INVALID):
        raise TicketWorkflowError(
            "Cannot attach files to a closed or invalid ticket.",
            code="ticket_closed",
        )

    comment = None
    if comment_id:
        comment = TicketComment.objects.filter(pk=comment_id, ticket=ticket).first()
        if not comment:
            raise TicketWorkflowError("Comment not found on this ticket.", code="invalid_comment")
        if (
            comment.visibility == TicketComment.Visibility.INTERNAL
            and not is_support_user(actor)
        ):
            raise TicketWorkflowError(
                "Cannot attach to an internal comment.",
                http_status=403,
                code="forbidden",
            )

    kind = infer_attachment_kind(uploaded_file)
    attachment = _attach_file_to_ticket(
        ticket=ticket,
        actor=actor,
        uploaded_file=uploaded_file,
        comment=comment,
        kind=kind,
        purpose=TicketAttachment.Purpose.GENERAL,
        notify=True,
    )
    return attachment


def _attach_file_to_ticket(
    *,
    ticket,
    actor,
    uploaded_file,
    comment=None,
    kind=None,
    purpose=None,
    notify=False,
):
    """Persist attachment + public activity. Caller must already authorize the ticket."""
    resolved_kind = kind or infer_attachment_kind(uploaded_file)
    original_name, content_type, size = _validate_attachment_file(
        uploaded_file, expected_kind=resolved_kind
    )
    safe_name = _sanitize_attachment_name(original_name)
    uploaded_file.name = safe_name
    resolved_purpose = purpose or TicketAttachment.Purpose.GENERAL

    attachment = TicketAttachment.objects.create(
        ticket=ticket,
        comment=comment,
        uploaded_by=actor,
        file=uploaded_file,
        file_name=safe_name,
        mime_type=content_type or "",
        size_bytes=size,
        kind=resolved_kind,
        purpose=resolved_purpose,
    )
    log_activity(
        ticket=ticket,
        actor=actor,
        action="ATTACHMENT_ADDED",
        comment=safe_name,
        metadata={
            "attachment_id": str(attachment.id),
            "file_name": safe_name,
            "size_bytes": size,
            "mime_type": content_type or "",
            "kind": resolved_kind,
            "purpose": resolved_purpose,
            "comment_id": str(comment.id) if comment else None,
        },
        is_public=True,
    )
    if notify:
        from .notifications import notify_ticket_attachment

        notify_ticket_attachment(ticket, uploader=actor, file_name=safe_name)
    return attachment


def get_ticket_attachment(*, ticket_id, attachment_id, actor):
    """Return attachment if actor may access the ticket; else raise."""
    ticket = (
        SupportTicket.objects.select_related("created_by", "organisation")
        .filter(pk=ticket_id)
        .first()
    )
    if not ticket:
        raise TicketWorkflowError("Ticket not found", http_status=404, code="not_found")
    if not can_view_ticket(actor, ticket):
        raise TicketWorkflowError(
            "You do not have permission to download this attachment.",
            http_status=403,
            code="forbidden",
        )
    attachment = TicketAttachment.objects.filter(pk=attachment_id, ticket=ticket).first()
    if not attachment:
        raise TicketWorkflowError(
            "Attachment not found", http_status=404, code="not_found"
        )
    return attachment


def filtered_activity_qs(ticket, viewer):
    """Creator sees public activity only; SUPPORT sees all."""
    qs = ticket.activity_logs.select_related("actor").all()
    if not is_support_user(viewer):
        qs = qs.filter(is_public=True)
    return qs


def _format_duration_compact(ms):
    """Compact duration labels for response timeline (e.g. 2m, 1h 5m)."""
    if ms is None or ms < 0:
        return None
    total_sec = int(ms // 1000)
    if total_sec < 60:
        return "0m" if total_sec <= 0 else "<1m"
    total_min = total_sec // 60
    if total_min < 60:
        return f"{total_min}m"
    hours = total_min // 60
    mins = total_min % 60
    if hours < 48:
        return f"{hours}h {mins}m" if mins else f"{hours}h"
    days = hours // 24
    rem_h = hours % 24
    return f"{days}d {rem_h}h" if rem_h else f"{days}d"


def _first_activity_ts(activity_rows, actions):
    want = {str(a).upper() for a in actions}
    for row in activity_rows or []:
        action = str(getattr(row, "action", None) or "").upper()
        if action not in want:
            continue
        created = getattr(row, "created_on", None)
        if created is None:
            continue
        return int(created.timestamp() * 1000)
    return None


def compute_ticket_durations(ticket, activity_rows=None):
    """
    Server-side response-timeline durations (mirrors web computeTicketDurations).

    Uses model timestamps plus activity fallbacks for assign/pickup / resolve /
    invalidate / close when fields are missing.
    """
    empty = {
        "open_to_assign": None,
        "assign_to_resolve": None,
        "assign_to_resolve_label": None,
        "resolve_to_close": None,
        "total": None,
        "open_to_assign_ms": None,
        "assign_to_resolve_ms": None,
        "resolve_to_close_ms": None,
        "total_ms": None,
        "segments": [],
        "is_complete": False,
    }
    if ticket is None:
        return empty

    if activity_rows is None:
        activity_rows = list(
            ticket.activity_logs.select_related("actor").order_by("created_on")
        )

    status = str(ticket.status or "").upper()
    created = (
        int(ticket.created_on.timestamp() * 1000) if ticket.created_on else None
    )
    assigned = _first_activity_ts(activity_rows, ["ASSIGNED", "PICKED_UP"])
    resolved = (
        int(ticket.resolved_at.timestamp() * 1000)
        if ticket.resolved_at
        else _first_activity_ts(activity_rows, ["RESOLVED"])
    )
    invalidated = _first_activity_ts(activity_rows, ["INVALIDATED"])
    closed = (
        int(ticket.closed_at.timestamp() * 1000)
        if ticket.closed_at
        else _first_activity_ts(activity_rows, ["CLOSED"])
    )

    is_invalid_path = invalidated is not None and (
        resolved is None or invalidated <= resolved
    )
    if resolved is not None and invalidated is not None:
        end_for_assign_resolve = min(resolved, invalidated)
    else:
        end_for_assign_resolve = resolved if resolved is not None else invalidated
    assign_to_resolve_label = None
    if end_for_assign_resolve is not None:
        assign_to_resolve_label = (
            "Assign→Invalid" if is_invalid_path else "Assign→Resolve"
        )

    is_terminal = status in ("CLOSED", "RESOLVED", "INVALID")
    now_ms = int(timezone.now().timestamp() * 1000)
    if closed is not None:
        total_end = closed
    elif resolved is not None:
        total_end = resolved
    elif invalidated is not None:
        total_end = invalidated
    elif is_terminal and ticket.updated_on:
        total_end = int(ticket.updated_on.timestamp() * 1000)
    else:
        total_end = now_ms

    open_to_assign_ms = (
        assigned - created
        if created is not None and assigned is not None and assigned >= created
        else None
    )
    assign_to_resolve_ms = (
        end_for_assign_resolve - assigned
        if (
            assigned is not None
            and end_for_assign_resolve is not None
            and end_for_assign_resolve >= assigned
        )
        else None
    )
    resolve_to_close_ms = (
        closed - resolved
        if (
            not is_invalid_path
            and resolved is not None
            and closed is not None
            and closed >= resolved
        )
        else None
    )
    total_ms = (
        total_end - created
        if created is not None and total_end is not None and total_end >= created
        else None
    )

    segments = []
    if open_to_assign_ms is not None:
        segments.append(
            {
                "key": "openAssign",
                "label": "Open→Assign",
                "ms": open_to_assign_ms,
                "muted": False,
            }
        )
    elif created is not None and assigned is None and not is_terminal:
        segments.append(
            {
                "key": "openAssign",
                "label": "Open→Now",
                "ms": max(0, now_ms - created),
                "muted": True,
            }
        )

    if assign_to_resolve_ms is not None:
        segments.append(
            {
                "key": "assignInvalid" if is_invalid_path else "assignResolve",
                "label": assign_to_resolve_label or "Assign→Resolve",
                "ms": assign_to_resolve_ms,
                "muted": False,
            }
        )
    elif assigned is not None and end_for_assign_resolve is None and not is_terminal:
        segments.append(
            {
                "key": "assignResolve",
                "label": "Assign→Now",
                "ms": max(0, now_ms - assigned),
                "muted": True,
            }
        )

    if resolve_to_close_ms is not None:
        segments.append(
            {
                "key": "resolveClose",
                "label": "Resolve→Close",
                "ms": resolve_to_close_ms,
                "muted": False,
            }
        )
    elif (
        not is_invalid_path
        and resolved is not None
        and closed is None
        and status == "RESOLVED"
    ):
        segments.append(
            {
                "key": "resolveClose",
                "label": "Resolve→Now",
                "ms": max(0, now_ms - resolved),
                "muted": True,
            }
        )

    if not segments and total_ms is not None and total_ms > 0:
        segments.append(
            {
                "key": "total",
                "label": "Total" if is_terminal else "Elapsed",
                "ms": total_ms,
                "muted": not is_terminal,
            }
        )

    for seg in segments:
        seg["display"] = _format_duration_compact(seg["ms"]) or "0m"

    return {
        "open_to_assign": _format_duration_compact(open_to_assign_ms),
        "assign_to_resolve": _format_duration_compact(assign_to_resolve_ms),
        "assign_to_resolve_label": assign_to_resolve_label,
        "resolve_to_close": _format_duration_compact(resolve_to_close_ms),
        "total": _format_duration_compact(total_ms),
        "open_to_assign_ms": open_to_assign_ms,
        "assign_to_resolve_ms": assign_to_resolve_ms,
        "resolve_to_close_ms": resolve_to_close_ms,
        "total_ms": total_ms,
        "segments": segments,
        "is_complete": is_terminal,
    }

