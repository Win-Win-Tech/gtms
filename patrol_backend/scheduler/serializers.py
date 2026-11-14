from rest_framework import serializers
from .models import (
    Location,
    LocationSite,
    SiteCamera,
    Shift,
    Assignment,
    Checkpoint,
    SiteSetting,
    CheckpointTemplate,
    ChecklistItem,
    ChecklistTemplate,
    Vendor,
)
from django.utils.timezone import now
from uuid import UUID


class LocationSiteSerializer(serializers.ModelSerializer):
    labour_law_limit_enabled = serializers.BooleanField(required=False, allow_null=True)
    consecutive_work_limit_enabled = serializers.BooleanField(required=False, allow_null=True)
    max_consecutive_work_days = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=31
    )
    max_monthly_work_hours = serializers.IntegerField(
        required=False, allow_null=True, min_value=1, max_value=744
    )
    consecutive_work_inherited = serializers.SerializerMethodField()

    class Meta:
        model = LocationSite
        fields = [
            'id',
            'name',
            'latitude',
            'longitude',
            'is_active',
            'boundary_type',
            'boundary_radius_m',
            'boundary_polygon',
            'boundary_enabled',
            'breach_alerts_enabled',
            'location_missing_alerts_enabled',
            'labour_law_limit_enabled',
            'consecutive_work_limit_enabled',
            'max_consecutive_work_days',
            'max_monthly_work_hours',
            'consecutive_work_inherited',
        ]

    def _unpack_labour(self, cached, obj):
        if cached is not None:
            if len(cached) == 4:
                return cached
            enabled, max_days, inherited = cached
            return enabled, max_days, None, inherited
        from patrol_backend.utils.consecutive_work import resolve_labour_law_settings

        return resolve_labour_law_settings(getattr(obj, "location_id", None), obj.id)

    def get_consecutive_work_inherited(self, obj):
        cached = (self.context.get("consecutive_work_by_site") or {}).get(str(obj.id))
        return self._unpack_labour(cached, obj)[3]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        cached = (self.context.get("consecutive_work_by_site") or {}).get(str(instance.id))
        enabled, max_days, max_hours, inherited = self._unpack_labour(cached, instance)
        data["labour_law_limit_enabled"] = enabled
        data["consecutive_work_limit_enabled"] = enabled
        data["max_consecutive_work_days"] = max_days
        data["max_monthly_work_hours"] = max_hours
        data["consecutive_work_inherited"] = inherited
        return data

    def _pop_consecutive_fields(self, validated_data):
        initial = self.initial_data if self.initial_data is not None else {}
        enabled_explicit = "labour_law_limit_enabled" in initial or "consecutive_work_limit_enabled" in initial
        max_explicit = "max_consecutive_work_days" in initial
        hours_explicit = "max_monthly_work_hours" in initial
        enabled = validated_data.pop("labour_law_limit_enabled", None)
        if enabled is None:
            enabled = validated_data.pop("consecutive_work_limit_enabled", None)
        else:
            validated_data.pop("consecutive_work_limit_enabled", None)
        max_days = validated_data.pop("max_consecutive_work_days", None)
        max_hours = validated_data.pop("max_monthly_work_hours", None)
        return (
            enabled if enabled_explicit else None,
            max_days if max_explicit else None,
            max_hours if hours_explicit else None,
        )

    def create(self, validated_data):
        enabled, max_days, max_hours = self._pop_consecutive_fields(validated_data)
        site = super().create(validated_data)
        if enabled is not None or max_days is not None or max_hours is not None:
            from patrol_backend.utils.consecutive_work import upsert_site_consecutive_work_settings

            request = self.context.get("request")
            upsert_site_consecutive_work_settings(
                site,
                enabled=enabled,
                max_days=max_days,
                max_hours=max_hours,
                user=getattr(request, "user", None),
            )
        return site

    def update(self, instance, validated_data):
        enabled, max_days, max_hours = self._pop_consecutive_fields(validated_data)
        site = super().update(instance, validated_data)
        if enabled is not None or max_days is not None or max_hours is not None:
            from patrol_backend.utils.consecutive_work import upsert_site_consecutive_work_settings

            request = self.context.get("request")
            upsert_site_consecutive_work_settings(
                site,
                enabled=enabled,
                max_days=max_days,
                max_hours=max_hours,
                user=getattr(request, "user", None),
            )
        return site


class SiteCameraSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteCamera
        fields = [
            "id",
            "site",
            "name",
            "rtsp_url",
            "camera_type",
            "features",
            "direction",
            "gate_mode",
            "is_enabled",
            "sort_order",
            "stream_path",
            "anpr_geometry",
            "created_on",
            "modified_on",
        ]
        read_only_fields = ["id", "site", "created_on", "modified_on"]


class SiteCameraWriteSerializer(serializers.Serializer):
    id = serializers.UUIDField(required=False, allow_null=True)
    name = serializers.CharField(max_length=255)
    rtsp_url = serializers.CharField(max_length=1024)
    camera_type = serializers.ChoiceField(
        choices=SiteCamera.CameraType.choices,
        default=SiteCamera.CameraType.VEHICLE,
        required=False,
    )
    features = serializers.ListField(
        child=serializers.CharField(max_length=64),
        required=False,
        default=list,
    )
    direction = serializers.ChoiceField(
        choices=SiteCamera.Direction.choices,
        default=SiteCamera.Direction.TOGGLE,
        required=False,
    )
    gate_mode = serializers.ChoiceField(
        choices=SiteCamera.GateMode.choices,
        default=SiteCamera.GateMode.PARKED_TOGGLE,
        required=False,
    )
    is_enabled = serializers.BooleanField(default=True, required=False)
    sort_order = serializers.IntegerField(min_value=0, default=0, required=False)
    stream_path = serializers.CharField(max_length=255, required=False, allow_blank=True, default="")
    anpr_geometry = serializers.JSONField(required=False, default=dict)

    def validate(self, attrs):
        camera_type = str(attrs.get("camera_type") or SiteCamera.CameraType.VEHICLE)
        name = (attrs.get("name") or "").strip()
        allowed = SiteCamera.FEATURES_BY_TYPE.get(camera_type, ())
        labels = dict(SiteCamera.Feature.choices)
        type_label = dict(SiteCamera.CameraType.choices).get(camera_type, camera_type)

        features = []
        for raw in attrs.get("features") or []:
            feature = (raw or "").strip()
            if feature and feature not in features:
                features.append(feature)

        for feature in features:
            if feature not in allowed:
                raise serializers.ValidationError(
                    {"features": f"{labels.get(feature, feature)} is not available for {type_label} cameras (camera: {name})."}
                )
            if feature in SiteCamera.UNAVAILABLE_FEATURES:
                raise serializers.ValidationError(
                    {"features": f"{labels[feature]} is coming soon and cannot be enabled yet (camera: {name})."}
                )
        if allowed and not features:
            raise serializers.ValidationError(
                {"features": f"Select at least one feature for {type_label} cameras (camera: {name})."}
            )

        attrs["camera_type"] = camera_type
        attrs["features"] = features
        return attrs


class SiteCameraBulkSerializer(serializers.Serializer):
    cameras = SiteCameraWriteSerializer(many=True)


def _normalize_nested_site_payload(site_data):
    """Accept legacy keys (e.g. site_name) when creating sites with a new org."""
    data = dict(site_data)
    if not data.get('name') and data.get('site_name'):
        data['name'] = data.pop('site_name')
    else:
        data.pop('site_name', None)
    data.pop('id', None)
    allowed = set(LocationSiteSerializer.Meta.fields) - {'id'}
    return {key: value for key, value in data.items() if key in allowed}


class LocationSerializer(serializers.ModelSerializer):
    sites = LocationSiteSerializer(many=True, required=False)

    class Meta:
        model = Location
        fields = [
            'id', 'sites', 'name', 'address', 'latitude', 'longitude', 
            'is_qr_scan_enable', 'is_face_attendance_enabled', 'is_ai_extraction_enabled',
            'created_on', 'modified_on', 'is_deleted'
        ]

    def create(self, validated_data):
        sites_data = validated_data.pop('sites', [])
        location = Location.objects.create(**validated_data)
        for site_data in sites_data:
            LocationSite.objects.create(
                location=location,
                **_normalize_nested_site_payload(site_data),
            )
        return location

    def update(self, instance, validated_data):
        # Sites are managed via POST/PATCH /locations/{id}/sites/ only.
        # Legacy org PUT used to send `sites` and hard-delete all rows — ignore it.
        validated_data.pop('sites', None)
        return super().update(instance, validated_data)

