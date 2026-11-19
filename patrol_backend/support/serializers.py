from rest_framework import serializers

from .models import (
    SupportTicket,
    TicketActivityLog,
    TicketAttachment,
    TicketCategory,
    TicketComment,
)
from .permissions import is_support_user
from .services import TicketWorkflowError, compute_ticket_durations, create_ticket
from .sla import sla_hours_for_priority, ticket_is_overdue


class TicketCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TicketCategory
        fields = ["id", "name", "is_active", "sort_order"]


class SupportAgentSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField(allow_null=True)


class SupportUserSerializer(serializers.Serializer):
    """SUPPORT-role users for Super Admin management (not the assign-agent picker)."""

    id = serializers.UUIDField()
    name = serializers.CharField()
    email = serializers.EmailField(allow_null=True)
    role = serializers.CharField()
    is_active = serializers.BooleanField()
    created_on = serializers.DateTimeField(required=False, allow_null=True)
    modified_on = serializers.DateTimeField(required=False, allow_null=True)


class TicketAttachmentSerializer(serializers.ModelSerializer):
    """Private files: expose authenticated download path, not a public MEDIA URL."""

    download_url = serializers.SerializerMethodField()
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = TicketAttachment
        fields = [
            "id",
            "file_name",
            "mime_type",
            "size_bytes",
            "kind",
            "purpose",
            "created_on",
            "comment",
            "uploaded_by",
            "uploaded_by_name",
            "download_url",
        ]
        read_only_fields = fields

    def get_download_url(self, obj):
        return f"/support/tickets/{obj.ticket_id}/attachments/{obj.id}/download/"

    def get_uploaded_by_name(self, obj):
        u = obj.uploaded_by
        if not u:
            return None
        return u.name or u.email


class TicketCommentSerializer(serializers.ModelSerializer):
    author_name = serializers.SerializerMethodField()

    class Meta:
        model = TicketComment
        fields = [
            "id",
            "body",
            "visibility",
            "author",
            "author_name",
            "created_on",
        ]
        read_only_fields = ["id", "author", "author_name", "created_on"]

    def get_author_name(self, obj):
        if not obj.author:
            return None
        return obj.author.name or obj.author.email


class TicketActivityLogSerializer(serializers.ModelSerializer):
    actor_name = serializers.SerializerMethodField()

    class Meta:
        model = TicketActivityLog
        fields = [
            "id",
            "action",
            "old_value",
            "new_value",
            "comment",
            "metadata_json",
            "is_public",
            "actor",
            "actor_name",
            "created_on",
        ]

    def get_actor_name(self, obj):
        if not obj.actor:
            return None
        return obj.actor.name or obj.actor.email


class SupportTicketListSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source="category.name", read_only=True)
    organisation_name = serializers.CharField(source="organisation.name", read_only=True, default=None)
    site_name = serializers.CharField(source="site.name", read_only=True, default=None)
    created_by_name = serializers.SerializerMethodField()
    assignee_name = serializers.SerializerMethodField()
    is_overdue = serializers.SerializerMethodField()
    sla_hours = serializers.SerializerMethodField()

    class Meta:
        model = SupportTicket
        fields = [
            "id",
            "ticket_number",
            "subject",
            "priority",
            "status",
            "category",
            "category_name",
            "organisation",
            "organisation_name",
            "site",
            "site_name",
            "created_by",
            "created_by_name",
            "current_assignee",
            "assignee_name",
            "due_at",
            "is_overdue",
            "sla_hours",
            "created_on",
            "updated_on",
        ]

    def get_created_by_name(self, obj):
        u = obj.created_by
        return (u.name or u.email) if u else None

    def get_assignee_name(self, obj):
        u = obj.current_assignee
        return (u.name or u.email) if u else None

    def get_is_overdue(self, obj):
        return ticket_is_overdue(obj)

    def get_sla_hours(self, obj):
        return sla_hours_for_priority(obj.priority)


class SupportTicketDetailSerializer(SupportTicketListSerializer):
    description = serializers.CharField()
    comments = serializers.SerializerMethodField()
    activity = serializers.SerializerMethodField()
    durations = serializers.SerializerMethodField()
    attachments = TicketAttachmentSerializer(many=True, read_only=True)
    resolution_summary = serializers.CharField(read_only=True)
    invalidation_reason = serializers.CharField(read_only=True)

    class Meta(SupportTicketListSerializer.Meta):
        fields = SupportTicketListSerializer.Meta.fields + [
            "description",
            "comments",
            "activity",
            "durations",
            "attachments",
            "resolution_summary",
            "invalidation_reason",
            "resolved_at",
            "closed_at",
        ]

    def _viewer_is_support(self):
        request = self.context.get("request")
        return bool(request and is_support_user(request.user))

    def _filtered_activity_qs(self, obj):
        qs = obj.activity_logs.select_related("actor").all()
        if not self._viewer_is_support():
            qs = qs.filter(is_public=True)
        return qs

    def get_comments(self, obj):
        qs = obj.comments.select_related("author").all()
        if not self._viewer_is_support():
            qs = qs.filter(visibility=TicketComment.Visibility.PUBLIC)
        return TicketCommentSerializer(qs, many=True).data

    def get_activity(self, obj):
        return TicketActivityLogSerializer(self._filtered_activity_qs(obj), many=True).data

    def get_durations(self, obj):
        # Prefer already-prefetched activity; durations use public+internal timestamps
        # for accurate lifecycle (creators still only see public activity rows).
        activity_rows = list(obj.activity_logs.all())
        return compute_ticket_durations(obj, activity_rows=activity_rows)


class SupportTicketCreateSerializer(serializers.Serializer):
    subject = serializers.CharField(max_length=255)
    description = serializers.CharField()
    category_id = serializers.UUIDField()
    priority = serializers.ChoiceField(
        choices=SupportTicket.Priority.choices,
        default=SupportTicket.Priority.MEDIUM,
        required=False,
    )
    site_id = serializers.UUIDField(required=False, allow_null=True)
    due_at = serializers.DateTimeField(required=False, allow_null=True)
    idempotency_key = serializers.CharField(
        max_length=64, required=False, allow_blank=True, write_only=True
    )

    def validate_category_id(self, value):
        cat = TicketCategory.objects.filter(id=value, is_active=True).first()
        if not cat:
            raise serializers.ValidationError("Invalid or inactive category.")
        self.context["_category"] = cat
        return value

    def create(self, validated_data):
        request = self.context["request"]
        category = self.context["_category"]
        uploaded = self.context.get("uploaded_file")
        uploaded_files = self.context.get("uploaded_files")
        try:
            ticket = create_ticket(
                user=request.user,
                subject=validated_data["subject"],
                description=validated_data["description"],
                category=category,
                priority=validated_data.get("priority") or SupportTicket.Priority.MEDIUM,
                site_id=validated_data.get("site_id"),
                uploaded_file=uploaded,
                uploaded_files=uploaded_files,
                due_at=validated_data.get("due_at"),
            )
        except ValueError as e:
            raise serializers.ValidationError({"site_id": str(e)})
        except TicketWorkflowError as e:
            # Attachment validation on Raise Ticket (multipart media).
            field = "assets" if (uploaded_files or uploaded) else "file"
            raise serializers.ValidationError({field: e.message})
        return ticket
