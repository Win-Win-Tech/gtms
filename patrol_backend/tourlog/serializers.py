from rest_framework import serializers
from .models import TourLog, MissedCheckpoint, IncidentReport
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

class TourLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TourLog
        fields = '__all__'
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            # Use guard's location timezone if available
            location_id = str(instance.guard.location.id) if instance.guard and instance.guard.location else None
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            
            # Convert datetime fields to user timezone
            if instance.start_time:
                data['start_time'] = to_user_timezone(instance.start_time, user_tz).isoformat()
            
            if instance.end_time:
                data['end_time'] = to_user_timezone(instance.end_time, user_tz).isoformat()
        
        return data

class MissedCheckpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = MissedCheckpoint
        fields = '__all__'
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            # Use guard's location timezone if available
            location_id = str(instance.guard.location.id) if instance.guard and instance.guard.location else None
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            
            # Convert datetime fields to user timezone
            if instance.expected_time:
                data['expected_time'] = to_user_timezone(instance.expected_time, user_tz).isoformat()
            
            if instance.detected_at:
                data['detected_at'] = to_user_timezone(instance.detected_at, user_tz).isoformat()
        
        return data

class IncidentReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncidentReport
        fields = '__all__'
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            # Use guard's location timezone if available
            location_id = str(instance.guard.location.id) if instance.guard and instance.guard.location else None
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            
            # Convert datetime fields to user timezone
            if instance.timestamp:
                data['timestamp'] = to_user_timezone(instance.timestamp, user_tz).isoformat()
        
        return data
