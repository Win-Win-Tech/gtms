from django.http import FileResponse, HttpResponse
from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from .exports import generate_support_excel, generate_support_pdf
from .models import SupportTicket, TicketAttachment, TicketCategory, TicketComment
from .pagination import paginate_queryset
from .permissions import (
    IsTicketCreatorOrSupport,
    is_super_admin_user,
    is_support_user,
)
from .serializers import (
    SupportAgentSerializer,
    SupportTicketCreateSerializer,
    SupportTicketDetailSerializer,
    SupportTicketListSerializer,
    SupportUserSerializer,
    TicketActivityLogSerializer,
    TicketAttachmentSerializer,
    TicketCategorySerializer,
    TicketCommentSerializer,
)
from .services import (
    TicketWorkflowError,
    add_ticket_attachment,
    assign_ticket,
    build_dashboard_metrics,
    close_ticket,
    create_support_user,
    filter_support_tickets_queryset,
    filtered_activity_qs,
    get_ticket_attachment,
    invalidate_ticket,
    list_support_agents,
    list_support_users,
    log_activity,
    pickup_ticket,
    reopen_ticket,
    resolve_attachment_content_type,
    resolve_ticket,
    transfer_ticket,
    update_support_user,
    visible_tickets_queryset,
)
from .throttles import (
    SupportAttachmentThrottle,
    SupportCommentThrottle,
    SupportCreateThrottle,
    SupportReopenThrottle,
)


def _ok(message, data=None, code=status.HTTP_200_OK, **extra):
    payload = {"status": "success", "message": message, "data": data}
    if extra:
        payload.update(extra)
    return Response(payload, status=code)


def _err(message, data=None, code=status.HTTP_400_BAD_REQUEST):
    return Response(
        {"status": "error", "message": message, "data": data},
        status=code,
    )


def _workflow_err(exc: TicketWorkflowError):
    return _err(exc.message, {"code": exc.code}, code=exc.http_status)


def _detail_payload(ticket, request):
    ticket = (
        SupportTicket.objects.select_related(
            "category", "organisation", "site", "created_by", "current_assignee"
        )
        .prefetch_related(
            "comments__author",
            "activity_logs__actor",
            "attachments__uploaded_by",
        )
        .get(pk=ticket.pk)
    )
    return SupportTicketDetailSerializer(ticket, context={"request": request}).data


class TicketCategoryListView(APIView):
    """Active categories for Raise Ticket form."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        qs = TicketCategory.objects.filter(is_active=True).order_by("sort_order", "name")
        ser = TicketCategorySerializer(qs, many=True)
        return _ok("Categories fetched", ser.data)


def _collect_named_files(request, *field_names):
    """Collect UploadedFile objects from getlist/get across alias field names."""
    files = []
    seen_ids = set()
    for name in field_names:
        for f in request.FILES.getlist(name):
            if f is None:
                continue
            fid = id(f)
            if fid in seen_ids:
                continue
            seen_ids.add(fid)
            files.append(f)
    return files


def _extract_media_uploads(request):
    """
    Incident-parity multipart media for create/close.

    Assets (image/video): `assets`, `asset`, `photos`, `photo`, `images`, `image`, `videos`, `video`
    Audio: `audio`, `audios`, `description_audio`
    Legacy single file: `file`, `attachment` (kind inferred)

    Returns list of (UploadedFile, preferred_kind|None).
    """
    items = []
    for f in _collect_named_files(
        request,
        "assets",
        "asset",
        "photos",
        "photo",
        "images",
        "image",
        "videos",
        "video",
    ):
        items.append((f, TicketAttachment.Kind.ASSET))
    for f in _collect_named_files(
        request, "audio", "audios", "description_audio"
    ):
        items.append((f, TicketAttachment.Kind.AUDIO))
    for f in _collect_named_files(request, "file", "attachment", "files"):
        items.append((f, None))
    return items


def _create_ticket_from_request(request):
    """
    Shared create path for JSON or multipart Raise Ticket.

    Optional multipart media (assets / audio / legacy file) is stored in the same
    create transaction (same validation rules as POST .../attachments/).
    """
    media = _extract_media_uploads(request)
    ser = SupportTicketCreateSerializer(
        data=request.data,
        context={
            "request": request,
            "uploaded_files": media or None,
        },
    )
    if not ser.is_valid():
        return _err("Validation failed", ser.errors)
    ticket = ser.save()
    detail = SupportTicketDetailSerializer(ticket, context={"request": request})
    return _ok("Ticket created", detail.data, status.HTTP_201_CREATED)


class MyTicketListCreateView(APIView):
    """
    GET  /support/tickets/my/  — list tickets created by current user
    POST /support/tickets/my/  — create ticket (alias; prefer POST /support/tickets/)
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    throttle_classes = [SupportCreateThrottle]

    def get_throttles(self):
        if self.request.method == "GET":
            return []
        return super().get_throttles()

    def get(self, request):
        qs = (
            SupportTicket.objects.filter(created_by=request.user)
            .select_related("category", "organisation", "site", "created_by", "current_assignee")
            .order_by("-created_on")
        )
        # Same status / date / overdue filters as queue & reports
        qs = filter_support_tickets_queryset(
            qs, request.query_params, actor=request.user, request=request
        )
        page_qs, pagination = paginate_queryset(qs, request)
        ser = SupportTicketListSerializer(
            page_qs, many=True, context={"request": request}
        )
        return _ok("My tickets fetched", ser.data, pagination=pagination)

    def post(self, request):
        return _create_ticket_from_request(request)


