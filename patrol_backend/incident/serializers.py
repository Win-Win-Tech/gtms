from rest_framework import serializers
from .models import incidentreport
from django.contrib.auth import get_user_model

User = get_user_model()

class IncidentSerializer(serializers.ModelSerializer):
    created_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    assigned_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    assigned_to = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)
    resolved_by = serializers.PrimaryKeyRelatedField(queryset=User.objects.all(), required=False)

    class Meta:
        model = incidentreport
        fields = '__all__'
        read_only_fields = ['ticket_number', 'status', 'created_on', 'assigned_on', 'resolved_on']
