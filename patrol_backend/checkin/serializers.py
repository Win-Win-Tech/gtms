from rest_framework import serializers
from .models import CheckIn
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

class CheckInSerializer(serializers.ModelSerializer):
    class Meta:
        model = CheckIn
        fields = '__all__'
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            user_tz = get_user_timezone_from_request(request)
            
            # Convert timestamp to user timezone
            if instance.timestamp:
                data['timestamp'] = to_user_timezone(instance.timestamp, user_tz).isoformat()
        
        return data