class TicketCreateView(APIView):
    """POST /support/tickets/ — Raise Ticket (JSON or multipart with optional media)."""

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, FormParser, MultiPartParser]
    throttle_classes = [SupportCreateThrottle]

    def post(self, request):
        return _create_ticket_from_request(request)


class TicketDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsTicketCreatorOrSupport]

    def get_object(self, pk):
        return (
            SupportTicket.objects.select_related(
                "category", "organisation", "site", "created_by", "current_assignee"
            )
            .prefetch_related(
                "comments__author",
                "activity_logs__actor",
                "attachments__uploaded_by",
            )
            .filter(pk=pk)
            .first()
        )

    def get(self, request, pk):
        ticket = self.get_object(pk)
        if not ticket:
            return _err("Ticket not found", code=status.HTTP_404_NOT_FOUND)
        try:
            self.check_object_permissions(request, ticket)
        except PermissionDenied:
            return _err(
                "You do not have permission to view this ticket.",
                code=status.HTTP_403_FORBIDDEN,
            )
        ser = SupportTicketDetailSerializer(ticket, context={"request": request})
        return _ok("Ticket details fetched", ser.data)


def _add_ticket_comment(request, ticket, *, return_detail=False):
    """Shared comment create used by /comments/ and /actions/."""
    if ticket.status in (
        SupportTicket.Status.CLOSED,
        SupportTicket.Status.INVALID,
    ):
        return _err("Cannot comment on a closed or invalid ticket.")

    body = (request.data.get("body") or "").strip()
    if not body:
        return _err("Comment body is required.", {"body": ["This field is required."]})

    visibility = (request.data.get("visibility") or TicketComment.Visibility.PUBLIC).upper()
    if visibility == TicketComment.Visibility.INTERNAL and not is_support_user(request.user):
        return _err(
            "Only SUPPORT users can add internal notes.",
            code=status.HTTP_403_FORBIDDEN,
        )
    if visibility not in TicketComment.Visibility.values:
        visibility = TicketComment.Visibility.PUBLIC

    comment = TicketComment.objects.create(
        ticket=ticket,
        author=request.user,
        body=body,
        visibility=visibility,
    )
    log_activity(
        ticket=ticket,
        actor=request.user,
        action="COMMENT_ADDED",
        comment=body[:500],
        metadata={"visibility": visibility, "comment_id": str(comment.id)},
        is_public=(visibility == TicketComment.Visibility.PUBLIC),
    )
    if visibility == TicketComment.Visibility.PUBLIC:
        from .notifications import notify_ticket_public_comment

        notify_ticket_public_comment(
            ticket, author=request.user, comment_preview=body
        )
    if return_detail:
        return _ok(
            "Comment added",
            _detail_payload(ticket, request),
            status.HTTP_201_CREATED,
        )
    return _ok(
        "Comment added",
        TicketCommentSerializer(comment, context={"request": request}).data,
        status.HTTP_201_CREATED,
    )


