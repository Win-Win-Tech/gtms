from rest_framework.views import APIView
from tourlog.models import TourLog
from authapp.models import User
from django.db.models import Count, Avg
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from geopy.distance import geodesic
from django.utils import timezone
from datetime import date, datetime
from .models import AttendanceCheckin, CheckInLog
from .serializers import AttendanceCheckinSerializer, AttendanceCheckinDashboardSerializer
from scheduler.models import Assignment
from django.utils.timezone import localtime, make_aware
from datetime import  datetime, timedelta
from django.core.serializers import serialize
import json
from django.utils.timezone import now
from scheduler.models import Assignment
from checkin.models import CheckIn
import pytz
from django.utils.timezone import is_aware, is_naive
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import timedelta, datetime
from checkin.models import CheckIn
from authapp.models import User
from scheduler.models import Checkpoint, Assignment
from .serializers import CheckInReportSerializer
from rest_framework import generics
import os
from django.conf import settings
from django.utils.timezone import make_aware, is_aware, get_current_timezone
from rest_framework.permissions import AllowAny
from collections import defaultdict

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

import csv
from django.http import HttpResponse

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

from checkin.models import CheckIn

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

class DashboardCheckInReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Parse filters
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')

        #today = timezone.localdate()
        #now = timezone.now()

        today = timezone.now().date()
        now = timezone.now()

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
        elif filter_type == 'custom' and start_date and end_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        else:
            return Response({"error": "Invalid filter or missing dates"}, status=400)

        assignments = Assignment.objects.filter(
            start_date__lte=end.date(),
            end_date__gte=start.date()
        )

        if user_id:
            assignments = assignments.filter(guard_id=user_id)
        if location_id:
            assignments = assignments.filter(location_id=location_id)

        report = []

        for assignment in assignments:
            guard = assignment.guard
            shift = assignment.shift
            for cp in assignment.checkpoints:
                checkpoint_id = cp.get('checkpoint_id')
                expected_time_str = cp.get('time')
                expected_time = datetime.combine(assignment.start_date, datetime.strptime(expected_time_str, "%H:%M").time())

                checkin = CheckIn.objects.filter(
                    guard=guard,
                    shift=shift,
                    checkpoint_id=checkpoint_id,
                    timestamp__range=(start, end)
                ).order_by('timestamp').first()

                actual_time = checkin.timestamp if checkin else None
                delay = None
                status = "-"
                #print("checkin.synced", checkin.synced)
                expected_time = make_aware(expected_time)
                formatted_time = None

                # if actual_time and expected_time:
                #     # Convert aware datetime to naive if needed
                #     if is_aware(actual_time):
                #         actual_time = actual_time.replace(tzinfo=None)
                #     if is_aware(expected_time):
                #         expected_time = expected_time.replace(tzinfo=None)
                if checkin and not checkin.synced:
                    status = "Missed"
                    formatted_time = "-"
                    delay = 0
                elif actual_time and expected_time:
                    # Convert to aware IST if naive
                    if not is_aware(actual_time):
                        actual_time = make_aware(actual_time, get_current_timezone())
                    if not is_aware(expected_time):
                        expected_time = make_aware(expected_time, get_current_timezone())

                    # Convert both to UTC

                    # Convert both to IST for display
                    ist = timezone.get_current_timezone()
                    #actual_time = actual_time.astimezone(ist)
                    expected_time = expected_time.astimezone(ist)

