from rest_framework import viewsets
from .models import Location, Shift, Assignment, Checkpoint 
from checkin.models import CheckIn
from .serializers import LocationSerializer, ShiftSerializer, AssignmentSerializer, CheckpointSerializer
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.timezone import now
from uuid import UUID
from .models import SiteSetting
from .serializers import SiteSettingSerializer
from django.core.exceptions import ValidationError

class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.objects.all()
    serializer_class = LocationSerializer
    def get_queryset(self):
        """
        Optionally filter locations by `name` and `address` query params.
        """
        queryset = Location.objects.filter(is_deleted=False)
        name = self.request.query_params.get('name', None)
        address = self.request.query_params.get('address', None)

        if name:
            queryset = queryset.filter(name__icontains=name)
        if address:
            queryset = queryset.filter(address__icontains=address)

        return queryset

        
class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer
    @action(detail=False, methods=['get'], url_path='by-location/(?P<location_id>[^/.]+)')
    def by_location(self, request, location_id=None):
        shifts = Shift.objects.filter(location_id=location_id, is_deleted=False)
        serializer = self.get_serializer(shifts, many=True)
        return Response(serializer.data)    

#class AssignmentViewSet(viewsets.ModelViewSet):
#    queryset = Assignment.objects.all()
#    serializer_class = AssignmentSerializer

class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer

    def clean(self):
        super().clean()

        # Validate dates
        if self.end_date < self.start_date:
            raise ValidationError("End date cannot be before start date.")

        # Check for overlapping assignments
        overlapping = Assignment.objects.filter(
            guard=self.guard,
            start_date__lte=self.end_date,
            end_date__gte=self.start_date,
        ).exclude(id=self.id)  # exclude self for updates

        if overlapping.exists():
            raise ValidationError(
                f"Guard {self.guard} already has an overlapping assignment."
            )


    @action(detail=False, methods=['get'], url_path='upcoming-checkpoints/(?P<user_id>[^/.]+)')
    def upcoming_checkpoints(self, request, user_id=None):
        today = now().date()
#        assignments = Assignment.objects.filter(guard_id=user_id, assigned_date=today)
#        from django.db.models import Q

#today = now().date()
        assignments = Assignment.objects.filter(guard_id=user_id,start_date__lte=today,end_date__gte=today)
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
                    'assign_id':assignment.id,
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
    @action(detail=False, methods=['get'], url_path='by-guard/(?P<guard_id>[^/.]+)')
    def by_guard(self, request, guard_id=None):
        assignments = Assignment.objects.filter(guard_id=guard_id, is_deleted=False)
        serializer = self.get_serializer(assignments, many=True)
        return Response(serializer.data)


class CheckpointViewSet(viewsets.ModelViewSet):
    queryset = Checkpoint.objects.all()
    serializer_class = CheckpointSerializer

    @action(detail=False, methods=['get'], url_path='by-location/(?P<location_id>[^/.]+)')
    def by_location(self, request, location_id=None):
        today = now().date()
        checkpoints = Checkpoint.objects.filter(
            location=location_id,
             is_deleted=False
        )
        serializer = self.get_serializer(checkpoints, many=True)
        return Response(serializer.data)    


class SiteSettingViewSet(viewsets.ModelViewSet):
    queryset = SiteSetting.objects.all()
    serializer_class = SiteSettingSerializer

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        instance.delete(user=request.user)  # soft delete
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        """Create multiple site settings"""
        serializer = SiteSettingSerializer(data=request.data, many=True)
        serializer.is_valid(raise_exception=True)
        serializer.save(created_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        """Update multiple site settings"""
        data = request.data
        if not isinstance(data, list):
            return Response({'detail': 'Expected a list of objects.'}, status=status.HTTP_400_BAD_REQUEST)

        updated_items = []
        for item in data:
            try:
                instance = SiteSetting.objects.get(id=item.get('id'))
            except SiteSetting.DoesNotExist:
                continue  # skip if not found

            serializer = SiteSettingSerializer(instance, data=item, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save(modified_by=request.user)
            updated_items.append(serializer.data)

        return Response(updated_items, status=status.HTTP_200_OK)

    @action(detail=False, methods=['post'], url_path='bulk-delete')
    def bulk_delete(self, request):
        """Soft delete multiple site settings by ID"""
        ids = request.data.get('ids', [])
        if not isinstance(ids, list):
            return Response({'detail': 'Expected a list of UUIDs in "ids".'}, status=status.HTTP_400_BAD_REQUEST)

        settings = SiteSetting.objects.filter(id__in=ids)
        for setting in settings:
            setting.delete(user=request.user)
        return Response(status=status.HTTP_204_NO_CONTENT)
