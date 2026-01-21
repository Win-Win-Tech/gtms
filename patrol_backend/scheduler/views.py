import uuid

from rest_framework import viewsets, status as http_status
from django.db.models import Q, F
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
            # 1. Timezone and Date Range Setup
            user_tz = get_user_timezone_from_request(request)
            today_initial = get_user_today(user_tz)
            yesterday_initial = today_initial - timedelta(days=1)
            
            from patrol_backend.utils.timezone_utils import convert_date_range_to_utc
            start_dt_user = datetime.combine(today_initial, datetime.min.time())
            end_dt_user = datetime.combine(today_initial, datetime.max.time())
            start_utc, end_utc = convert_date_range_to_utc(start_dt_user.date(), end_dt_user.date(), user_tz)
            end_utc = end_utc + timedelta(days=1)

            # 2. Fetch Assignments (Today + Overnight carry-overs)
            assignments = list(Assignment.objects.filter(
                start_date__lte=end_utc.date(),
                end_date__gte=start_utc.date(),
                guard_id=user_id
            ).select_related('shift', 'shift__location'))

            yesterday_assignments = list(Assignment.objects.filter(
                guard_id=user_id,
                end_date=yesterday_initial,
                shift__end_time__lte=F('shift__start_time')
            ).select_related('shift', 'shift__location'))

            all_assignments = assignments + yesterday_assignments

            # 3. COLLECT ALL SCANS FOR THE GUARD TODAY (Pre-fetch for matching)
            # This is the "Scan Consumption" logic starting point
            all_checkins = list(CheckIn.objects.filter(
                guard_id=user_id,
                timestamp__gte=start_utc,
                timestamp__lt=end_utc
            ).order_by('timestamp'))
            
            for all_checkin in all_checkins:
                print(f"DEBUG: All checkin: {all_checkin.timestamp}, checkpoint id: {all_checkin.checkpoint.id}, guard id: {all_checkin.guard.id}, checkpoint label: {all_checkin.checkpoint.label}, checkpoint data: {all_checkin.checkpoint.data}")
            
            used_checkin_ids = set() # To track which physical scans are already "consumed"
            result = []

            # 4. Process Each Assignment
            for assignment in all_assignments:
                location_id = assignment.shift.location_id if assignment.shift and assignment.shift.location else None
                assignment_tz = get_user_timezone_from_request(request, location_id=location_id) if location_id else user_tz

                today = get_user_today(assignment_tz)
                user_now = get_user_now(assignment_tz)
                yesterday = today - timedelta(days=1)
                
                shift = assignment.shift
                is_overnight = shift.end_time <= shift.start_time
                assignment_ended_yesterday = assignment.end_date == yesterday
                
                if assignment_ended_yesterday and is_overnight:
                    if user_now.time() >= shift.end_time:
                        continue

                # 5. Process Checkpoints in this Assignment
                for cp in assignment.checkpoints:
                    checkpoint_id_str = cp.get('checkpoint_id')
                    try:
                        checkpoint_id = uuid.UUID(checkpoint_id_str)
                    except (ValueError, TypeError):
                        continue
                    
                    checkpoint_obj = Checkpoint.objects.filter(id=checkpoint_id).first()
                    if not checkpoint_obj:
                        continue

                    try:
                        checkpoint_time = datetime.strptime(cp['time'], '%H:%M').time()
                        
                        # Calculate the specific date for this checkpoint instance
                        if is_overnight:
                            if checkpoint_time >= shift.start_time:
                                if assignment_ended_yesterday: continue
                                checkpoint_date = today
                            else:
                                if user_now.time() < shift.end_time:
                                    checkpoint_date = today
                                else:
                                    checkpoint_date = today + timedelta(days=1)
                                    if today > assignment.end_date: continue
                        else:
                            checkpoint_date = today
                        
                        # Expected time in UTC
                        expected_dt_utc = combine_date_time_in_user_tz(checkpoint_date, checkpoint_time, assignment_tz)
                        
                        # 6. SCAN CONSUMPTION MATCHING
                        is_checked_in = False
                        for scan in all_checkins:
                            # If scan is for this checkpoint, not used yet, and within 30-min window
                            if (scan.checkpoint_id == checkpoint_id and 
                                scan.id not in used_checkin_ids and 
                                abs(scan.timestamp - expected_dt_utc) <= timedelta(minutes=30)):
                                
                                is_checked_in = True
                                used_checkin_ids.add(scan.id) # Mark this physical scan as "consumed"
                                break
                        
                    except Exception as e:
                        logger.warning(f"[UPCOMING_CHECKPOINTS_API] Error: {e}")
                        is_checked_in = False

                    result.append({
                        'assign_id': assignment.id,
                        'checkpoint_id': checkpoint_id,
                        'label': checkpoint_obj.label,
                        'time': cp['time'], # Format "HH:MM"
                        'lat': checkpoint_obj.latitude,
                        'lon': checkpoint_obj.longitude,
                        'qr': checkpoint_obj.data,
                        'shift_id': shift.id,
                        'status': 'completed' if is_checked_in else 'pending',
                        'synced': True if is_checked_in else None
                    })

            # 7. FINAL SORTING BY ASSIGNED TIME
            # Ensures 01:00, 01:30, 02:00 etc. order
            result.sort(key=lambda x: x['time'])
            
            return Response(result)

        except Exception as e:
            logger.error(f"[UPCOMING_CHECKPOINTS_API] Global Error: {str(e)}", exc_info=True)
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
            
            # Get user timezone - for superadmin viewing location-specific reports, uses location admin's timezone
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            # Note: This API shows shift assignments (schedules), not attendance records
            # So we always return the full month to show all scheduled shifts, including future dates
            
            # Always generate full month days array (01-01 through 31-01, etc.)
            days = [f"{day:02d}-{month:02d}" for day in range(1, num_days + 1)]

            # Get all assignments for the location
            assignments = Assignment.objects.select_related('guard', 'shift', 'shift__location').filter(
                shift__location_id=location_id,
                start_date__lte=datetime(year, month, num_days),
                end_date__gte=datetime(year, month, 1),
                is_deleted=False
            )

            # Build summary map - initialize with all days set to '-'
            summary_map = defaultdict(lambda: {
                'name': '',
                'location': '',
                **{day: '-' for day in days}
            })

            for assignment in assignments:
                guard_name = assignment.guard.name
                location_name = assignment.shift.location.name
                shift_name = assignment.shift.name

                # Process all days in the month (not limited to today)
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
            return Response({'error': 'Failed to generate summary.'}, status=http_status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path=r'monthly-location-summary-excel/(?P<location_id>[^/.]+)/(?P<year>\d{4})/(?P<month>\d{1,2})')
    def monthly_location_summary_excel(self, request, location_id=None, year=None, month=None):
        try:
            year = int(year)
            month = int(month)
            num_days = monthrange(year, month)[1]
            
            # Get user timezone - for superadmin viewing location-specific reports, uses location admin's timezone
            user_tz = get_user_timezone_from_request(request, location_id=location_id)
            # Note: This API shows shift assignments (schedules), not attendance records
            # So we always return the full month to show all scheduled shifts, including future dates
            
            # Always generate full month days array (01-01 through 31-01, etc.)
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

                # Process all days in the month (not limited to today)
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