#                    actual_time = actual_time.astimezone(timezone.utc)
#                    expected_time = expected_time.astimezone(timezone.utc)


                #if actual_time:
                    delay = int((actual_time - expected_time).total_seconds() / 60)
                    if delay <= 15:
                        status = "On Time"
                    elif 15 < delay <= 30:
                        status = "Delayed"
                    else:
                        status = "Missed"

                checkpoint_name = Checkpoint.objects.get(id=checkpoint_id).label
                #print("EXPECTED TIME", expected_time)
                #print("ACT TIME", actual_time)
                print("checkin", checkin)

                try:
                    if checkin.synced is False:
                        actual_time=None
                except Exception as e:
                    print("eeeeeee", e)

                if actual_time:
                    formatted_time = actual_time.astimezone(timezone.get_current_timezone()).strftime('%Y-%m-%d %H:%M:%S')
                else:
                    formatted_time = None

                report.append({
                    'guard_id': guard.id,
                    'guard_name': guard.name,
                    'checkpoint_id': checkpoint_id,
                    'checkpoint_name': checkpoint_name,
#                    'expected_time': expected_time,
                    'expected_time': expected_time.strftime('%Y-%m-%d %H:%M:%S'),
                    #'actual_checkin_time': actual_time.strftime('%Y-%m-%d %H:%M:%S'),
                    'actual_checkin_time': formatted_time,
#                    'actual_checkin_time': actual_time.astimezone(timezone.get_current_timezone()).strftime('%Y-%m-%d %H:%M:%S'),
                    'status': status,
                    'delay_minutes': delay
                })

        serializer = CheckInReportSerializer(report, many=True)
        return Response(serializer.data)

class AttendanceCheckinListView(generics.ListAPIView):
    serializer_class = AttendanceCheckinDashboardSerializer

    def get_queryset(self):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        #today = timezone.localdate()
        date_filter = self.request.query_params.get("date_filter", "today")

        naive_dt = datetime.now()  # Naive datetime
        aware_dt = make_aware(naive_dt)  # Convert to aware
        today = localtime(aware_dt)   # Now it's safe to use

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
    
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from django.utils.timezone import make_aware, is_aware
from django.http import HttpResponse
from datetime import datetime, timedelta
from openpyxl import Workbook
from io import BytesIO

class DashboardCheckInReportExcelView(APIView):
    