class ShiftSerializer(serializers.ModelSerializer):
    # start_time = serializers.DateTimeField(format='%Y-%m-%d %H:%M:%S')
    # end_time = serializers.DateTimeField(format='%Y-%m-%d %H:%M:%S')

    start_time = serializers.TimeField(input_formats=['%I:%M %p'], format='%I:%M %p')
    end_time = serializers.TimeField(input_formats=['%I:%M %p'], format='%I:%M %p')
    checkpoint_template = serializers.PrimaryKeyRelatedField(
        queryset=CheckpointTemplate.objects.filter(is_deleted=False),
        required=False,
        allow_null=True
    )
    site_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        allow_empty=True,
        write_only=True,
    )
    site_names = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Shift
        fields = [
            'id',
            'name',
            'location',
            'start_time',
            'end_time',
            'is_default',
            'applies_to_all_sites',
            'checkpoint_template',
            'site_ids',
            'site_names',
            'created_on',
            'modified_on',
            'created_by',
            'modified_by',
            'is_deleted',
            'deleted_on',
            'deleted_by',
        ]
        read_only_fields = [
            'created_on',
            'modified_on',
            'created_by',
            'modified_by',
            'is_deleted',
            'deleted_on',
            'deleted_by',
            'site_names',
        ]

    def get_site_names(self, obj):
        if getattr(obj, 'applies_to_all_sites', True):
            return []
        return [s.name for s in obj.sites.all()]

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if getattr(instance, 'applies_to_all_sites', True):
            data['site_ids'] = []
        else:
            data['site_ids'] = [str(s.id) for s in instance.sites.all()]
        return data

    def validate(self, attrs):
        applies_all = attrs.get(
            'applies_to_all_sites',
            getattr(self.instance, 'applies_to_all_sites', True) if self.instance else True,
        )
        location = attrs.get('location') or (self.instance.location if self.instance else None)
        site_ids_provided = 'site_ids' in getattr(self, 'initial_data', {})
        site_ids = attrs.get('site_ids', None)

        if applies_all:
            attrs['applies_to_all_sites'] = True
            attrs['_sites'] = []
            return attrs

        if not site_ids_provided and self.instance is not None:
            return attrs

        if site_ids is None:
            site_ids = []
        if not site_ids:
            raise serializers.ValidationError(
                {'site_ids': 'Select at least one site when All sites is off.'}
            )
        if location is None:
            raise serializers.ValidationError({'location': 'Location is required.'})
        sites = list(LocationSite.objects.filter(id__in=site_ids, location=location))
        if len(sites) != len({str(sid) for sid in site_ids}):
            raise serializers.ValidationError(
                {'site_ids': 'One or more sites are invalid or not in this organisation.'}
            )
        attrs['_sites'] = sites
        return attrs

    def create(self, validated_data):
        sites = validated_data.pop('_sites', None)
        validated_data.pop('site_ids', None)
        applies_all = validated_data.get('applies_to_all_sites', True)
        shift = super().create(validated_data)
        if applies_all:
            shift.sites.clear()
        elif sites is not None:
            shift.sites.set(sites)
        return shift

    def update(self, instance, validated_data):
        sites = validated_data.pop('_sites', None)
        validated_data.pop('site_ids', None)
        shift = super().update(instance, validated_data)
        if sites is not None:
            if shift.applies_to_all_sites:
                shift.sites.clear()
            else:
                shift.sites.set(sites)
        elif shift.applies_to_all_sites:
            shift.sites.clear()
        return shift


# class AssignmentSerializer(serializers.ModelSerializer):
#     class Meta:
#         model = Assignment
#         fields = '__all__'

class AssignmentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Assignment
        fields = '__all__'
    # New fields for extended info
    shift_details = serializers.SerializerMethodField()
    checkpoint_details = serializers.SerializerMethodField()

    def get_shift_details(self, obj):
        if obj.shift:
            return {
                "id": str(obj.shift.id),
                "name": obj.shift.name,
                "start_time": obj.shift.start_time.strftime("%H:%M") if obj.shift.start_time else None,
                "end_time": obj.shift.end_time.strftime("%H:%M") if obj.shift.end_time else None
            }
        return None

    def get_checkpoint_details(self, obj):
        user_id = self.context.get('user_id')  # optional: only needed if you want status
        today = now().date()

        completed_ids = set()
        if user_id:
            checkpoints = obj.checkpoints or []
            completed_ids = set(
                CheckIn.objects.filter(
                    guard_id=user_id,
                    shift_id=obj.shift.id,
                    checkpoint_id__in=[cp['checkpoint_id'] for cp in checkpoints if cp.get('checkpoint_id')]
                ).values_list('checkpoint_id', flat=True)
            )

        result = []
        for cp in (obj.checkpoints or []):
            checkpoint_id = cp.get('checkpoint_id')
            if not checkpoint_id:
                continue
            checkpoint_obj = Checkpoint.objects.filter(id=checkpoint_id).first()
            checklist_template_id = cp.get('checklist_template_id')
            checklist_template_name = None
            if checklist_template_id:
                ct = ChecklistTemplate.objects.filter(id=checklist_template_id, is_deleted=False).first()
                if ct:
                    checklist_template_name = ct.name
            item = {
                'checkpoint_id': checkpoint_id,
                'label': checkpoint_obj.label if checkpoint_obj else '',
                'time': cp['time'],
                'lat': checkpoint_obj.latitude if checkpoint_obj else None,
                'lon': checkpoint_obj.longitude if checkpoint_obj else None,
                'qr': checkpoint_obj.data if checkpoint_obj else None,
                'status': 'pending'
            }
            try:
                item['status'] = 'completed' if UUID(str(checkpoint_id)) in completed_ids else 'pending'
            except (ValueError, TypeError):
                item['status'] = 'pending'
            if checklist_template_id:
                item['checklist_template_id'] = str(checklist_template_id)
            if checklist_template_name:
                item['checklist_template_name'] = checklist_template_name
            result.append(item)
        return result
        

