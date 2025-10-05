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
from .models import AttendanceCheckin
from .serializers import AttendanceCheckinSerializer
from scheduler.models import Assignment
from django.utils.timezone import localtime, make_aware
from datetime import  datetime, timedelta
from django.core.serializers import serialize
import json
from django.utils.timezone import now
import pytz

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

    @action(detail=False, methods=["get"])
    def shift_today(self, request):
        """Return today's shift info + flags for checkin/checkout buttons"""
        user = request.user
        user_id = user.id
        # today = date.today()
        now = localtime()
        current_time = localtime().time()
        today = localtime().date()
        print(user)
        print(user.id)
        #print(now)
        print(request)
        
        assignments = Assignment.objects.filter(guard_id=user_id,start_date__lte=today,end_date__gte=today)
        result = []
        print("asssss",assignments)
                # Find assignment for today
        assignment = Assignment.objects.filter(
            guard_id=user_id,
            start_date__lte=today,
            end_date__gte=today
        ).first()
        # .select_related("shift", "location").first()
        # print("hi")
        # Assuming 'assignment' is your queryset
        #json_data = serialize('json', assignment)

        # Optional: pretty-print the JSON
        #parsed = json.loads(json_data)
        #print(json.dumps(parsed, indent=4))
        #print(assignment)
        if not assignment:
            return Response({
                "has_shift": False,
                "show_checkin": False,
                "show_checkout": False,
                "message": "No shifts today"
            }, status=status.HTTP_200_OK)

        shift = assignment.shift
        location = assignment.location
        print("SSSSSSS", shift)
        print("LLLLLLL",location)
        # Find existing attendance record
        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__date=today
        ).first()

        # Default flags
        show_checkin = False
        show_checkout = False
        message = ""

        shift_start = shift.start_time
        shift_end = shift.end_time

        # now = localtime()
        # shift_start_dt = datetime.combine(now.date(), shift.start_time)
        # shift_end_dt = datetime.combine(now.date(), shift.end_time)

        now = localtime()

        ist = pytz.timezone('Asia/Kolkata')
        local_now = localtime()
        current_time = local_now.astimezone(ist)

        now = current_time

        shift_start_dt = make_aware(datetime.combine(now.date(), shift.start_time))
        shift_end_dt = make_aware(datetime.combine(now.date(), shift.end_time))

        # Define IST timezone
        ist = pytz.timezone('Asia/Kolkata')

        # Make shift_start_dt and shift_end_dt timezone-aware (assuming they are naive)
        shift_start_dt = shift_start_dt.astimezone(ist)
        shift_end_dt = shift_end_dt.astimezone(ist)

        # Calculate check-in window
        earliest_checkin = shift_start_dt - timedelta(minutes=30)
        latest_checkin = shift_end_dt

        # earliest_checkin = shift_start_dt - timedelta(minutes=30)
        # latest_checkin = shift_end_dt

        if not attendance:
            earliest_checkin = shift_start_dt - timedelta(minutes=30)
            latest_checkin = shift_end_dt

            print("NNNNN",now)   
            print("ECCCC", earliest_checkin) 
            print("LLLLCCCC",latest_checkin)

            if earliest_checkin <= now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif now < earliest_checkin:
                message = "Too early to check in"
            else:
                message = "Shift has ended, you missed check-in"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                earliest_checkout = shift_start_dt
                latest_checkout = shift_end_dt + timedelta(minutes=30)

                if earliest_checkout <= now <= latest_checkout:
                    show_checkout = True
                    message = "You are checked in, please checkout when done"
                elif now < earliest_checkout:
                    message = "Too early to checkout"
                else:
                    message = "Checkout window closed"
            elif attendance.checkout_time:
                message = "Shift completed, already checked out"

        # --- CHECKIN / CHECKOUT LOGIC ---
        # if not attendance:
        #     # No checkin yet
        #     earliest_checkin = (datetime.combine(today, shift_start) - timedelta(minutes=30)).time()
        #     latest_checkin = shift_end

        #     if earliest_checkin <= now <= latest_checkin:
        #         show_checkin = True
        #         message = "You can check in"
        #     elif now < earliest_checkin:
        #         message = "Too early to check in"
        #     else:
        #         message = "Shift has ended, you missed check-in"
        # else:
        #     # Already checked in
        #     if attendance.checkin_time and not attendance.checkout_time:
        #         earliest_checkout = shift_start
        #         latest_checkout = (datetime.combine(today, shift_end) + timedelta(minutes=30)).time()

        #         if earliest_checkout <= now <= latest_checkout:
        #             show_checkout = True
        #             message = "You are checked in, please checkout when done"
        #         elif now < earliest_checkout:
        #             message = "Too early to checkout"
        #         else:
        #             message = "Checkout window closed"
        #     elif attendance.checkout_time:
        #         message = "Shift completed, already checked out"

        # --- Response ---
        return Response({
            "has_shift": True,
            "shift_id": str(shift.id),
            "shift_start": shift.start_time,
            "shift_end": shift.end_time,
            "location_name": location.name,
            #"location_lat": location.latitude,
            #"location_lon": location.longitude,
            "show_checkin": show_checkin,
            "show_checkout": show_checkout,
            "message": message
        }, status=status.HTTP_200_OK)

    # def get_today_assignment(self, user):
    #     today = date.today()
    #     return Assignment.objects.filter(
    #         guard=user,
    #         start_date__lte=today,
    #         end_date__gte=today
    #     ).first()

    # ----------------------
    # CHECK-IN
    # ----------------------
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
        if distance > 50:
            return Response({"error": "Not within 50m of assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        attendance, created = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            checkin_time__date=date.today()
        )

        if attendance.checkin_time:
            return Response({"message": "Already checked in"}, status=status.HTTP_400_BAD_REQUEST)

        attendance.checkin_time = timezone.now()
        attendance.latitude = lat
        attendance.longitude = lon
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_201_CREATED)

    # ----------------------
    # CHECK-OUT
    # ----------------------
    @action(detail=False, methods=["post"])
    def checkout(self, request):
        user = request.user
        assignment = self.get_today_assignment(user)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            checkin_time__date=date.today()
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

        if attendance.checkout_time:
            return Response({"message": "Already checked out"}, status=status.HTTP_400_BAD_REQUEST)

        attendance.checkout_time = timezone.now()
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_200_OK)

    # ----------------------
    # DASHBOARD (with date range filter)
    # ----------------------
    @action(detail=False, methods=["get"])
    def dashboard(self, request):
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        # Default: today
        if not start_date:
            start_date = date.today().strftime("%Y-%m-%d")
        if not end_date:
            end_date = date.today().strftime("%Y-%m-%d")

        try:
            start_date = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date = datetime.strptime(end_date, "%Y-%m-%d").date()
        except ValueError:
            return Response({"error": "Invalid date format. Use YYYY-MM-DD."}, status=status.HTTP_400_BAD_REQUEST)

        records = AttendanceCheckin.objects.filter(
            checkin_time_date_gte=start_date,
            checkin_time_date_lte=end_date
        ).select_related("guard", "shift", "assignment")

        data = []
        for record in records:
            data.append({
                "name": record.guard.get_full_name() or record.guard.username,
                "date": record.checkin_time.date() if record.checkin_time else record.assignment.start_date,
                "shift_time": f"{record.shift.start_time} - {record.shift.end_time}",
                "checkin_time": record.checkin_time,
                "checkout_time": record.checkout_time,
                "status": record.status,
                "remarks": record.remarks,
                "od_remarks": record.od_remarks
            })

        return Response(data, status=status.HTTP_200_OK)