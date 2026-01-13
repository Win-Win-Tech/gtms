from rest_framework import viewsets
from .models import TourLog, MissedCheckpoint, IncidentReport
from .serializers import TourLogSerializer, MissedCheckpointSerializer, IncidentReportSerializer

class TourLogViewSet(viewsets.ModelViewSet):
    queryset = TourLog.objects.all()
    serializer_class = TourLogSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context for timezone conversion"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

class MissedCheckpointViewSet(viewsets.ModelViewSet):
    queryset = MissedCheckpoint.objects.all()
    serializer_class = MissedCheckpointSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context for timezone conversion"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context

class IncidentReportViewSet(viewsets.ModelViewSet):
    queryset = IncidentReport.objects.all()
    serializer_class = IncidentReportSerializer
    
    def get_serializer_context(self):
        """Add request to serializer context for timezone conversion"""
        context = super().get_serializer_context()
        context['request'] = self.request
        return context
