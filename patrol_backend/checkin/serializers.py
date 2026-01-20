from rest_framework import serializers
from .models import CheckIn
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

class CheckInSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckIn
        fields = '__all__'
        extra_kwargs = {
            'expected_checkpoint_time': {'required': False}
        }
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            # Use check-in's location timezone if available
            # Get location from shift or guard
            location_id = None
            if instance.shift and instance.shift.location:
                location_id = str(instance.shift.location.id)
            elif instance.guard and instance.guard.location:
                location_id = str(instance.guard.location.id)
            
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            
            # Convert timestamp to user timezone
            if instance.timestamp:
                data['timestamp'] = to_user_timezone(instance.timestamp, user_tz).isoformat()
        
        return data
