from rest_framework import serializers

from .contact_details import entry_phone_number, entry_visitor_name
from .models import VisitorEntry
from .serializers import VisitorEntrySerializer, entry_needs_details


def _with_can_edit_details(fields):
    fields = list(fields)
    fields.insert(fields.index("needs_details") + 1, "can_edit_details")
    return fields


class VisitorEntrySerializerV5(VisitorEntrySerializer):
    # CCTV entries may carry their own name / phone for one visit.
    visitor_name = serializers.SerializerMethodField()
    phone_number = serializers.SerializerMethodField()
    can_edit_details = serializers.SerializerMethodField()

    class Meta(VisitorEntrySerializer.Meta):
        fields = _with_can_edit_details(VisitorEntrySerializer.Meta.fields)
        read_only_fields = fields

    def get_visitor_name(self, obj):
        return entry_visitor_name(obj)

    def get_phone_number(self, obj):
        return entry_phone_number(obj)

    def get_needs_details(self, obj):
        return entry_needs_details(obj, entry_contact=True)

    def get_can_edit_details(self, obj):
        return obj.entry_source == VisitorEntry.ENTRY_CCTV

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["site_id"] = str(instance.site_id) if instance.site_id else None
        data["site_name"] = instance.site.name if instance.site else None
        return data
