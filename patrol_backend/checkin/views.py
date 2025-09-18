from rest_framework import viewsets
from .models import CheckIn
from .serializers import CheckInSerializer

class CheckInViewSet(viewsets.ModelViewSet):
    queryset = CheckIn.objects.all()
    serializer_class = CheckInSerializer