class TicketCommentCreateView(APIView):
    """Deprecated path — prefer POST /support/tickets/<id>/actions/ with action=comment."""

    permission_classes = [permissions.IsAuthenticated, IsTicketCreatorOrSupport]
    throttle_classes = [SupportCommentThrottle]

    def post(self, request, pk):
        ticket = SupportTicket.objects.filter(pk=pk).first()
        if not ticket:
            return _err("Ticket not found", code=status.HTTP_404_NOT_FOUND)
        try:
            self.check_object_permissions(request, ticket)
        except PermissionDenied:
            return _err(
                "You do not have permission to comment on this ticket.",
                code=status.HTTP_403_FORBIDDEN,
            )
        return _add_ticket_comment(request, ticket, return_detail=False)


class TicketActivityListView(APIView):
    """
    GET /support/tickets/<id>/activity/
    Creator-filtered timeline (public only); SUPPORT sees all events.
    """

    permission_classes = [permissions.IsAuthenticated, IsTicketCreatorOrSupport]

    def get(self, request, pk):
        ticket = SupportTicket.objects.filter(pk=pk).first()
        if not ticket:
            return _err("Ticket not found", code=status.HTTP_404_NOT_FOUND)
        try:
            self.check_object_permissions(request, ticket)
        except PermissionDenied:
            return _err(
                "You do not have permission to view this ticket.",
                code=status.HTTP_403_FORBIDDEN,
            )
        qs = filtered_activity_qs(ticket, request.user)
        return _ok(
            "Activity timeline fetched",
            TicketActivityLogSerializer(
                qs, many=True, context={"request": request}
            ).data,
        )


class TicketAttachmentUploadView(APIView):
    """POST /support/tickets/<id>/attachments/ — multipart file upload (private)."""

    permission_classes = [permissions.IsAuthenticated, IsTicketCreatorOrSupport]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    throttle_classes = [SupportAttachmentThrottle]

    def post(self, request, pk):
        ticket = SupportTicket.objects.filter(pk=pk).first()
        if not ticket:
            return _err("Ticket not found", code=status.HTTP_404_NOT_FOUND)
        try:
            self.check_object_permissions(request, ticket)
        except PermissionDenied:
            return _err(
                "You do not have permission to attach files to this ticket.",
                code=status.HTTP_403_FORBIDDEN,
            )

        uploaded = request.FILES.get("file") or request.FILES.get("attachment")
        comment_id = request.data.get("comment_id") or None
        try:
            attachment = add_ticket_attachment(
                ticket_id=pk,
                actor=request.user,
                uploaded_file=uploaded,
                comment_id=comment_id,
            )
        except TicketWorkflowError as exc:
            return _workflow_err(exc)

        return _ok(
            "Attachment uploaded",
            TicketAttachmentSerializer(attachment, context={"request": request}).data,
            status.HTTP_201_CREATED,
        )


class TicketAttachmentDownloadView(APIView):
    """
    GET /support/tickets/<id>/attachments/<attachment_id>/download/
    Auth-gated private file download (no public MEDIA URL required).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk, attachment_id):
        try:
            attachment = get_ticket_attachment(
                ticket_id=pk, attachment_id=attachment_id, actor=request.user
            )
        except TicketWorkflowError as exc:
            return _workflow_err(exc)

        if not attachment.file:
            return _err("File missing", code=status.HTTP_404_NOT_FOUND)
        try:
            handle = attachment.file.open("rb")
        except Exception:
            return _err(
                "Unable to open attachment",
                code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        content_type = resolve_attachment_content_type(attachment)
        # Preview uses inline so browsers / blob URLs get a usable media Content-Type
        # without forcing a download; default remains attachment for Download button.
        disposition = (request.query_params.get("disposition") or "").strip().lower()
        inline = disposition in ("inline", "preview") or (
            request.query_params.get("inline") or ""
        ).strip().lower() in ("1", "true", "yes")
        return FileResponse(
            handle,
            as_attachment=not inline,
            filename=attachment.file_name or "attachment",
            content_type=content_type,
        )


class SupportTicketQueueView(APIView):
    """SUPPORT global queue."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        qs = SupportTicket.objects.select_related(
            "category", "organisation", "site", "created_by", "current_assignee"
        ).order_by("-created_on")
        qs = filter_support_tickets_queryset(
            qs, request.query_params, actor=request.user, request=request
        )
        page_qs, pagination = paginate_queryset(qs, request, default_page_size=50)
        ser = SupportTicketListSerializer(
            page_qs, many=True, context={"request": request}
        )
        return _ok("Support ticket queue fetched", ser.data, pagination=pagination)


