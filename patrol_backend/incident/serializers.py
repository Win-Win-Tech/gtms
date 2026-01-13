from rest_framework import serializers
from .models import incidentreport
from django.contrib.auth import get_user_model
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request, to_user_timezone

User = get_user_model()

class IncidentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    assigned_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    resolved_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)

    created_by_name = serializers.SerializerMethodField()
    assigned_by_name = serializers.SerializerMethodField()
    assigned_to_name = serializers.SerializerMethodField()
    resolved_by_name = serializers.SerializerMethodField()
    location_name = serializers.SerializerMethodField()
    checkpoint_name = serializers.SerializerMethodField()

    class Meta:
        model = incidentreport
        fields = '__all__'
        read_only_fields = [
            'ticket_number', 'status', 'created_on', 'assigned_on', 'resolved_on',
            'created_by_name', 'assigned_by_name', 'assigned_to_name', 'resolved_by_name',
            'location_name', 'checkpoint_name'
        ]

    def get_created_by_name(self, obj):
        return obj.created_by.name if obj.created_by else None

    def get_assigned_by_name(self, obj):
        return obj.assigned_by.name if obj.assigned_by else None

    def get_assigned_to_name(self, obj):
        return obj.assigned_to.name if obj.assigned_to else None

    def get_resolved_by_name(self, obj):
        return obj.resolved_by.name if obj.resolved_by else None

    def get_location_name(self, obj):
        return obj.location.name if obj.location else None

    def get_checkpoint_name(self, obj):
        return obj.checkpoint.label if obj.checkpoint else None
    
    def to_representation(self, instance):
        """Convert UTC datetimes to user timezone before serialization"""
        data = super().to_representation(instance)
        
        # Get user timezone from request context
        request = self.context.get('request')
        if request:
            # Use incident's location timezone if available, otherwise use user timezone
            # This ensures superadmins viewing multiple locations see times in each location's timezone
            location_id = str(instance.location.id) if instance.location else None
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            
            # Convert datetime fields to user timezone
            if instance.created_on:
                data['created_on'] = to_user_timezone(instance.created_on, user_tz).isoformat()
            
            if instance.assigned_on:
                data['assigned_on'] = to_user_timezone(instance.assigned_on, user_tz).isoformat()
            
            if instance.resolved_on:
                data['resolved_on'] = to_user_timezone(instance.resolved_on, user_tz).isoformat()
        
        return data


    # class Meta:
    #     model = incidentreport
    #     fields = '__all__'
    #     read_only_fields = ['ticket_number', 'status', 'created_on', 'assigned_on', 'resolved_on']
