from rest_framework import serializers
from .models import TourLog, MissedCheckpoint, IncidentReport

class TourLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = TourLog
        fields = '__all__'

class MissedCheckpointSerializer(serializers.ModelSerializer):
    class Meta:
        model = MissedCheckpoint
        fields = '__all__'

class IncidentReportSerializer(serializers.ModelSerializer):
    class Meta:
        model = IncidentReport
        fields = '__all__'
