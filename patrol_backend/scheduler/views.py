from rest_framework import viewsets, status
from .models import Location, Shift, Assignment, Checkpoint, SiteSetting
from checkin.models import CheckIn
from .serializers import (
    LocationSerializer,
    ShiftSerializer,
    AssignmentSerializer,
    CheckpointSerializer,
    SiteSettingSerializer
)
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.timezone import now, make_aware
from uuid import UUID
from django.core.exceptions import ValidationError
from datetime import datetime, timedelta
import pytz
import logging

logger = logging.getLogger(__name__)

class LocationViewSet(viewsets.ModelViewSet):
    queryset = Location.objects.all()
    serializer_class = LocationSerializer

    def get_queryset(self):
        try:
            queryset = Location.objects.filter(is_deleted=False)
            name = self.request.query_params.get('name')
            address = self.request.query_params.get('address')

            if name:
                queryset = queryset.filter(name__icontains=name)
            if address:
                queryset = queryset.filter(address__icontains=address)

            return queryset
        except Exception as e:
            logger.error(f"Error filtering locations: {e}", exc_info=True)
            return Location.objects.none()


class ShiftViewSet(viewsets.ModelViewSet):
    queryset = Shift.objects.all()
    serializer_class = ShiftSerializer

    @action(detail=False, methods=['get'], url_path='by-location/(?P<location_id>[^/.]+)')
    def by_location(self, request, location_id=None):
        try:
            shifts = Shift.objects.filter(location_id=location_id, is_deleted=False)
            serializer = self.get_serializer(shifts, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Error fetching shifts by location: {e}", exc_info=True)
            return Response({'error': 'Failed to retrieve shifts.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AssignmentViewSet(viewsets.ModelViewSet):
    queryset = Assignment.objects.all()
    serializer_class = AssignmentSerializer

    def clean(self):
        try:
            super().clean()
            if self.end_date < self.start_date:
                raise ValidationError("End date cannot be before start date.")

            overlapping = Assignment.objects.filter(
                guard=self.guard,
                start_date__lte=self.end_date,
                end_date__gte=self.start_date,
            ).exclude(id=self.id)

            if overlapping.exists():
                raise ValidationError(f"Guard {self.guard} already has an overlapping assignment.")
        except ValidationError as ve:
            raise ve
        except Exception as e:
            logger.error(f"Error during assignment validation: {e}", exc_info=True)
            raise ValidationError("Unexpected error during assignment validation.")

    @action(detail=False, methods=['get'], url_path='upcoming-checkpoints/(?P<user_id>[^/.]+)')
    def upcoming_checkpoints(self, request, user_id=None):
        try:
            today = now().date()
            assignments = Assignment.objects.filter(guard_id=user_id, start_date__lte=today, end_date__gte=today)
            result = []
            print("assignnnnnnnnn", assignments)
            for assignment in assignments:
                checkpoint_data = assignment.checkpoints
                completed_ids = set(
                    CheckIn.objects.filter(
                        guard_id=user_id,
                        shift_id=assignment.shift.id,
                        checkpoint_id__in=[cp['checkpoint_id'] for cp in checkpoint_data],
                        synced=True,
                        timestamp__date=today  # ✅ Only include check-ins from today
                    ).values_list('checkpoint_id', flat=True)
                )

                for cp in checkpoint_data:
                    checkpoint_id = cp['checkpoint_id']
                    checkpoint_obj = Checkpoint.objects.filter(id=checkpoint_id).first()

                    try:
                        checkpoint_time = datetime.strptime(cp['time'], '%H:%M').time()
                        checkpoint_datetime = make_aware(datetime.combine(today, checkpoint_time))
                        is_overdue = now() > checkpoint_datetime + timedelta(minutes=15)
                    except Exception:
                        is_overdue = False

                    is_checked_in = UUID(checkpoint_id) in completed_ids
                    status = 'completed' if is_checked_in or is_overdue else 'pending'
                    synced = True if is_checked_in else False if is_overdue else None

                    if is_overdue and not is_checked_in and checkpoint_obj:
                        try:
                            CheckIn.objects.create(
                                guard_id=user_id,
                                shift_id=assignment.shift.id,
                                checkpoint_id=checkpoint_id,
                                timestamp=now(),
                                latitude=checkpoint_obj.latitude,
                                longitude=checkpoint_obj.longitude,
                                synced=False
                            )
                            completed_ids.add(UUID(checkpoint_id))
                        except Exception as e:
                            logger.warning(f"Failed to auto-create missed check-in: {e}")

                    try:
                        # utc_naive = datetime.strptime(cp['time'], '%H:%M')
                        # utc_aware = pytz.utc.localize(utc_naive)
                        # ist_time = utc_aware.astimezone(pytz.timezone('Asia/Kolkata'))
                        # ist_time = utc_aware.astimezone(pytz.timezone('Asia/Kolkata'))
                        ist_time = cp['time']
                    except Exception:
                        ist_time = cp['time']

                    checkpoint_info = {
                        'assign_id': assignment.id,
                        'checkpoint_id': checkpoint_id,
                        'label': checkpoint_obj.label if checkpoint_obj else '',
                        'time': ist_time,
                        'lat': checkpoint_obj.latitude if checkpoint_obj else '',
                        'lon': checkpoint_obj.longitude if checkpoint_obj else '',
                        'qr': checkpoint_obj.data if checkpoint_obj else '',
                        'shift_id': assignment.shift.id,
                        'status': status
                    }

                    if synced is not None:
                        checkpoint_info['synced'] = synced

                    result.append(checkpoint_info)

            return Response(result)
        except Exception as e:
            logger.error(f"Error fetching upcoming checkpoints: {e}", exc_info=True)
            return Response({'error': 'Failed to retrieve upcoming checkpoints.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='by-guard/(?P<guard_id>[^/.]+)')
    def by_guard(self, request, guard_id=None):
        try:
            assignments = Assignment.objects.filter(guard_id=guard_id, is_deleted=False)
            serializer = self.get_serializer(assignments, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Error fetching assignments by guard: {e}", exc_info=True)
            return Response({'error': 'Failed to retrieve assignments.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CheckpointViewSet(viewsets.ModelViewSet):
    queryset = Checkpoint.objects.all()
    serializer_class = CheckpointSerializer

    @action(detail=False, methods=['get'], url_path='by-location/(?P<location_id>[^/.]+)')
    def by_location(self, request, location_id=None):
        try:
            checkpoints = Checkpoint.objects.filter(location=location_id, is_deleted=False)
            serializer = self.get_serializer(checkpoints, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Error fetching checkpoints by location: {e}", exc_info=True)
            return Response({'error': 'Failed to retrieve checkpoints.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class SiteSettingViewSet(viewsets.ModelViewSet):
    queryset = SiteSetting.objects.all()
    serializer_class = SiteSettingSerializer

    def destroy(self, request, *args, **kwargs):
        try:
            instance = self.get_object()
            instance.delete(user=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            logger.error(f"Error deleting site setting: {e}", exc_info=True)
            return Response({'error': 'Failed to delete site setting.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        try:
            serializer = SiteSettingSerializer(data=request.data, many=True)
            serializer.is_valid(raise_exception=True)
            serializer.save(created_by=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except ValidationError as ve:
            return Response({'error': ve.message_dict}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"Error during bulk create: {e}", exc_info=True)
            return Response({'error': 'Failed to create site settings.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['patch'], url_path='bulk-update')
    def bulk_update(self, request):
        data = request.data
        if not isinstance(data, list):
            return Response({'detail': 'Expected a list of objects.'}, status=status.HTTP_400_BAD_REQUEST)

        updated_items = []
        try:
            for item in data:
                try:
                    instance = SiteSetting.objects.get(id=item.get('id'))
                except SiteSetting.DoesNotExist:
                    continue

                serializer = SiteSettingSerializer(instance, data=item, partial=True)
                serializer.is_valid(raise_exception=True)
                serializer.save(modified_by=request.user)
                updated_items.append(serializer.data)

            return Response(updated_items, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Error during bulk update: {e}", exc_info=True)
            return Response({'error': 'Failed to update site settings.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['post'], url_path='bulk-delete')
    def bulk_delete(self, request):
        ids = request.data.get('ids', [])
        if not isinstance(ids, list):
           return Response({'detail': 'Expected a list of UUIDs in "ids".'}, status=status.HTTP_400_BAD_REQUEST)