class SupportDashboardView(APIView):
    """
    GET /support/dashboard/
    Global / org / site metrics for SUPPORT console.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        org_id = request.query_params.get("organisation_id") or request.query_params.get(
            "location_id"
        )
        site_id = request.query_params.get("site_id")
        # Header "All" / literal "null" must not hit UUID filters.
        if org_id and str(org_id).lower() in ("all", "null", "undefined", ""):
            org_id = None
        if site_id and str(site_id).lower() in ("all", "null", "undefined", ""):
            site_id = None
        try:
            data = build_dashboard_metrics(
                actor=request.user,
                organisation_id=org_id or None,
                site_id=site_id or None,
            )
        except TicketWorkflowError as exc:
            return _workflow_err(exc)
        return _ok("Support dashboard metrics fetched", data)


class SupportAgentListView(APIView):
    """List SUPPORT agents for assign/transfer pickers."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        agents = list_support_agents()
        return _ok("Support agents fetched", SupportAgentSerializer(agents, many=True).data)


class _SupportTicketActionBase(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _run(self, request, pk, fn, success_message):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        try:
            ticket = fn()
        except TicketWorkflowError as exc:
            return _workflow_err(exc)
        return _ok(success_message, _detail_payload(ticket, request))


def _resolve_summary_from_data(data):
    return (
        data.get("resolution")
        or data.get("summary")
        or data.get("resolution_summary")
        or data.get("comment")
        or ""
    )


class TicketActionsView(APIView):
    """
    POST /support/tickets/<id>/actions/

    Combined JSON workflow endpoint. Discriminator field: `action`.

    Supported actions:
      pickup | assign | transfer | resolve | invalidate | reopen | comment

    Create (multipart media) and close (multipart evidence) stay on their own
    endpoints — they are intentionally not part of this JSON actions API.
    """

    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser]

    def get_throttles(self):
        action = (self.request.data.get("action") or "").strip().lower()
        if action == "comment":
            return [SupportCommentThrottle()]
        if action == "reopen":
            return [SupportReopenThrottle()]
        return []

    def post(self, request, pk):
        action = (request.data.get("action") or "").strip().lower()
        if not action:
            return _err(
                "action is required.",
                {"action": ["This field is required."]},
            )

        if action == "comment":
            ticket = SupportTicket.objects.filter(pk=pk).first()
            if not ticket:
                return _err("Ticket not found", code=status.HTTP_404_NOT_FOUND)
            perm = IsTicketCreatorOrSupport()
            if not perm.has_object_permission(request, self, ticket):
                return _err(
                    "You do not have permission to comment on this ticket.",
                    code=status.HTTP_403_FORBIDDEN,
                )
            return _add_ticket_comment(request, ticket, return_detail=True)

        if action == "reopen":
            comment = request.data.get("comment") or request.data.get("reason") or ""
            target_status = request.data.get("target_status") or request.data.get("status")
            try:
                ticket = reopen_ticket(
                    ticket_id=pk,
                    actor=request.user,
                    comment=comment,
                    target_status=target_status,
                )
            except TicketWorkflowError as exc:
                return _workflow_err(exc)
            return _ok("Ticket reopened", _detail_payload(ticket, request))

        if action == "pickup":
            if not is_support_user(request.user):
                return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
            try:
                ticket = pickup_ticket(ticket_id=pk, actor=request.user)
            except TicketWorkflowError as exc:
                return _workflow_err(exc)
            return _ok("Ticket picked up", _detail_payload(ticket, request))

        if action == "assign":
            if not is_support_user(request.user):
                return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
            assignee_id = request.data.get("assignee_id")
            if not assignee_id:
                return _err(
                    "assignee_id is required.",
                    {"assignee_id": ["This field is required."]},
                )
            comment = request.data.get("comment") or ""
            try:
                ticket = assign_ticket(
                    ticket_id=pk,
                    actor=request.user,
                    assignee_id=assignee_id,
                    comment=comment,
                )
            except TicketWorkflowError as exc:
                return _workflow_err(exc)
            return _ok("Ticket assigned", _detail_payload(ticket, request))

        if action == "transfer":
            if not is_support_user(request.user):
                return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
            assignee_id = request.data.get("assignee_id")
            comment = request.data.get("comment") or ""
            if not assignee_id:
                return _err(
                    "assignee_id is required.",
                    {"assignee_id": ["This field is required."]},
                )
            try:
                ticket = transfer_ticket(
                    ticket_id=pk,
                    actor=request.user,
                    assignee_id=assignee_id,
                    comment=comment,
                )
            except TicketWorkflowError as exc:
                return _workflow_err(exc)
            return _ok("Ticket transferred", _detail_payload(ticket, request))

        if action == "resolve":
            if not is_support_user(request.user):
                return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
            summary = _resolve_summary_from_data(request.data)
            try:
                ticket = resolve_ticket(
                    ticket_id=pk, actor=request.user, summary=summary
                )
            except TicketWorkflowError as exc:
                return _workflow_err(exc)
            return _ok("Ticket resolved", _detail_payload(ticket, request))

        if action == "invalidate":
            if not is_support_user(request.user):
                return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
            reason = request.data.get("reason") or request.data.get("comment") or ""
            try:
                ticket = invalidate_ticket(
                    ticket_id=pk, actor=request.user, reason=reason
                )
            except TicketWorkflowError as exc:
                return _workflow_err(exc)
            return _ok("Ticket invalidated", _detail_payload(ticket, request))

        return _err(
            f"Unknown action '{action}'.",
            {
                "action": [
                    "Must be one of: pickup, assign, transfer, resolve, "
                    "invalidate, reopen, comment"
                ]
            },
        )


