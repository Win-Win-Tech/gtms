# Standard library imports
import csv
import json
import os
from calendar import monthrange
from collections import defaultdict
from datetime import date, datetime, timedelta
from io import BytesIO

# Third-party imports
import pytz
from geopy.distance import geodesic
from openpyxl import Workbook

# Django imports
from django.conf import settings
from django.core.serializers import serialize
from django.db.models import Avg, Count, Q
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.timezone import (
    get_current_timezone,
    is_aware,
    is_naive,
    localtime,
    make_aware,
    now,
)

# Django REST Framework imports
from rest_framework import generics, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ViewSet

# Local app imports
from authapp.models import User
from checkin.models import CheckIn
from scheduler.models import Assignment, Checkpoint, Location
from tourlog.models import TourLog

from .models import AttendanceCheckin, CheckInLog
from .serializers import (
    AttendanceCheckinDashboardSerializer,
    AttendanceCheckinSerializer,
    CheckInReportSerializer,
)

class GuardPerformanceView(APIView):
    def get(self, request):
        data = []
        for guard in User.objects.all():
            logs = TourLog.objects.filter(guard=guard)
            completed = logs.filter(completed=True).count()
            total = logs.count()
            avg_duration = logs.aggregate(avg=Avg('end_time'))['avg']
            data.append({
#               "guard": guard.username,
                "guard": guard.name if hasattr(guard, "name") else guard.email,
                "completed_tours": completed,
                "total_tours": total,
                "completion_rate": round((completed / total) * 100, 2) if total else 0,
                "avg_duration": avg_duration
            })
        return Response(data)

class TourStatsView(APIView):
    def get(self, request):
        from django.utils.timezone import now
        today = now().date()
        logs_today = TourLog.objects.filter(start_time__date=today)
        completed = logs_today.filter(completed=True).count()
        total = logs_today.count()
        return Response({
            "date": str(today),
            "completed": completed,
            "total": total,
            "completion_rate": round((completed / total) * 100, 2) if total else 0
        })


class ExportTourLogsCSV(APIView):
    def get(self, request):
        logs = TourLog.objects.select_related('guard', 'shift').all()
        response = HttpResponse(content_type='text/csv')
        response['Content-Disposition'] = 'attachment; filename="tour_logs.csv"'
        writer = csv.writer(response)
        writer.writerow(['Guard', 'Shift', 'Start', 'End', 'Completed'])
        for log in logs:
            writer.writerow([
                log.guard.username,
                log.shift.name,
                log.start_time,
                log.end_time,
                log.completed
            ])
        return response


class PatrolRouteView(APIView):
    def get(self, request, guard_id):
        checkins = CheckIn.objects.filter(guard_id=guard_id).order_by('timestamp')
        route = [
            {"lat": c.latitude, "lng": c.longitude, "time": c.timestamp}
            for c in checkins
        ]
        return Response(route)

class AttendanceCheckinViewSet(viewsets.ModelViewSet):
    queryset = AttendanceCheckin.objects.all()
    serializer_class = AttendanceCheckinSerializer

    def get_today_assignment(self, user):
