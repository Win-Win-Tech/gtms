from rest_framework import viewsets
from .models import TourLog, MissedCheckpoint, IncidentReport
from .serializers import TourLogSerializer, MissedCheckpointSerializer, IncidentReportSerializer

class TourLogViewSet(viewsets.ModelViewSet):
    queryset = TourLog.objects.all()
    serializer_class = TourLogSerializer

class MissedCheckpointViewSet(viewsets.ModelViewSet):
    queryset = MissedCheckpoint.objects.all()
    serializer_class = MissedCheckpointSerializer

class IncidentReportViewSet(viewsets.ModelViewSet):
    queryset = IncidentReport.objects.all()
    serializer_class = IncidentReportSerializer
