from rest_framework import serializers
from .models import Location, LocationSite, Shift, Assignment, Checkpoint, SiteSetting, CheckpointTemplate, ChecklistItem, ChecklistTemplate
from django.utils.timezone import now
from uuid import UUID

class LocationSiteSerializer(serializers.ModelSerializer):
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
        ]


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

    class Meta:
        model = Shift
        fields = '__all__'


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