#       today = timezone.localtime(timezone.now()).date()
        now = timezone.now()
        if timezone.is_naive(now):
            now = timezone.make_aware(now, timezone.get_current_timezone())
        today = timezone.localtime(now).date()
        return Assignment.objects.filter(
            guard_id=user.id,
            start_date__lte=today,
            end_date__gte=today
        ).first()        

    

    @action(detail=False, methods=["get"])
    def shift_today(self, request):
        """Return today's shift info + flags for checkin/checkout buttons"""
        user = request.user
        user_id = user.id

        now = timezone.now()
        if timezone.is_naive(now):
            now = timezone.make_aware(now, timezone.get_current_timezone())
        now = timezone.localtime(now)

        today = now.date()

        assignment = Assignment.objects.filter(
            guard_id=user_id,
            start_date__lte=today,
            end_date__gte=today
        ).first()

        if not assignment:
            return Response({
                "has_shift": False,
                "show_checkin": False,
                "show_checkout": False,
                "message": "No shifts today"
            }, status=status.HTTP_200_OK)

        shift = assignment.shift
        location = assignment.location

        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__date=today
        ).first()

        show_checkin = False
        show_checkout = False
        message = ""

        # Determine shift window
        shift_start_dt = make_aware(datetime.combine(today, shift.start_time)) if is_naive(datetime.combine(today, shift.start_time)) else datetime.combine(today, shift.start_time)
  
        if shift.end_time <= shift.start_time:
            shift_end_dt = make_aware(datetime.combine(today + timedelta(days=1), shift.end_time)) if is_naive(datetime.combine(today + timedelta(days=1), shift.end_time)) else datetime.combine(today + timedelta(days=1), shift.end_time)
        else:
            shift_end_dt = make_aware(datetime.combine(today, shift.end_time)) if is_naive(datetime.combine(today, shift.end_time)) else datetime.combine(today, shift.end_time)
        #shift_end_dt = make_aware(datetime.combine(today, shift.end_time)) if is_naive(datetime.combine(today, shift.end_time)) else datetime.combine(today, shift.end_time)
        
        # Define check-in and check-out windows
        earliest_checkin = shift_start_dt - timedelta(minutes=30)
        latest_checkin = shift_end_dt
        earliest_checkout = shift_start_dt
        latest_checkout = shift_end_dt + timedelta(minutes=30)

        # Scenario logic
        if not attendance:
            # Scenario 1: No check-in yet
            if earliest_checkin <= now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif now < earliest_checkin:
                message = "Too early to check in"
            else:
                # Scenario 5: Shift ended without check-out
                message = "Shift ended"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                if now <= shift_end_dt:
                    # Scenario 2: Checked in, not yet checked out
                    show_checkout = True
                    message = "You are checked in, checkout when done"
                else:
                    # Scenario 5: Shift ended, no checkout
                    message = "Shift ended"
            elif attendance.checkin_time and attendance.checkout_time:
                if now <= shift_end_dt:
                    # Check if user has checked in again after checkout
                    latest_checkin = CheckInLog.objects.filter(
                        guard=user,
                        assignment=assignment,
                        shift=shift,
                        org_location=location,
                        type="checkin",
                        timestamp__date=today,
                        timestamp__gt=attendance.checkout_time
                    ).order_by("-timestamp").first()

                    if latest_checkin:
                        show_checkin = False
                        show_checkout = True
                        message = "You have checked-in. You can check-out"
                    else:
                        show_checkin = True
                        show_checkout = False
                        message = "You are checked out already but can check in again"
   
                else:
                    # Scenario 5: Shift ended
                    message = "Shift ended"

    # Optional: Scenario 4 — checked in again after checkout
    # If you track multiple check-ins via a log, you can detect this and show:
    # show_checkout = True
    # message = "You can check out"

        return Response({
                "has_shift": True,
                "shift_id": str(shift.id),
                "shift_start": shift.start_time,
                "shift_end": shift.end_time,
                "location_name": location.name,
                "show_checkin": show_checkin,
                "show_checkout": show_checkout,
                "message": message
            }, status=status.HTTP_200_OK)

  
    @action(detail=False, methods=["post"])
    def checkin(self, request):
        user = request.user
        assignment = self.get_today_assignment(user)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))
        distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters
        print("request.data", request.data)

        if distance > 100:
            return Response({"error": "Not within >100m of assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        # Log this check-in
        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon
        )

        # Update or create attendance record
        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__date=date.today()
        )

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__date=date.today()
        ).order_by("timestamp").first()

        attendance.checkin_time = earliest_checkin.timestamp
        attendance.latitude = earliest_checkin.latitude
        attendance.longitude = earliest_checkin.longitude
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        user = request.user
        assignment = self.get_today_assignment(user)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__date=date.today()
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        # Log this checkout
        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            latitude=lat,
            longitude=lon
        )

        latest_checkout = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkout",
            timestamp__date=date.today()
        ).order_by("-timestamp").first()

        attendance.checkout_time = latest_checkout.timestamp
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_200_OK)


    # ----------------------
    # DASHBOARD (with date range filter)
    # ----------------------
    # @action(detail=False, methods=["get"])
    # def dashboard(self, request):
    #     start_date = request.query_params.get("start_date")
    #     end_date = request.query_params.get("end_date")

    #     # Default: today
    #     if not start_date:
    #         start_date = date.today().strftime("%Y-%m-%d")
    #     if not end_date:
    #         end_date = date.today().strftime("%Y-%m-%d")

    #     try:
    #         start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
    #         end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
    #     except ValueError:
    #         return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

    #     records = AttendanceCheckin.objects.filter(
    #         checkin_time_date_gte=start_date,
    #         checkin_time_date_lte=end_date
    #     ).select_related("guard", "shift", "assignment")

    #     data = []
    #     for record in records:
    #         data.append({
    #             "name": record.guard.get_full_name() or record.guard.username,
    #             "date": record.checkin_time.date() if record.checkin_time else record.assignment.start_date,
    #             "shift_time": f"{record.shift.start_time} - {record.shift.end_time}",
    #             "checkin_time": record.checkin_time,
    #             "checkout_time": record.checkout_time,
    #             "status": record.status,
    #             "remarks": record.remarks,
    #             "od_remarks": record.od_remarks
    #         })

    #     return Response(data, status=status.HTTP_200_OK)
    

    # views.py

# ============================================================================
# CORE FUNCTION: Shared business logic for check-in reports
# ============================================================================

