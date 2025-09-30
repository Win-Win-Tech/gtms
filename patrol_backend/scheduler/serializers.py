from rest_framework import serializers
from .models import Location, Shift, Assignment, Checkpoint,SiteSetting

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

