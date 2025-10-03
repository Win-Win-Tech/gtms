from rest_framework import serializers
from .models import Location, Shift, Assignment, Checkpoint,SiteSetting
from django.utils.timezone import now
from uuid import UUID

class LocationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Location
        fields = '__all__'

class ShiftSerializer(serializers.ModelSerializer):
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
            completed_ids = set(
                CheckIn.objects.filter(
                    guard_id=user_id,
                    shift_id=obj.shift.id,
                    checkpoint_id__in=[cp['checkpoint_id'] for cp in obj.checkpoints]
                ).values_list('checkpoint_id', flat=True)
            )

        result = []
        for cp in obj.checkpoints:
            checkpoint_obj = Checkpoint.objects.filter(id=cp['checkpoint_id']).first()
            result.append({
                'checkpoint_id': cp['checkpoint_id'],
                'label': checkpoint_obj.label if checkpoint_obj else '',
                'time': cp['time'],
                'lat': checkpoint_obj.latitude if checkpoint_obj else None,
                'lon': checkpoint_obj.longitude if checkpoint_obj else None,
                'qr': checkpoint_obj.data if checkpoint_obj else None,
                'status': 'completed' if UUID(cp['checkpoint_id']) in completed_ids else 'pending'
            })
        return result
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

