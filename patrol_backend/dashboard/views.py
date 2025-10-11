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
from .serializers import AttendanceCheckinSerializer
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
        shift_end_dt = make_aware(datetime.combine(today, shift.end_time)) if is_naive(datetime.combine(today, shift.end_time)) else datetime.combine(today, shift.end_time)

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
                status = "Missed"

                expected_time = make_aware(expected_time)

                if actual_time and expected_time:
                    # Convert aware datetime to naive if needed
                    if is_aware(actual_time):
                        actual_time = actual_time.replace(tzinfo=None)
                    if is_aware(expected_time):
                        expected_time = expected_time.replace(tzinfo=None)

                #if actual_time:
                    delay = int((actual_time - expected_time).total_seconds() / 60)
                    if delay <= 15:
                        status = "On Time"
                    elif 15 < delay <= 30:
                        status = "Delayed"
                    else:
                        status = "Missed"

                checkpoint_name = Checkpoint.objects.get(id=checkpoint_id).label

                report.append({
                    'guard_id': guard.id,
                    'guard_name': guard.name,
                    'checkpoint_id': checkpoint_id,
                    'checkpoint_name': checkpoint_name,
                    'expected_time': expected_time,
                    'actual_checkin_time': actual_time,
                    'status': status,
                    'delay_minutes': delay
                })

        serializer = CheckInReportSerializer(report, many=True)
        return Response(serializer.data)

class AttendanceCheckinListView(generics.ListAPIView):
    serializer_class = AttendanceCheckinSerializer

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