"""v5 user serializers — live UserSerializer/UserListSerializer plus site fields."""

import json
import uuid

from rest_framework import serializers

from .serializers import UserListSerializer, UserSerializer
from .site_access import assigned_sites_payload, site_ids_payload, validate_and_sync_user_sites


def _parse_site_ids(value):
    if value is None or value == "":
        return []
    if isinstance(value, str):
        raw = value.strip()
        if raw.startswith("["):
            try:
                value = json.loads(raw)
            except json.JSONDecodeError:
                value = [p.strip() for p in raw.split(",") if p.strip()]
        else:
            value = [p.strip() for p in raw.split(",") if p.strip()]
    if not isinstance(value, (list, tuple)):
        value = [value]
    out = []
    for item in value:
        if item in (None, ""):
            continue
        try:
            out.append(uuid.UUID(str(item)))
        except (TypeError, ValueError, AttributeError):
            raise serializers.ValidationError("Each site_ids value must be a UUID.")
    return out


def _parse_bool(value, default=None):
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


class UserSerializerV5(UserSerializer):
    site_ids = serializers.ListField(
        child=serializers.UUIDField(),
        write_only=True,
        required=False,
        allow_empty=True,
    )
    all_org_sites = serializers.BooleanField(required=False)
    assigned_sites = serializers.SerializerMethodField(read_only=True)

    class Meta(UserSerializer.Meta):
        fields = UserSerializer.Meta.fields + [
            "site_ids",
            "all_org_sites",
            "assigned_sites",
        ]

    def to_internal_value(self, data):
        mutable = {}
        if hasattr(data, "lists"):
            for key in data.keys():
                mutable[key] = data.get(key)
            listed = data.getlist("site_ids") or data.getlist("site_ids[]")
            if listed:
                if len(listed) == 1 and str(listed[0]).strip().startswith("["):
                    mutable["site_ids"] = listed[0]
                else:
                    mutable["site_ids"] = listed
        else:
            mutable = dict(data)

        if "site_ids" in mutable or "site_ids[]" in mutable:
            raw = mutable.get("site_ids", mutable.get("site_ids[]"))
            mutable["site_ids"] = _parse_site_ids(raw)

        if "all_org_sites" in mutable:
            mutable["all_org_sites"] = _parse_bool(mutable.get("all_org_sites"), False)

        return super().to_internal_value(mutable)

    def get_assigned_sites(self, obj):
        return assigned_sites_payload(obj)

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["all_org_sites"] = bool(getattr(instance, "all_org_sites", False))
        data["site_ids"] = site_ids_payload(instance)
        data["assigned_sites"] = assigned_sites_payload(instance)
        return data

    def create(self, validated_data):
        site_ids = validated_data.pop("site_ids", None)
        all_org_sites = validated_data.pop("all_org_sites", None)
        role = (validated_data.get("role") or "").lower()
        if all_org_sites is None and role == "admin":
            all_org_sites = True
        if all_org_sites is None:
            all_org_sites = False
        validated_data["all_org_sites"] = all_org_sites
        user = super().create(validated_data)
        validate_and_sync_user_sites(
            user,
            site_ids=site_ids if site_ids is not None else [],
            all_org_sites=all_org_sites,
        )
        return user

    def update(self, instance, validated_data):
        site_ids = validated_data.pop("site_ids", None)
        all_org_sites = validated_data.pop("all_org_sites", None)
        user = super().update(instance, validated_data)
        if site_ids is not None or all_org_sites is not None:
            validate_and_sync_user_sites(user, site_ids=site_ids, all_org_sites=all_org_sites)
        return user


class UserListSerializerV5(UserListSerializer):
    all_org_sites = serializers.BooleanField(read_only=True)
    site_ids = serializers.SerializerMethodField()
    assigned_sites = serializers.SerializerMethodField()

    class Meta(UserListSerializer.Meta):
        fields = UserListSerializer.Meta.fields + [
            "all_org_sites",
            "site_ids",
            "assigned_sites",
        ]

    def get_site_ids(self, obj):
        return site_ids_payload(obj)

    def get_assigned_sites(self, obj):
        return assigned_sites_payload(obj)