#############Code-Starts#########

    #from datetime import datetime, timedelta

    # for cp in obj.checkpoints:
    #     checkpoint_obj = Checkpoint.objects.filter(id=cp['checkpoint_id']).first()

    #     # Parse checkpoint time (assumed format 'HH:MM')
    #     try:
    #         checkpoint_time = datetime.strptime(cp['time'], '%H:%M').time()
    #         checkpoint_datetime = datetime.combine(datetime.today(), checkpoint_time)
    #         is_overdue = datetime.now() > checkpoint_datetime + timedelta(minutes=15)
    #     except Exception:
    #         is_overdue = False  # fallback if time parsing fails

    #     is_completed = UUID(cp['checkpoint_id']) in completed_ids or is_overdue

    #     result.append({
    #         'checkpoint_id': cp['checkpoint_id'],
    #         'label': checkpoint_obj.label if checkpoint_obj else '',
    #         'time': cp['time'],
    #         'lat': checkpoint_obj.latitude if checkpoint_obj else None,
    #         'lon': checkpoint_obj.longitude if checkpoint_obj else None,
    #         'qr': checkpoint_obj.data if checkpoint_obj else None,
    #         'status': 'completed' if is_completed else 'pending'
    #     })

#############Code-Ends#########
    def validate(self, data):
        guard = data["guard"]
        start_date = data["start_date"]
        end_date = data["end_date"]

        overlapping = Assignment.objects.filter(
            guard=guard,
            start_date__lte=end_date,
            end_date__gte=start_date,
        )

        # Exclude self on update
        if self.instance:
            overlapping = overlapping.exclude(id=self.instance.id)

        if overlapping.exists():
            raise serializers.ValidationError(
                {"detail": f"Guard {guard} already has an overlapping assignment."}
            )

        return data

class CheckpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = Checkpoint
        fields = '__all__'

class SiteSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = SiteSetting
        fields = '__all__'        


class CheckpointTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckpointTemplate
        fields = '__all__'
        read_only_fields = ['id', 'created_on', 'modified_on', 'created_by', 'modified_by', 'deleted_on', 'deleted_by', 'is_deleted']


class ChecklistItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistItem
        fields = '__all__'


class ChecklistTemplateSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChecklistTemplate
        fields = '__all__'
        read_only_fields = ['id', 'created_on', 'modified_on', 'created_by', 'modified_by', 'deleted_on', 'deleted_by', 'is_deleted']


class VendorSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)
    location = serializers.PrimaryKeyRelatedField(queryset=Location.objects.all())
    users_count = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Vendor
        fields = [
            "id",
            "name",
            "location",
            "location_name",
            "contact_person",
            "phone_no",
            "email",
            "is_active",
            "users_count",
            "created_on",
            "modified_on",
        ]
        read_only_fields = ["id", "created_on", "modified_on"]

    def to_internal_value(self, data):
        # Backward compatibility in case a client passes location_id or locationId
        mutable = data.copy() if hasattr(data, 'copy') else dict(data)
        loc = mutable.get('location') or mutable.get('location_id') or mutable.get('locationId')
        if loc:
            mutable['location'] = loc
        mutable.pop('locationId', None)
        mutable.pop('location_id', None)
        return super().to_internal_value(mutable)

    def get_users_count(self, obj):
        return obj.users.filter(is_deleted=False).count()

