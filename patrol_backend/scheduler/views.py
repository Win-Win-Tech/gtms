from rest_framework import viewsets
from .models import Location, Shift, Assignment, Checkpoint 
from checkin.models import CheckIn
from .serializers import LocationSerializer, ShiftSerializer, AssignmentSerializer, CheckpointSerializer
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.timezone import now
from uuid import UUID

class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.objects.all()
    serializer_class = LocationSerializer

class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer

#class AssignmentViewSet(viewsets.ModelViewSet):
#    queryset = Assignment.objects.all()
#    serializer_class = AssignmentSerializer

class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer



    @action(detail=False, methods=['get'], url_path='upcoming-checkpoints/(?P<user_id>[^/.]+)')
    def upcoming_checkpoints(self, request, user_id=None):
        today = now().date()
#        assignments = Assignment.objects.filter(guard_id=user_id, assigned_date=today)
#        from django.db.models import Q

#today = now().date()
        assignments = Assignment.objects.filter(guard_id=user_id,shift__start_date__lte=today,shift__end_date__gte=today)
        result = []

        for assignment in assignments:
            checkpoint_data = assignment.checkpoints  # [{'checkpoint_id': str, 'time': 'HH:MM'}]

            completed_ids = set(
                CheckIn.objects.filter(
                    guard_id=user_id,
                    shift_id=assignment.shift.id,
                    checkpoint_id__in=[cp['checkpoint_id'] for cp in checkpoint_data]
                ).values_list('checkpoint_id', flat=True)
            )

            for cp in checkpoint_data:
                print(cp['checkpoint_id'])
                print (completed_ids)
                checkpoint_obj = Checkpoint.objects.filter(id=cp['checkpoint_id']).first()
                result.append({
                    'checkpoint_id': cp['checkpoint_id'],
                    'label': checkpoint_obj.label if checkpoint_obj else '',
                    'time': cp['time'],
                    'lat' : checkpoint_obj.latitude if checkpoint_obj else '',
                    'lon' : checkpoint_obj.longitude if checkpoint_obj else '',
                    'qr' : checkpoint_obj.data if checkpoint_obj else '',
                    'shift_id' : assignment.shift.id,
                #   'status': 'completed' if cp['checkpoint_id'] in completed_ids else 'pending'
                    'status' : 'completed' if UUID(cp['checkpoint_id']) in completed_ids else 'pending'
                })

        return Response(result)

class CheckpointViewSet(viewsets.ModelViewSet):
    queryset = Checkpoint.objects.all()
    serializer_class = CheckpointSerializer
from django.shortcuts import render

# Create your views here.