class TicketPickupView(_SupportTicketActionBase):
    """Deprecated — prefer POST .../actions/ with action=pickup."""

    def post(self, request, pk):
        return self._run(
            request,
            pk,
            lambda: pickup_ticket(ticket_id=pk, actor=request.user),
            "Ticket picked up",
        )


class TicketAssignView(_SupportTicketActionBase):
    """Deprecated — prefer POST .../actions/ with action=assign."""

    def post(self, request, pk):
        assignee_id = request.data.get("assignee_id")
        if not assignee_id:
            return _err("assignee_id is required.", {"assignee_id": ["This field is required."]})
        comment = request.data.get("comment") or ""
        return self._run(
            request,
            pk,
            lambda: assign_ticket(
                ticket_id=pk,
                actor=request.user,
                assignee_id=assignee_id,
                comment=comment,
            ),
            "Ticket assigned",
        )


class TicketTransferView(_SupportTicketActionBase):
    """Deprecated — prefer POST .../actions/ with action=transfer."""

    def post(self, request, pk):
        assignee_id = request.data.get("assignee_id")
        comment = request.data.get("comment") or ""
        if not assignee_id:
            return _err("assignee_id is required.", {"assignee_id": ["This field is required."]})
        return self._run(
            request,
            pk,
            lambda: transfer_ticket(
                ticket_id=pk,
                actor=request.user,
                assignee_id=assignee_id,
                comment=comment,
            ),
            "Ticket transferred",
        )


class TicketInvalidateView(_SupportTicketActionBase):
    """Deprecated — prefer POST .../actions/ with action=invalidate."""

    def post(self, request, pk):
        reason = request.data.get("reason") or request.data.get("comment") or ""
        return self._run(
            request,
            pk,
            lambda: invalidate_ticket(ticket_id=pk, actor=request.user, reason=reason),
            "Ticket invalidated",
        )


class TicketResolveView(_SupportTicketActionBase):
    """Deprecated — prefer POST .../actions/ with action=resolve."""

    def post(self, request, pk):
        summary = _resolve_summary_from_data(request.data)
        return self._run(
            request,
            pk,
            lambda: resolve_ticket(ticket_id=pk, actor=request.user, summary=summary),
            "Ticket resolved",
        )


class TicketCloseView(_SupportTicketActionBase):
    """
    POST /support/tickets/<id>/close/
    JSON or multipart; optional proof/evidence media (assets/audio like create).
    """

    parser_classes = [JSONParser, FormParser, MultiPartParser]

    def post(self, request, pk):
        comment = request.data.get("comment") or ""
        media = _extract_media_uploads(request)
        return self._run(
            request,
            pk,
            lambda: close_ticket(
                ticket_id=pk,
                actor=request.user,
                comment=comment,
                uploaded_files=media or None,
            ),
            "Ticket closed",
        )