def _get_checkin_report_data(filter_type='today', start_date_str=None, end_date_str=None, user_id=None, location_id=None):
    """
    Internal helper function that contains ALL business logic for check-in reports.
    This ensures consistency across JSON API, Excel exports, and Celery tasks.
    
    Args:
        filter_type: 'today', 'this_week', 'this_month', or 'custom'
        start_date_str: For custom filter (YYYY-MM-DD string)
        end_date_str: For custom filter (YYYY-MM-DD string)
        user_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
    
    Returns:
        List of dicts with report data:
        [{
            'date': '2025-11-05',
            'guard_id': UUID,
            'guard_name': str,
            'location_id': str,
            'location_name': str,
            'shift_id': str,
            'shift_name': str,
            'checkpoint_id': str,
            'checkpoint_name': str,
            'expected_time': datetime,
            'actual_checkin_time': datetime or None,
            'status': 'On Time' | 'Delayed' | 'Missed',
            'delay_minutes': int or None
        }, ...]
    """
    from django.utils.timezone import now as django_now
    
    today = timezone.now().date()
    
    # Parse filter and determine date range
    if filter_type == 'today':
        start = datetime.combine(today, datetime.min.time())
        end = datetime.combine(today, datetime.max.time())
    elif filter_type == 'this_week':
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        start = datetime.combine(start, datetime.min.time())
        end = datetime.combine(end, datetime.max.time())
    elif filter_type == 'this_month':
        start = datetime(today.year, today.month, 1)
        next_month = start.replace(day=28) + timedelta(days=4)
        end = datetime(next_month.year, next_month.month, 1) - timedelta(seconds=1)
    elif filter_type == 'custom' and start_date_str and end_date_str:
        try:
            start = datetime.strptime(start_date_str, "%Y-%m-%d")
            end = datetime.strptime(end_date_str, "%Y-%m-%d")
            end = datetime.combine(end.date(), datetime.max.time())
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
    else:
        raise ValueError("Invalid filter or missing dates")
    
    # Get assignments that overlap with the date range
    assignments = Assignment.objects.filter(
        start_date__lte=end.date(),
        end_date__gte=start.date()
    ).select_related('guard', 'location', 'shift')
    
    if user_id:
        assignments = assignments.filter(guard_id=user_id)
    if location_id:
        assignments = assignments.filter(location_id=location_id)
    
    report = []
    
    # Generate date range for the filter period
    date_range = []
    current_date = start.date()
    while current_date <= end.date():
        date_range.append(current_date)
        current_date += timedelta(days=1)
    
    for assignment in assignments:
        guard = assignment.guard
        shift = assignment.shift
        location = assignment.location
        
        # For each checkpoint in the assignment
        for cp in assignment.checkpoints:
            checkpoint_id = cp.get('checkpoint_id')
            expected_time_str = cp.get('time')
            
            # Validate checkpoint data
            if not checkpoint_id or not expected_time_str:
                continue  # Skip invalid checkpoint entries
            
            try:
                expected_time_obj = datetime.strptime(expected_time_str, "%H:%M").time()
            except ValueError:
                continue  # Skip invalid time format
            
            # Get checkpoint name (with error handling)
            try:
                checkpoint = Checkpoint.objects.get(id=checkpoint_id)
                checkpoint_name = checkpoint.label
            except Checkpoint.DoesNotExist:
                checkpoint_name = "Unknown Checkpoint"
                continue  # Skip if checkpoint doesn't exist
            
            # For each date in the range where the assignment is active
            for check_date in date_range:
                # Only process if assignment is active on this date
                if not (assignment.start_date <= check_date <= assignment.end_date):
                    continue
                
                # Calculate expected time for this specific date
                expected_datetime = datetime.combine(check_date, expected_time_obj)
                
                # Define the search window for this specific date's check-in
                day_start = datetime.combine(check_date, datetime.min.time())
                day_end = datetime.combine(check_date, datetime.max.time())
                
                # Query for check-ins on this specific date
                checkin = CheckIn.objects.filter(
                    guard=guard,
                    shift=shift,
                    checkpoint_id=checkpoint_id,
                    timestamp__gte=day_start,
                    timestamp__lte=day_end
                ).order_by('timestamp').first()
                
                actual_time = None
                delay = None
                status = "Missed"
                
                # Process check-in if found
                if checkin:
                    # Check if it's synced
                    if not checkin.synced:
                        # Unsynced check-in (offline mode) - treat as missed
                        status = "Missed"
                        actual_time = None
                        delay = None
                    else:
                        # Valid synced check-in
                        actual_time = checkin.timestamp
                        
                        # Calculate delay in minutes
                        delay = int((actual_time - expected_datetime).total_seconds() / 60)
                        
                        # Determine status based on delay
                        if delay <= 15:
                            status = "On Time"
                        elif 15 < delay <= 30:
                            status = "Delayed"
                        else:
                            status = "Missed"
                
                # Add to report
                report.append({
                    'date': check_date.strftime('%Y-%m-%d'),
                    'guard_id': str(guard.id),
                    'guard_name': guard.name,
                    'location_id': str(location.id) if location else None,
                    'location_name': location.name if location else "",
                    'shift_id': str(shift.id) if shift else None,
                    'shift_name': shift.name if shift else "",
                    'checkpoint_id': str(checkpoint_id),
                    'checkpoint_name': checkpoint_name,
                    'expected_time': expected_datetime,
                    'actual_checkin_time': actual_time,
                    'status': status,
                    'delay_minutes': delay
                })
    
    return report


