from rest_framework import serializers
from .models import incidentreport
from django.contrib.auth import get_user_model

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


    # class Meta:
    #     model = incidentreport
    #     fields = '__all__'
    #     read_only_fields = ['ticket_number', 'status', 'created_on', 'assigned_on', 'resolved_on']