class TicketReopenView(APIView):
    """
    Deprecated — prefer POST .../actions/ with action=reopen.

    - Creator: RESOLVED only
    - SUPPORT / Super Admin: RESOLVED, CLOSED, or INVALID (CLOSED/INVALID need comment);
      optional target_status OPEN|IN_PROGRESS
    """

    permission_classes = [permissions.IsAuthenticated]
    throttle_classes = [SupportReopenThrottle]

    def post(self, request, pk):
        comment = request.data.get("comment") or request.data.get("reason") or ""
        target_status = request.data.get("target_status") or request.data.get("status")
        try:
            ticket = reopen_ticket(
                ticket_id=pk,
                actor=request.user,
                comment=comment,
                target_status=target_status,
            )
        except TicketWorkflowError as exc:
            return _workflow_err(exc)
        return _ok("Ticket reopened", _detail_payload(ticket, request))


def _support_report_queryset(request):
    qs = SupportTicket.objects.select_related(
        "category", "organisation", "site", "created_by", "current_assignee"
    ).order_by("-created_on")
    return filter_support_tickets_queryset(
        qs, request.query_params, actor=request.user, request=request
    )


class SupportTicketReportView(APIView):
    """GET /support/reports/ — filtered ticket preview for SUPPORT Reports tab."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        qs = _support_report_queryset(request)
        page_qs, pagination = paginate_queryset(qs, request, default_page_size=50)
        ser = SupportTicketListSerializer(
            page_qs, many=True, context={"request": request}
        )
        return _ok("Support ticket report fetched", ser.data, pagination=pagination)


class SupportTicketExportExcelView(APIView):
    """GET /support/reports/export-excel/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        qs = _support_report_queryset(request)
        return generate_support_excel(qs, request)


class SupportTicketExportPdfView(APIView):
    """GET /support/reports/export-pdf/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_support_user(request.user):
            return _err("SUPPORT role required.", code=status.HTTP_403_FORBIDDEN)
        qs = _support_report_queryset(request)
        try:
            content, filename = generate_support_pdf(qs, request)
        except ImportError as exc:
            return _err(str(exc), code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        response = HttpResponse(content, content_type="application/pdf")
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        return response


class SupportUserListCreateView(APIView):
    """
    GET/POST /support/support-users/
    Super Admin only — manage SUPPORT role users (location always null/global).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not is_super_admin_user(request.user):
            return _err("Super Admin required.", code=status.HTTP_403_FORBIDDEN)
        include_inactive = (
            request.query_params.get("include_inactive") or "1"
        ).strip().lower() not in ("0", "false", "no")
        users = list_support_users(include_inactive=include_inactive)
        page_qs, pagination = paginate_queryset(users, request)
        return _ok(
            "Support users fetched",
            SupportUserSerializer(
                page_qs, many=True, context={"request": request}
            ).data,
            pagination=pagination,
        )

    def post(self, request):
        if not is_super_admin_user(request.user):
            return _err("Super Admin required.", code=status.HTTP_403_FORBIDDEN)
        try:
            user = create_support_user(
                actor=request.user,
                name=request.data.get("name"),
                email=request.data.get("email"),
                password=request.data.get("password"),
                timezone=request.data.get("timezone"),
            )
        except TicketWorkflowError as exc:
            return _workflow_err(exc)
        return _ok(
            "Support user created",
            SupportUserSerializer(user, context={"request": request}).data,
            code=status.HTTP_201_CREATED,
        )


class SupportUserDetailView(APIView):
    """PATCH /support/support-users/<id>/ — Super Admin edit name/email/password/is_active."""

    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, pk):
        if not is_super_admin_user(request.user):
            return _err("Super Admin required.", code=status.HTTP_403_FORBIDDEN)
        try:
            user = update_support_user(
                actor=request.user,
                user_id=pk,
                name=request.data.get("name", None)
                if "name" in request.data
                else None,
                email=request.data.get("email", None)
                if "email" in request.data
                else None,
                password=request.data.get("password") or None,
                is_active=request.data.get("is_active")
                if "is_active" in request.data
                else None,
                timezone=request.data.get("timezone")
                if "timezone" in request.data
                else None,
            )
        except TicketWorkflowError as exc:
            return _workflow_err(exc)
        return _ok(
            "Support user updated",
            SupportUserSerializer(user, context={"request": request}).data,
        )