# ============================================================================
# API ENDPOINTS
# ============================================================================

class DashboardCheckInReportView(APIView):
    """
    API endpoint that returns check-in report data as JSON.
    Used by the frontend dashboard for real-time display.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Parse filters from request
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        
        try:
            # Get report data using shared core function
            report_data = _get_checkin_report_data(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id
            )
            
            # Format datetime fields for JSON response
            for item in report_data:
                if item['expected_time']:
                    item['expected_time'] = item['expected_time'].strftime('%Y-%m-%d %H:%M:%S')
                if item['actual_checkin_time']:
                    item['actual_checkin_time'] = item['actual_checkin_time'].strftime('%Y-%m-%d %H:%M:%S')
            
            # Serialize and return
            serializer = CheckInReportSerializer(report_data, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"An error occurred while generating the report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class AttendanceCheckinListView(generics.ListAPIView):
    serializer_class = AttendanceCheckinDashboardSerializer

    def get_queryset(self):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        #today = timezone.localdate()
        date_filter = self.request.query_params.get("date_filter", "today")

        today = datetime.now().date()

        if date_filter == "today":
            queryset = queryset.filter(checkin_time__date=today)
        elif date_filter == "week":
            start_week = today - timezone.timedelta(days=today.weekday())
            end_week = start_week + timezone.timedelta(days=6)
            queryset = queryset.filter(checkin_time__date__range=(start_week, end_week))
        elif date_filter == "month":
            queryset = queryset.filter(checkin_time__date__month=today.month)

        # Additional filters
        guard_id = self.request.query_params.get("guard")
        if guard_id:
            queryset = queryset.filter(guard_id=guard_id)

        location_id = self.request.query_params.get("location")
        if location_id:
            queryset = queryset.filter(org_location_id=location_id)

        shift_id = self.request.query_params.get("shift")
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)

        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)

        defaulters = self.request.query_params.get("defaulters")
        if defaulters == "true":
            queryset = queryset.filter(checkin_time__date=today, checkout_time__isnull=True)

        return queryset


def generate_checkin_excel_report_internal(filter_type='today', start_date=None, end_date=None, user_id=None, location_id=None):
    """
    Internal helper function to generate check-in Excel report.
    Used by both the API endpoint and Celery tasks.
    
    Args:
        filter_type: 'today', 'this_week', 'this_month', or 'custom'
        start_date: For custom filter (YYYY-MM-DD string)
        end_date: For custom filter (YYYY-MM-DD string)
        user_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
    
    Returns:
        dict with 'file_path', 'filename', and 'row_count'
    """
    # Get report data using shared core function
    report_data = _get_checkin_report_data(
        filter_type=filter_type,
        start_date_str=start_date,
        end_date_str=end_date,
        user_id=user_id,
        location_id=location_id
    )
    
    # Create Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Check-In Report"

    # Header row
    headers = [
        'Shift Date', 'Guard', 'Shift Name', 'Checkpoint Name',
        'Scheduled', 'Scanned', 'Status', 'Delay (minutes)'
    ]
    ws.append(headers)

    row_count = 0
    for item in report_data:
        ws.append([
            item['date'],
            item['guard_name'],
            item['shift_name'],
            item['checkpoint_name'],
            item['expected_time'].strftime("%Y-%m-%d %H:%M") if item['expected_time'] else "",
            item['actual_checkin_time'].strftime("%Y-%m-%d %H:%M") if item['actual_checkin_time'] else "",
            item['status'],
            item['delay_minutes'] if item['delay_minutes'] is not None else ""
        ])
        row_count += 1

    # Save to in-memory buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # Define file path
    filename = f"checkin_report_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)

    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())

    return {
        'file_path': file_path,
        'filename': filename,
        'row_count': row_count
    }



class DashboardCheckInReportExcelView(APIView):
    """
    API endpoint that generates and returns an Excel file download URL.
    Used by the frontend for exporting check-in reports.
    """
    permission_classes = [IsAuthenticated]  # Fixed: Added authentication requirement
    
    def get(self, request):
        # Parse filters
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')
        
        try:
            # Get report data using shared core function
            report_data = _get_checkin_report_data(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id
            )
            
            # Create Excel workbook
            wb = Workbook()
            ws = wb.active
            ws.title = "Check-In Report"

            # Header row
            headers = [
                'Date', 'Guard', 'Shift Name', 'Checkpoint Name',
                'Expected Time', 'Actual Check-In Time', 'Status', 'Delay (minutes)'
            ]
            ws.append(headers)

            # Add data rows
            for item in report_data:
                ws.append([
                    item['date'],
                    item['guard_name'],
                    item['shift_name'],
                    item['checkpoint_name'],
                    item['expected_time'].strftime("%Y-%m-%d %H:%M") if item['expected_time'] else "",
                    item['actual_checkin_time'].strftime("%Y-%m-%d %H:%M") if item['actual_checkin_time'] else "",
                    item['status'],
                    item['delay_minutes'] if item['delay_minutes'] is not None else ""
                ])

            # Save to in-memory buffer
            buffer = BytesIO()
            wb.save(buffer)
            buffer.seek(0)

            # Define file path
            filename = f"checkin_report_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
            file_path = os.path.join(settings.MEDIA_ROOT, filename)

            # Save file to MEDIA directory
            with open(file_path, 'wb') as f:
                f.write(buffer.getvalue())

            # Build downloadable URL
            file_url = request.build_absolute_uri(os.path.join(settings.MEDIA_URL, filename))

            return Response({"download_url": file_url}, status=status.HTTP_200_OK)
            
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"An error occurred while generating the Excel report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


def generate_attendance_excel_report_internal(date_filter='today', start_date=None, end_date=None, guard_id=None, location_id=None, shift_id=None, status_filter=None, defaulters=False):
    """
    Internal helper function to generate attendance check-in Excel report.
    This contains the business logic that both the API endpoint and Celery tasks can use.
    
    Args:
        date_filter: 'today', 'week', 'month', or 'custom'
        start_date: For custom filter (YYYY-MM-DD string)
        end_date: For custom filter (YYYY-MM-DD string)
        guard_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
        shift_id: Optional UUID string to filter by shift
        status_filter: Optional status string
        defaulters: Boolean to filter only defaulters
    
    Returns: dict with 'file_path' and 'filename'
    """
    queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

    today = datetime.now().date()

    if date_filter == "today":
        queryset = queryset.filter(checkin_time__date=today)
    elif date_filter == "week":
        start_week = today - timezone.timedelta(days=today.weekday())
        end_week = start_week + timezone.timedelta(days=6)
        queryset = queryset.filter(checkin_time__date__range=(start_week, end_week))
    elif date_filter == "month":
        queryset = queryset.filter(checkin_time__date__month=today.month)
    elif date_filter == "custom" and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            queryset = queryset.filter(checkin_time__date__range=(start_date_obj, end_date_obj))
        except ValueError:
            raise ValueError("Invalid custom date format. Use YYYY-MM-DD.")

    # Additional filters
    if guard_id:
        queryset = queryset.filter(guard_id=guard_id)

    if location_id:
        queryset = queryset.filter(org_location_id=location_id)

    if shift_id:
        queryset = queryset.filter(shift_id=shift_id)

    if status_filter:
        queryset = queryset.filter(status=status_filter)

    if defaulters:
        queryset = queryset.filter(checkin_time__date=today, checkout_time__isnull=True)

    # Create Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Checkins"

    # Header with reordered columns and added Date + Duration
    ws.append(["Shift Date", "Name", "Shift", "Location", "Checkin Time", "Checkout Time", "Duration (HH:MM)", "Status", "Remarks"])

    row_count = 0  # Track number of data rows
    for obj in queryset:
        checkin = obj.checkin_time
        checkout = obj.checkout_time
        shift = obj.shift
        attendance_status = ""
        other_statuses = []
        duration = ""

        if shift:
            shift_start = datetime.combine(checkin.date(), shift.start_time) if checkin else None
            shift_end = datetime.combine(checkin.date(), shift.end_time)

        if checkin and checkout:
            # Duration
            delta = checkout - checkin
            total_hours = delta.total_seconds() / 3600
            hours, remainder = divmod(delta.total_seconds(), 3600)
            minutes = remainder // 60
            duration = f"{int(hours):02}:{int(minutes):02}"

            # Attendance Status
            if total_hours <= 6:
                attendance_status = "Absent"
            elif 6 < total_hours <= 8:
                attendance_status = "Present"
            elif total_hours >= 8:
                attendance_status = "Overtime"

            # Check-in Timing
            if shift_start:
                checkin_diff = (checkin - shift_start).total_seconds() / 60  # Removed abs() to detect early vs late
                if -30 <= checkin_diff <= 30:
                    other_statuses.append("On-time Checked-in")
                elif checkin_diff < -30:
                    other_statuses.append("Early Checked-in")
                elif checkin_diff > 30:
                    other_statuses.append("Delay Checked-in")

            # Check-out Timing
            checkout_diff = abs((checkout - shift_end).total_seconds()) / 60
            if checkout_diff <= 30:
                other_statuses.append("On-time Checked-out")
            elif checkout < shift_end and checkout_diff < 30:
                other_statuses.append("Early Checked-out")

        elif checkin and not checkout:
            attendance_status = ""
            other_statuses.append("Missed Checked-out")
        elif not checkin and shift:
            attendance_status = ""
            other_statuses.append("Missed Check-in")

        ws.append([
            checkin.strftime('%Y-%m-%d') if checkin else "",
            obj.guard.name,
            shift.name if shift else "",
            obj.org_location.name if obj.org_location else "",
            checkin.strftime('%Y-%m-%d %H:%M:%S') if checkin else "",
            checkout.strftime('%Y-%m-%d %H:%M:%S') if checkout else "",
            duration,
            attendance_status,
            ", ".join(other_statuses)
        ])
        row_count += 1  # Increment row count

    # Save to in-memory buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)

    # Define file path
    filename = f"attendance_export_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)

    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())

    return {
        'file_path': file_path,
        'filename': filename,
        'row_count': row_count  # Return the number of data rows
    }


class AttendanceCheckinExportView(APIView):
    def get(self, request):
        # Parse filters
        date_filter = request.query_params.get("date_filter", "today")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        guard_id = request.query_params.get("guard")
        location_id = request.query_params.get("location")
        shift_id = request.query_params.get("shift")
        status_filter = request.query_params.get("status")
        defaulters = request.query_params.get("defaulters") == "true"
        
        try:
            # Use internal helper function to avoid code duplication
            result = generate_attendance_excel_report_internal(
                date_filter=date_filter,
                start_date=start_date,
                end_date=end_date,
                guard_id=guard_id,
                location_id=location_id,
                shift_id=shift_id,
                status_filter=status_filter,
                defaulters=defaulters
            )
            
            filename = result['filename']
            file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
            return Response({"file_url": file_url})
            
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": f"Failed to generate report: {str(e)}"}, status=500)


def _get_monthly_attendance_summary_data(month=None, start_date_str=None, end_date_str=None, location_id=None, user_id=None):
    """
    Internal helper function to generate monthly attendance summary data.
    This contains the business logic shared by both JSON and Excel endpoints.
    
    Args:
        month: Optional string in YYYY-MM format
        start_date_str: Optional string in YYYY-MM-DD format (for custom range)
        end_date_str: Optional string in YYYY-MM-DD format (for custom range)
        location_id: Optional UUID string to filter by location
        user_id: Optional UUID string to filter by guard
    
    Returns:
        dict with 'summary_data' (list of dicts), 'date_range' (list of dates), 
        'start_date', and 'end_date'
    """
    # Parse and validate date range
    if month:
        try:
            year, month_num = map(int, month.split("-"))
            if not (1 <= month_num <= 12):
                raise ValueError("Month must be between 1 and 12")
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        except (ValueError, AttributeError) as e:
            raise ValueError(f"Invalid month format. Use YYYY-MM. Error: {e}")
    elif start_date_str and end_date_str:
        start_date = parse_date(start_date_str)
        end_date = parse_date(end_date_str)
        if not start_date or not end_date:
            raise ValueError("Invalid date format. Use YYYY-MM-DD")
    else:
        today = now().date()
        start_date = today.replace(day=1)
        end_date = today

    # Get assignments within date range
    assignments = Assignment.objects.filter(
        start_date__lte=end_date,
        end_date__gte=start_date,
        is_deleted=False
    )

    if location_id:
        assignments = assignments.filter(location_id=location_id)
    if user_id:
        assignments = assignments.filter(guard_id=user_id)

    # Generate date range
    date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    # Group assignments by guard + location
    grouped = defaultdict(lambda: {
        "guard": None,
        "location": None,
        "assignments": []
    })

    for assignment in assignments.select_related("guard", "location"):
        key = (assignment.guard.id, assignment.location.id if assignment.location else None)
        grouped[key]["guard"] = assignment.guard
        grouped[key]["location"] = assignment.location.name if assignment.location else "N/A"
        grouped[key]["assignments"].append(assignment)

    summary_data = []

    # Build summary for each guard-location combination
    for (guard_id, loc_id), data in grouped.items():
        guard = data["guard"]
        location = data["location"]
        row = {
            "name": guard.name,
            "location": location
        }

        # Check attendance for each date
        for date in date_range:
            active_assignments = [
                a for a in data["assignments"]
                if a.start_date <= date <= a.end_date
            ]

            if not active_assignments:
                row[date.strftime("%d-%b")] = "-"
                continue

            has_checkin = AttendanceCheckin.objects.filter(
                guard=guard,
                assignment__in=active_assignments,
                checkin_time__date=date
            ).exists()

            row[date.strftime("%d-%b")] = "P" if has_checkin else "A"

        summary_data.append(row)

    return {
        'summary_data': summary_data,
        'date_range': date_range,
        'start_date': start_date,
        'end_date': end_date
    }


def generate_monthly_attendance_summary_excel_internal(month=None, start_date_str=None, end_date_str=None, location_id=None, user_id=None):
    """
    Internal helper function to generate monthly attendance summary Excel report.
    This contains the business logic that both the API endpoint and Celery tasks can use.
    
    Args:
        month: Optional string in YYYY-MM format
        start_date_str: Optional string in YYYY-MM-DD format (for custom range)
        end_date_str: Optional string in YYYY-MM-DD format (for custom range)
        location_id: Optional UUID string to filter by location
        user_id: Optional UUID string to filter by guard
    
    Returns: dict with 'file_path', 'filename', 'start_date', 'end_date', 'row_count'
    """
    # Get summary data using core helper
    result = _get_monthly_attendance_summary_data(
        month=month,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        location_id=location_id,
        user_id=user_id
    )
    
    summary_data = result['summary_data']
    date_range = result['date_range']
    start_date = result['start_date']
    end_date = result['end_date']
    
    # Create Excel workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Monthly Attendance Summary"
    
    # Header row
    headers = ["Name", "Location"] + [date.strftime("%d-%b") for date in date_range]
    ws.append(headers)
    
    # Data rows
    row_count = 0
    for row_data in summary_data:
        row = [row_data["name"], row_data["location"]]
        # Add attendance for each date
        for date in date_range:
            row.append(row_data.get(date.strftime("%d-%b"), "-"))
        ws.append(row)
        row_count += 1
    
    # Save to in-memory buffer
    buffer = BytesIO()
    wb.save(buffer)
    buffer.seek(0)
    
    # Define file path
    filename = f"attendance_summary_{timezone.now().strftime('%Y%m%d%H%M%S')}.xlsx"
    file_path = os.path.join(settings.MEDIA_ROOT, filename)
    
    # Save file to MEDIA directory
    with open(file_path, 'wb') as f:
        f.write(buffer.getvalue())
    
    return {
        'file_path': file_path,
        'filename': filename,
        'start_date': start_date,
        'end_date': end_date,
        'row_count': row_count  # Number of guards in the report
    }


class MonthlyAttendanceSummaryViewSet(ViewSet):

    @action(detail=False, methods=["get"])
    def summary(self, request):
        # Parse filters
        location_id = request.query_params.get("location_id")
        user_id = request.query_params.get("user_id")
        month = request.query_params.get("month")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        try:
            # Use internal helper function
            result = _get_monthly_attendance_summary_data(
                month=month,
                start_date_str=start_date,
                end_date_str=end_date,
                location_id=location_id,
                user_id=user_id
            )
            
            return Response(result['summary_data'])
            
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": f"Failed to generate summary: {str(e)}"}, status=500)


# class MonthlyAttendanceSummaryViewSet(ViewSet):
#     @action(detail=False, methods=["get"])
#     def summary(self, request):
#         # Filters from query params
#         location_id = request.query_params.get("location_id")
#         user_id = request.query_params.get("user_id")
#         month = request.query_params.get("month")  # format: YYYY-MM
#         start_date = request.query_params.get("start_date")
#         end_date = request.query_params.get("end_date")

#         # Resolve date range
#         if month:
#             year, month_num = map(int, month.split("-"))
#             start_date = datetime(year, month_num, 1).date()
#             end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
#         elif start_date and end_date:
#             start_date = parse_date(start_date)
#             end_date = parse_date(end_date)
#         else:
#             today = now().date()
#             start_date = today.replace(day=1)
#             end_date = today

#         # Get assignments within date range
#         assignments = Assignment.objects.filter(
#             start_date__lte=end_date,
#             end_date__gte=start_date,
#             is_deleted=False
#         )

#         if location_id:
#             assignments = assignments.filter(location_id=location_id)
#         if user_id:
#             assignments = assignments.filter(guard_id=user_id)

#         # Build guard-wise attendance map
# #         summary = []
# #         date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

# #         for assignment in assignments.select_related("guard", "location"):
# #             guard = assignment.guard
# #             location = assignment.location.name if assignment.location else "N/A"

# #             row = {
# #                 #"name": guard.get_full_name() or guard.username,
# #                 "name": guard.name,
# #                 "location": location
# #             }

# #             for date in date_range:
# #                 # Check if assignment is active on this date
# #                 if assignment.start_date <= date <= assignment.end_date:
# #                     checkin = AttendanceCheckin.objects.filter(
# #                         guard=guard,
# #                         assignment=assignment,
# #                         shift=assignment.shift,
# #                         org_location=assignment.location,
# #                         checkin_time__date=date
# #                     ).first()

# # #                   row[date.strftime("%d-%b")] = checkin.status[0].upper() if checkin else "A"
# #                     row[date.strftime("%d-%b")] = "P" if checkin else "A" if assignment.start_date <= date <= assignment.end_date else "-"

# #                 else:
# #                     row[date.strftime("%d-%b")] = "-"

# #             summary.append(row)

#         # Build guard-wise attendance map
#         summary = []
#         #date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
#         date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
#         # Group assignments by guard + location
#         from collections import defaultdict

#         # Group assignments by guard + location
#         grouped = defaultdict(lambda: {
#             "guard": None,
#             "location": None,
#             "assignments": []
#         })

#         guard_map = defaultdict(lambda: {"assignments": [], "location": None})

#         # for assignment in assignments.select_related("guard", "location"):
#         #     key = (assignment.guard.id, assignment.location.id if assignment.location else None)
#         #     guard_map[key]["assignments"].append(assignment)
#         #     guard_map[key]["location"] = assignment.location.name if assignment.location else "N/A"
#         #     guard_map[key]["guard"] = assignment.guard

#         for assignment in assignments.select_related("guard", "location"):
#             key = (assignment.guard.id, assignment.location.id if assignment.location else None)
#             grouped[key]["guard"] = assignment.guard
#             grouped[key]["location"] = assignment.location.name if assignment.location else "N/A"
#             grouped[key]["assignments"].append(assignment)
        
#         summary = []

#         # Build summary rows
#         for (guard_id, location_id), data in guard_map.items():
#             guard = data["guard"]
#             location = data["location"]
#             row = {
#                 "name": guard.name,
#                 "location": location
#             }

#         for date in date_range:
#                 # Get all assignments active on this date
#                 active_assignments = [
#                     a for a in data["assignments"]
#                     if a.start_date <= date <= a.end_date
#                 ]

#                 if not active_assignments:
#                     row[date.strftime("%d-%b")] = "-"
#                     continue

#                 # Check if any assignment has a checkin
#                 has_checkin = AttendanceCheckin.objects.filter(
#                     guard=guard,
#                     assignment__in=active_assignments,
#                     checkin_time__date=date
#                 ).exists()

#                 row[date.strftime("%d-%b")] = "P" if has_checkin else "A"

    
#         summary.append(row)


#         return Response(summary)


class MonthlyAttendanceExcelViewSet(ViewSet):

    @action(detail=False, methods=["get"])
    def export_excel(self, request):
        # Parse filters
        location_id = request.query_params.get("location_id")
        user_id = request.query_params.get("user_id")
        month = request.query_params.get("month")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        try:
            # Use Excel helper function (consistent with daily reports pattern)
            result = generate_monthly_attendance_summary_excel_internal(
                month=month,
                start_date_str=start_date,
                end_date_str=end_date,
                location_id=location_id,
                user_id=user_id
            )
            
            file_path = result['file_path']
            start_date_obj = result['start_date']
            end_date_obj = result['end_date']
            
            # Return Excel file as HTTP response
            with open(file_path, 'rb') as f:
                excel_content = f.read()
            
            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            filename = f"attendance_summary_{start_date_obj.strftime('%Y%m%d')}_{end_date_obj.strftime('%Y%m%d')}.xlsx"
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            return Response({"error": f"Failed to generate Excel: {str(e)}"}, status=500)

    # def export_excel(self, request):
    #     location_id = request.query_params.get("location_id")
    #     user_id = request.query_params.get("user_id")
    #     month = request.query_params.get("month")
    #     start_date = request.query_params.get("start_date")
    #     end_date = request.query_params.get("end_date")

    #     if month:
    #         year, month_num = map(int, month.split("-"))
    #         start_date = datetime(year, month_num, 1).date()
    #         end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
    #     elif start_date and end_date:
    #         start_date = parse_date(start_date)
    #         end_date = parse_date(end_date)
    #     else:
    #         today = now().date()
    #         start_date = today.replace(day=1)
    #         end_date = today

    #     assignments = Assignment.objects.filter(
    #         start_date__lte=end_date,
    #         end_date__gte=start_date,
    #         is_deleted=False
    #     )

    #     if location_id:
    #         assignments = assignments.filter(location_id=location_id)
    #     if user_id:
    #         assignments = assignments.filter(guard_id=user_id)

    #     date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]

    #     wb = Workbook()
    #     ws = wb.active
    #     ws.title = "Attendance Summary"

    #     # Header row
    #     headers = ["Name", "Location"] + [date.strftime("%d-%b") for date in date_range]
    #     ws.append(headers)

    #     for assignment in assignments.select_related("guard", "location"):
    #         guard = assignment.guard
    #         location = assignment.location.name if assignment.location else "N/A"

    #         row = [guard.name, location]

    #         for date in date_range:
    #             if assignment.start_date <= date <= assignment.end_date:
    #                 checkin = AttendanceCheckin.objects.filter(
    #                     guard=guard,
    #                     assignment=assignment,
    #                     shift=assignment.shift,
    #                     org_location=assignment.location,
    #                     checkin_time__date=date
    #                 ).first()
    #                 status_code = "P" if checkin else "A"
    #             else:
    #                 status_code = "-"
    #             row.append(status_code)

    #         ws.append(row)

    #     # Prepare response
    #     response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    #     filename = f"attendance_summary_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
    #     response["Content-Disposition"] = f'attachment; filename="{filename}"'
    #     wb.save(response)
    #     return response