#    permission_classes = [IsAuthenticated]
    permission_classes = [AllowAny]
    def get(self, request):
        # Parse filters
        filter_type = request.query_params.get('filter', 'today')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        user_id = request.query_params.get('user_id')
        location_id = request.query_params.get('location_id')

        today = timezone.now().date()
        now = timezone.now()

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
        elif filter_type == 'custom' and start_date and end_date:
            start = datetime.strptime(start_date, "%Y-%m-%d")
            end = datetime.strptime(end_date, "%Y-%m-%d") + timedelta(days=1)
        else:
            return Response({"error": "Invalid filter or missing dates"}, status=400)

        assignments = Assignment.objects.filter(
            start_date__lte=end.date(),
            end_date__gte=start.date()
        )

        if user_id:
            assignments = assignments.filter(guard_id=user_id)
        if location_id:
            assignments = assignments.filter(location_id=location_id)

        # Create Excel workbook
        wb = Workbook()
        ws = wb.active
        ws.title = "Check-In Report"

        # Header row
        headers = [
            'Guard ID', 'Guard Name', 'Checkpoint ID', 'Checkpoint Name',
            'Expected Time', 'Actual Check-In Time', 'Status', 'Delay (minutes)'
        ]
        ws.append(headers)

        for assignment in assignments:
            guard = assignment.guard
            shift = assignment.shift
            for cp in assignment.checkpoints:
                checkpoint_id = cp.get('checkpoint_id')
                expected_time_str = cp.get('time')
                expected_time = datetime.combine(assignment.start_date, datetime.strptime(expected_time_str, "%H:%M").time())

                checkin = CheckIn.objects.filter(
                    guard=guard,
                    shift=shift,
                    checkpoint_id=checkpoint_id,
                    timestamp__range=(start, end)
                ).order_by('timestamp').first()

                actual_time = checkin.timestamp if checkin else None
                delay = None
                status = "Missed"

                expected_time = make_aware(expected_time)

                if actual_time and expected_time:
                    if is_aware(actual_time):
                        actual_time = actual_time.replace(tzinfo=None)
                    if is_aware(expected_time):
                        expected_time = expected_time.replace(tzinfo=None)

                    delay = int((actual_time - expected_time).total_seconds() / 60)
                    if delay <= 15:
                        status = "On Time"
                    elif 15 < delay <= 30:
                        status = "Delayed"
                    else:
                        status = "Missed"

                checkpoint_name = Checkpoint.objects.get(id=checkpoint_id).label

                ws.append([
                    str(guard.id),
                    guard.name,
                    str(checkpoint_id),
                    checkpoint_name,
                    expected_time.strftime("%Y-%m-%d %H:%M"),
                    actual_time.strftime("%Y-%m-%d %H:%M") if actual_time else "",
                    status,
                    delay if delay is not None else ""
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

        return Response({"download_url": file_url})

from rest_framework.views import APIView
from rest_framework.response import Response
from django.utils.timezone import make_aware, localtime
from datetime import datetime
import os
from django.conf import settings
from openpyxl import Workbook
from .models import AttendanceCheckin
from .serializers import AttendanceCheckinDashboardSerializer

class AttendanceCheckinExportView(APIView):
    def get(self, request):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        date_filter = request.query_params.get("date_filter", "today")
        naive_dt = datetime.now()
        aware_dt = make_aware(naive_dt)
        today = localtime(aware_dt)

        if date_filter == "today":
            queryset = queryset.filter(checkin_time__date=today)
        elif date_filter == "week":
            start_week = today - timezone.timedelta(days=today.weekday())
            end_week = start_week + timezone.timedelta(days=6)
            queryset = queryset.filter(checkin_time__date__range=(start_week, end_week))
        elif date_filter == "month":
            queryset = queryset.filter(checkin_time__date__month=today.month)
        elif date_filter == "custom":
            # Handle custom date range
            start_date_str = request.query_params.get("start_date")
            end_date_str = request.query_params.get("end_date")
            try:
                if start_date_str and end_date_str:
                    start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
                    end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
                    queryset = queryset.filter(checkin_time__date__range=(start_date, end_date))
            except ValueError:
                return Response({"error": "Invalid custom date format. Use YYYY-MM-DD."}, status=400)

        # Additional filters
        guard_id = request.query_params.get("guard")
        if guard_id:
            queryset = queryset.filter(guard_id=guard_id)

        location_id = request.query_params.get("location")
        if location_id:
            queryset = queryset.filter(org_location_id=location_id)

        shift_id = request.query_params.get("shift")
        if shift_id:
            queryset = queryset.filter(shift_id=shift_id)

        status = request.query_params.get("status")
        if status:
            queryset = queryset.filter(status=status)

        defaulters = request.query_params.get("defaulters")
        if defaulters == "true":
            queryset = queryset.filter(checkin_time__date=today, checkout_time__isnull=True)

        # Create Excel
        wb = Workbook()
        ws = wb.active
        ws.title = "Attendance Checkins"

        # # Header
        # ws.append(["Guard", "Shift", "Location", "Checkin Time", "Checkout Time", "Status"])

        # for obj in queryset:
        #     ws.append([
        #         obj.guard.name,
        #         obj.shift.name if obj.shift else "",
        #         obj.org_location.name if obj.org_location else "",
        #         obj.checkin_time.strftime('%Y-%m-%d %H:%M:%S') if obj.checkin_time else "",
        #         obj.checkout_time.strftime('%Y-%m-%d %H:%M:%S') if obj.checkout_time else "",
        #         obj.status
        #     ])


        # Header with reordered columns and added Date + Duration
        ws.append(["Date", "Name", "Shift", "Location", "Checkin Time", "Checkout Time", "Duration (HH:MM)","Status", "Remarks"])

        # for obj in queryset:
        #     checkin = obj.checkin_time
        #     checkout = obj.checkout_time
        #     duration = ""
        #     if checkin and checkout:
        #         delta = checkout - checkin
        #         hours, remainder = divmod(delta.total_seconds(), 3600)
        #         minutes = remainder // 60
        #         duration = f"{int(hours):02}:{int(minutes):02}"

        #     ws.append([
        #         checkin.strftime('%Y-%m-%d') if checkin else "",
        #         obj.shift.name if obj.shift else "",
        #         obj.guard.name,
        #         obj.org_location.name if obj.org_location else "",
        #         checkin.strftime('%Y-%m-%d %H:%M:%S') if checkin else "",
        #         checkout.strftime('%Y-%m-%d %H:%M:%S') if checkout else "",
        #         obj.status,
        #         duration
        #     ])

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
                    checkin_diff = abs((checkin - shift_start).total_seconds()) / 60
                    if checkin_diff <= 30:
                        other_statuses.append("On-time Checked-in")
                    elif checkin > shift_start + timedelta(minutes=30):
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

        # Save file
        filename = f"attendance_export_{datetime.now().strftime('%Y%m%d%H%M%S')}.xlsx"
        filepath = os.path.join(settings.MEDIA_ROOT, filename)
        wb.save(filepath)

        # Return URL
        file_url = request.build_absolute_uri(settings.MEDIA_URL + filename)
        return Response({"file_url": file_url})


from rest_framework.viewsets import ViewSet
from rest_framework.response import Response
from rest_framework.decorators import action
from django.db.models import Q
from django.utils.dateparse import parse_date
from django.utils.timezone import now
from calendar import monthrange
from datetime import datetime, timedelta
from scheduler.models import Assignment, Location
from dashboard.models import AttendanceCheckin
from authapp.models import User

from collections import defaultdict

class MonthlyAttendanceSummaryViewSet(ViewSet):

    @action(detail=False, methods=["get"])
    def summary(self, request):
        location_id = request.query_params.get("location_id")
        user_id = request.query_params.get("user_id")
        month = request.query_params.get("month")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        if month:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        elif start_date and end_date:
            start_date = parse_date(start_date)
            end_date = parse_date(end_date)
        else:
            today = now().date()
            start_date = today.replace(day=1)
            end_date = today

        assignments = Assignment.objects.filter(
            start_date__lte=end_date,
            end_date__gte=start_date,
            is_deleted=False
        )

        if location_id:
            assignments = assignments.filter(location_id=location_id)
        if user_id:
            assignments = assignments.filter(guard_id=user_id)

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

        summary = []

        for (guard_id, location_id), data in grouped.items():
            guard = data["guard"]
            location = data["location"]
            row = {
                "name": guard.name,
                "location": location
            }

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

            summary.append(row)

        return Response(summary)


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


from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from django.http import HttpResponse
from openpyxl import Workbook
from datetime import datetime, timedelta
from calendar import monthrange
from django.utils.dateparse import parse_date
from django.utils.timezone import now

class MonthlyAttendanceExcelViewSet(ViewSet):

    @action(detail=False, methods=["get"])

    def export_excel(self, request):
        location_id = request.query_params.get("location_id")
        user_id = request.query_params.get("user_id")
        month = request.query_params.get("month")
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        if month:
            year, month_num = map(int, month.split("-"))
            start_date = datetime(year, month_num, 1).date()
            end_date = datetime(year, month_num, monthrange(year, month_num)[1]).date()
        elif start_date and end_date:
            start_date = parse_date(start_date)
            end_date = parse_date(end_date)
        else:
            today = now().date()
            start_date = today.replace(day=1)
            end_date = today

        assignments = Assignment.objects.filter(
            start_date__lte=end_date,
            end_date__gte=start_date,
            is_deleted=False
        )

        if location_id:
            assignments = assignments.filter(location_id=location_id)
        if user_id:
            assignments = assignments.filter(guard_id=user_id)

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

        wb = Workbook()
        ws = wb.active
        ws.title = "Attendance Summary"

        headers = ["Name", "Location"] + [date.strftime("%d-%b") for date in date_range]
        ws.append(headers)

        for (guard_id, location_id), data in grouped.items():
            guard = data["guard"]
            location = data["location"]
            row = [guard.name, location]

            for date in date_range:
                active_assignments = [
                    a for a in data["assignments"]
                    if a.start_date <= date <= a.end_date
                ]

                if not active_assignments:
                    row.append("-")
                    continue

                has_checkin = AttendanceCheckin.objects.filter(
                    guard=guard,
                    assignment__in=active_assignments,
                    checkin_time__date=date
                ).exists()

                row.append("P" if has_checkin else "A")

            ws.append(row)

        response = HttpResponse(content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
        filename = f"attendance_summary_{start_date.strftime('%Y%m%d')}_{end_date.strftime('%Y%m%d')}.xlsx"
        response["Content-Disposition"] = f'attachment; filename="{filename}"'
        wb.save(response)
        return response

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

