from rest_framework import viewsets, status as http_status
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
from calendar import monthrange
from collections import defaultdict
from rest_framework.decorators import action
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from django.http import HttpResponse
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    get_user_now,
    combine_date_time_in_user_tz,
    to_user_timezone,
    convert_date_range_to_utc
)

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

    @action(detail=False, methods=['post'], url_path='bulk-create')
    def bulk_create(self, request):
        assignments_data = request.data

        if not isinstance(assignments_data, list):
            return Response({"error": "Expected a list of assignments"}, status=status.HTTP_400_BAD_REQUEST)

        created = []
        errors = []

        for idx, data in enumerate(assignments_data):
            serializer = self.get_serializer(data=data)
            if serializer.is_valid():
                serializer.save()
                created.append(serializer.data)
            else:
                errors.append({
                    "index": idx,
                    "errors": serializer.errors,
                    "data": data
                })

        if errors:
            return Response({
                "created": created,
                "errors": errors
            }, status=status.HTTP_207_MULTI_STATUS)

        return Response({"created": created}, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='upcoming-checkpoints/(?P<user_id>[^/.]+)')
    def upcoming_checkpoints(self, request, user_id=None):
        try:
            # Get user's timezone
            user_tz = get_user_timezone_from_request(request)
            today = get_user_today(user_tz)
            
            
            assignments = Assignment.objects.filter(guard_id=user_id, start_date__lte=today, end_date__gte=today)
            result = []
            
            # Convert today to UTC date range for database queries
            start_utc, end_utc = convert_date_range_to_utc(today, today, user_tz)
            
            
            for assignment in assignments:
                checkpoint_data = assignment.checkpoints
                shift = assignment.shift
                is_overnight = shift.end_time <= shift.start_time
                
                
                completed_ids = set(
                    CheckIn.objects.filter(
                        guard_id=user_id,
                        shift_id=assignment.shift.id,
                        checkpoint_id__in=[cp['checkpoint_id'] for cp in checkpoint_data],
                        synced=True,
                        timestamp__gte=start_utc,
                        timestamp__lt=end_utc + timedelta(days=1)  # Use UTC range for query
                    ).values_list('checkpoint_id', flat=True)
                )

                for cp in checkpoint_data:
                    checkpoint_id = cp['checkpoint_id']
                    checkpoint_obj = Checkpoint.objects.filter(id=checkpoint_id).first()

                    try:
                        checkpoint_time = datetime.strptime(cp['time'], '%H:%M').time()
                        
                        # Get current time in user timezone (needed for shift instance determination)
                        from patrol_backend.utils.timezone_utils import get_user_now
                        user_now = get_user_now(user_tz)
                        current_time = user_now.time()
                        
                        # Determine which calendar date this checkpoint occurs on
                        # For overnight shifts: need to determine which shift instance is active
                        if is_overnight:
                            if checkpoint_time >= shift.start_time:
                                # Checkpoint is before midnight - belongs to today's shift
                                checkpoint_date = today
                            else:
                                # Checkpoint is after midnight - need to determine which shift instance
                                # If current time < shift.end_time: We're still in yesterday's shift
                                #   → Checkpoint belongs to yesterday's shift but occurs on today
                                # If current time >= shift.end_time: Yesterday's shift ended
                                #   → Checkpoint belongs to today's shift and occurs tomorrow
                                if current_time < shift.end_time:
                                    # Still in yesterday's shift - checkpoint occurs today
                                    checkpoint_date = today
                                else:
                                    # Past yesterday's shift end - this is tomorrow's checkpoint from today's shift
                                    checkpoint_date = today + timedelta(days=1)
                                    # Only include if assignment is still active on that date
                                    if not (assignment.start_date <= checkpoint_date <= assignment.end_date):
                                        continue  # Skip if assignment not active on that date
                        else:
                            # Normal shift - checkpoint is on the same day
                            checkpoint_date = today
                        
                        # Combine date and time in user timezone, then convert to UTC for comparison
                        checkpoint_datetime_user = combine_date_time_in_user_tz(checkpoint_date, checkpoint_time, user_tz)
                        checkpoint_datetime_user = checkpoint_datetime_user.astimezone(user_tz)
                        
                        # Check if overdue (in user timezone)
                        is_overdue = user_now > checkpoint_datetime_user + timedelta(minutes=15)
                    except Exception as e:
                        logger.warning(f"[UPCOMING_CHECKPOINTS_API] Error processing checkpoint time: {e}")
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

            logger.info(f"[UPCOMING_CHECKPOINTS_API] Returned {len(result)} checkpoints for user {user_id}")
            return Response(result)
        except Exception as e:
            logger.error(f"[UPCOMING_CHECKPOINTS_API] Error: {str(e)}", exc_info=True)
            return Response({'error': 'Failed to retrieve upcoming checkpoints.'}, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='by-guard/(?P<guard_id>[^/.]+)')
    def by_guard(self, request, guard_id=None):
        try:
            assignments = Assignment.objects.filter(guard_id=guard_id, is_deleted=False)
            serializer = self.get_serializer(assignments, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.error(f"Error fetching assignments by guard: {e}", exc_info=True)
            return Response({'error': 'Failed to retrieve assignments.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    @action(detail=False, methods=['get'], url_path=r'monthly-location-summary/(?P<location_id>[^/.]+)/(?P<year>\d{4})/(?P<month>\d{1,2})')
    def monthly_location_summary(self, request, location_id=None, year=None, month=None):
        try:
            year = int(year)
            month = int(month)
            num_days = monthrange(year, month)[1]
            days = [f"{day:02d}-{month:02d}" for day in range(1, num_days + 1)]

            # Get all assignments for the location
            assignments = Assignment.objects.select_related('guard', 'shift', 'shift__location').filter(
                shift__location_id=location_id,
                start_date__lte=datetime(year, month, num_days),
                end_date__gte=datetime(year, month, 1),
                is_deleted=False
            )

            # Build summary map
            summary_map = defaultdict(lambda: {
                'name': '',
                'location': '',
                **{day: '-' for day in days}
            })

            for assignment in assignments:
                guard_name = assignment.guard.name
                location_name = assignment.shift.location.name
                shift_name = assignment.shift.name

                for day in range(1, num_days + 1):
                    current_date = datetime(year, month, day).date()
                    if assignment.start_date <= current_date <= assignment.end_date:
                        key = f"{day:02d}-{month:02d}"
                        summary_map[guard_name]['name'] = guard_name
                        summary_map[guard_name]['location'] = location_name
                        summary_map[guard_name][key] = shift_name

            # Convert to list format
            result = list(summary_map.values())
            return Response({
                'headers': ['Name', 'Location'] + days,
                'rows': result
            })

        except Exception as e:
            logger.error(f"Error generating location-based summary: {e}", exc_info=True)
            return Response({'error': 'Failed to generate summary.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path=r'monthly-location-summary-excel/(?P<location_id>[^/.]+)/(?P<year>\d{4})/(?P<month>\d{1,2})')
    def monthly_location_summary_excel(self, request, location_id=None, year=None, month=None):
        try:
            year = int(year)
            month = int(month)
            num_days = monthrange(year, month)[1]
            days = [f"{day:02d}-{month:02d}" for day in range(1, num_days + 1)]

            assignments = Assignment.objects.select_related('guard', 'shift', 'shift__location').filter(
                shift__location_id=location_id,
                start_date__lte=datetime(year, month, num_days),
                end_date__gte=datetime(year, month, 1),
                is_deleted=False
            )

            summary_map = defaultdict(lambda: {
                'name': '',
                'location': '',
                **{day: '-' for day in days}
            })

            for assignment in assignments:
                guard_name = assignment.guard.name
                location_name = assignment.shift.location.name
                shift_name = assignment.shift.name

                for day in range(1, num_days + 1):
                    current_date = datetime(year, month, day).date()
                    if assignment.start_date <= current_date <= assignment.end_date:
                        key = f"{day:02d}-{month:02d}"
                        summary_map[guard_name]['name'] = guard_name
                        summary_map[guard_name]['location'] = location_name
                        summary_map[guard_name][key] = shift_name

            # Create Excel workbook
            wb = Workbook()
            ws = wb.active
            ws.title = f"{month:02d}-{year} Summary"

            headers = ['Name', 'Location'] + days
            ws.append(headers)

            for row in summary_map.values():
                ws.append([row['name'], row['location']] + [row[day] for day in days])

            # Adjust column widths
            for i, column in enumerate(headers, 1):
                ws.column_dimensions[get_column_letter(i)].width = max(12, len(column) + 2)

            # Prepare response
            response = HttpResponse(content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
            filename = f"monthly_summary_{location_id}_{year}_{month}.xlsx"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            wb.save(response)
            return response

        except Exception as e:
            logger.error(f"Error generating Excel summary: {e}", exc_info=True)
            return Response({'error': 'Failed to generate Excel summary.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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


from rest_framework import viewsets, permissions
from rest_framework.response import Response
from rest_framework import status
from .models import CheckpointTemplate
from .serializers import CheckpointTemplateSerializer

class CheckpointTemplateViewSet(viewsets.ModelViewSet):

    queryset = CheckpointTemplate.objects.filter(is_deleted=False)
    serializer_class = CheckpointTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]

    @action(detail=False, methods=['get'], url_path='all')
    def get_all_templates(self, request):
        templates = CheckpointTemplate.objects.filter(is_deleted=False)
        result = [self._enrich_template(template) for template in templates]
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='by-shift-location/(?P<shift_id>[^/.]+)/(?P<location_id>[^/.]+)')
    def by_shift_and_location(self, request, shift_id=None, location_id=None):
        templates = CheckpointTemplate.objects.filter(
            shift_id=shift_id,
            location_id=location_id,
            is_deleted=False
        )
        if not templates.exists():
            return Response({"detail": "No templates found for this shift and location."}, status=status.HTTP_404_NOT_FOUND)

        result = [self._enrich_template(template) for template in templates]
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=['get'], url_path='by-location/(?P<location_id>[^/.]+)')
    def by_location(self, request, location_id=None):
        """
        Get all checkpoint templates for a specific location.
        
        URL: /checkpoint-templates/by-location/{location_id}/
        """
        templates = CheckpointTemplate.objects.filter(
            location_id=location_id,
            is_deleted=False
        )
        
        if not templates.exists():
            return Response(
                {"detail": "No templates found for this location."}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        result = [self._enrich_template(template) for template in templates]
        return Response(result, status=status.HTTP_200_OK)        

    @action(detail=False, methods=['get'], url_path='by-shift/(?P<shift_id>[^/.]+)')
    def by_shift(self, request, shift_id=None):
        templates = CheckpointTemplate.objects.filter(shift_id=shift_id, is_deleted=False)
        if not templates.exists():
            return Response({"detail": "No templates found for this shift."}, status=status.HTTP_404_NOT_FOUND)

        result = []
        for template in templates:
            enriched_checkpoints = []
            for item in template.checkpoints:
                checkpoint_id = item.get("checkpoint_id")
                time = item.get("time")
                checkpoint = Checkpoint.objects.filter(id=checkpoint_id, is_deleted=False).first()
                if checkpoint:
                    enriched_checkpoints.append({
                        "time": time,
                        "checkpoint": {
                            "id": str(checkpoint.id),
                            "label": checkpoint.label,
                            "type": checkpoint.type,
                            "data": checkpoint.data,
                            "latitude": checkpoint.latitude,
                            "longitude": checkpoint.longitude,
                            "location_id": checkpoint.location_id,
                        }
                    })
            result.append({
                "template_id": str(template.id),
                "template_name": template.template_name,
                "shift_id": str(template.shift_id),
                "checkpoints": enriched_checkpoints
            })

        return Response(result, status=status.HTTP_200_OK)

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    def _enrich_template(self, template):
        enriched_checkpoints = []
        for item in template.checkpoints:
            checkpoint_id = item.get("checkpoint_id")
            time = item.get("time")
            checkpoint = Checkpoint.objects.filter(id=checkpoint_id, is_deleted=False).first()
            if checkpoint:
                enriched_checkpoints.append({
                    "time": time,
                    "checkpoint": {
                        "id": str(checkpoint.id),
                        "label": checkpoint.label,
                        "type": checkpoint.type,
                        "data": checkpoint.data,
                        "latitude": checkpoint.latitude,
                        "longitude": checkpoint.longitude,
                        "location_id": checkpoint.location_id,
                    }
                })
        return {
            "template_id": str(template.id),
            "template_name": template.template_name,
            "shift_id": str(template.shift_id),
            "location_id": str(template.location_id),
            "checkpoints": enriched_checkpoints
        }

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    def perform_destroy(self, instance):
        instance.delete(user=self.request.user)

# from rest_framework.views import APIView
# from rest_framework.response import Response
# from rest_framework import status, permissions
# from .models import CheckpointTemplate, Checkpoint
# from .serializers import CheckpointTemplateSerializer
# from django.shortcuts import get_object_or_404

# class CheckpointTemplateByShiftView(APIView):
#     permission_classes = [permissions.IsAuthenticated]

#     def get(self, request, shift_id):
#         templates = CheckpointTemplate.objects.filter(shift_id=shift_id, is_deleted=False)
#         if not templates.exists():
#             return Response({"detail": "No templates found for this shift."}, status=status.HTTP_404_NOT_FOUND)

#         result = []
#         for template in templates:
#             enriched_checkpoints = []
#             for item in template.checkpoints:
#                 checkpoint_id = item.get("checkpoint_id")
#                 time = item.get("time")
#                 checkpoint = Checkpoint.objects.filter(id=checkpoint_id, is_deleted=False).first()
#                 if checkpoint:
#                     enriched_checkpoints.append({
#                         "time": time,
#                         "checkpoint": {
#                             "id": str(checkpoint.id),
#                             "label": checkpoint.label,
#                             "type": checkpoint.type,
#                             "data": checkpoint.data,
#                             "latitude": checkpoint.latitude,
#                             "longitude": checkpoint.longitude,
#                             "location_id": checkpoint.location_id,
#                         }
#                     })
#             result.append({
#                 "template_id": str(template.id),
#                 "template_name": template.template_name,
#                 "shift_id": str(template.shift_id),
#                 "checkpoints": enriched_checkpoints
#             })

#         return Response(result, status=status.HTTP_200_OK)
