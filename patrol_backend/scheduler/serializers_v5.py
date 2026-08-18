"""v5 assignment serializers — live AssignmentSerializer plus posted site_id."""

from rest_framework import serializers

from scheduler.daily_site import clear_daily_sites, fill_daily_sites, posted_site_for_assignment
from scheduler.serializers import AssignmentSerializer


class AssignmentSerializerV5(AssignmentSerializer):
    site_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    date = serializers.DateField(write_only=True, required=False, allow_null=True)
    posted_site_id = serializers.SerializerMethodField()
    posted_site_name = serializers.SerializerMethodField()

    def get_posted_site_id(self, obj):
        site_id, _ = self._posted(obj)
        return site_id

    def get_posted_site_name(self, obj):
        _, name = self._posted(obj)
        return name

    def validate(self, data):
        if self.instance:
            data.setdefault("guard", self.instance.guard)
            data.setdefault("start_date", self.instance.start_date)
            data.setdefault("end_date", self.instance.end_date)
        return super().validate(data)

    def _posted(self, obj):
        cache = getattr(self, "_posted_cache", None)
        if cache is None:
            self._posted_cache = {}
            cache = self._posted_cache
        key = obj.pk
        if key in cache:
            return cache[key]
        result = posted_site_for_assignment(obj)
        cache[key] = result
        return result

    def create(self, validated_data):
        site_id = validated_data.pop("site_id", None)
        validated_data.pop("date", None)
        assignment = super().create(validated_data)
        if site_id:
            request = self.context.get("request")
            fill_daily_sites(
                assignment,
                site_id,
                date=None,
                caller=getattr(request, "user", None),
            )
        return assignment

    def update(self, instance, validated_data):
        site_explicit = self.initial_data is not None and "site_id" in self.initial_data
        site_id = validated_data.pop("site_id", None)
        date = validated_data.pop("date", None)
        assignment = super().update(instance, validated_data)
        request = self.context.get("request")
        caller = getattr(request, "user", None)
        if site_id:
            fill_daily_sites(
                assignment,
                site_id,
                date=date,
                caller=caller,
            )
        elif site_explicit:
            clear_daily_sites(assignment, date=date)
        return assignment
