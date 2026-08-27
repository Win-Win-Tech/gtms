import re

from rest_framework import serializers

from .models import VisitorLookupOption


def _slugify_code(label: str) -> str:
    text = (label or "").strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    return text.strip("_")[:32]


class VisitorLookupOptionSerializer(serializers.ModelSerializer):
    # Optional on create — auto-generated from label when omitted.
    code = serializers.CharField(required=False, allow_blank=True, max_length=32)

    class Meta:
        model = VisitorLookupOption
        fields = [
            "id",
            "kind",
            "code",
            "label",
            "location",
            "is_default",
            "sort_order",
            "is_active",
        ]
        read_only_fields = ["id", "location", "is_default"]

    def validate_kind(self, value):
        allowed = {c[0] for c in VisitorLookupOption.KIND_CHOICES}
        if value not in allowed:
            raise serializers.ValidationError(f"kind must be one of: {', '.join(sorted(allowed))}")
        return value

    def validate(self, attrs):
        # Code is immutable after create
        if self.instance:
            attrs.pop("code", None)
            return attrs

        label = attrs.get("label") or ""
        code = (attrs.get("code") or "").strip().lower()
        if not code:
            code = _slugify_code(label)
        if not code:
            raise serializers.ValidationError({"label": "Label is required to generate a code."})
        attrs["code"] = code
        return attrs
