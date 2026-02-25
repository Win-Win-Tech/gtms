# Standard library imports
import csv
import json
import logging
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
from scheduler.models import Assignment, Checkpoint, Location, Shift, CheckpointTemplate
from tourlog.models import TourLog
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    get_user_now,
    to_user_timezone,
    convert_date_range_to_utc,
    combine_date_time_in_user_tz
)

from .models import AttendanceCheckin, CheckInLog
from .serializers import (
    AttendanceCheckinDashboardSerializer,
    AttendanceCheckinSerializer,
    CheckInReportSerializer,
)

# Setup logger
logger = logging.getLogger(__name__)

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

    # ===== OLD APIs (for current mobile app - no overnight enhancements) =====
    
    def get_today_assignment(self, user, request=None):
        """OLD: Simple assignment lookup - today only, no overnight handling"""
        if request:
            user_tz = get_user_timezone_from_request(request)
        else:
            from patrol_backend.utils.timezone_utils import get_user_timezone
            user_tz = get_user_timezone(user)
        
        today = get_user_today(user_tz)
        return Assignment.objects.filter(
            guard_id=user.id,
            start_date__lte=today,
            end_date__gte=today
        ).first()

    # ===== V2 APIs (enhanced with overnight shift + auto-assignment support) =====

    def get_today_assignment_v2(self, user, request=None):
        """V2: Enhanced assignment lookup with overnight shift support.
           Returns a tuple: (assignment, logical_start_date)
        """
        if request:
            user_tz = get_user_timezone_from_request(request)
        else:
            from patrol_backend.utils.timezone_utils import get_user_timezone
            user_tz = get_user_timezone(user)
        
        today = get_user_today(user_tz)
        yesterday = today - timedelta(days=1)
        user_now = get_user_now(user_tz)
        
        # Check assignments that overlap with today OR yesterday (for overnight shifts)
        assignments = Assignment.objects.filter(
            guard_id=user.id,
            start_date__lte=today,
            end_date__gte=yesterday
        ).select_related('shift', 'location')
        
        # Find the active shift instance
        for assignment in assignments:
            shift = assignment.shift
            if not shift:
                continue
                
            is_overnight = shift.end_time <= shift.start_time
            
            # Check if this is yesterday's overnight shift still active today
            if assignment.start_date <= yesterday and is_overnight:
                # Add 30 min grace period for late checkout
                shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz) + timedelta(minutes=30)
                if user_now <= shift_end_dt:
                    return assignment, yesterday  # Still active from yesterday
            
            # Check if this is today's shift (regular or overnight starting today)
            if assignment.start_date <= today:
                # Add 30 min early checkin grace period
                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz) - timedelta(minutes=30)
                
                if is_overnight:
                    # For overnight shifts, calculate logical start and end boundaries including grace periods
                    actual_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                    actual_end_dt = combine_date_time_in_user_tz(today + timedelta(days=1), shift.end_time, user_tz)
                    
                    window_start = actual_start_dt - timedelta(minutes=30)
                    window_end = actual_end_dt + timedelta(minutes=30)
                    
                    # Also check if it's past midnight and we are in yesterday's shift window
                    yesterday_start_dt = combine_date_time_in_user_tz(yesterday, shift.start_time, user_tz)
                    yesterday_end_dt = combine_date_time_in_user_tz(yesterday + timedelta(days=1), shift.end_time, user_tz)
                    
                    yesterday_window_start = yesterday_start_dt - timedelta(minutes=30)
                    yesterday_window_end = yesterday_end_dt + timedelta(minutes=30)

                    if yesterday_window_start <= user_now <= yesterday_window_end:
                        return assignment, yesterday
                    elif window_start <= user_now <= window_end:
                         return assignment, today
                else:
                    # Regular shift boundaries
                    shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz) + timedelta(minutes=30)
                    if shift_start_dt <= user_now <= shift_end_dt:
                        return assignment, today
        
        return None, None

    def find_and_create_default_assignment(self, user, checkin_time_user, user_tz, location_lat, location_lon):
        """
        Find default shift matching check-in time and create assignment.
        Returns assignment if created, None otherwise.
        """
        checkin_time = checkin_time_user.time()
        checkin_date = checkin_time_user.date()
        
        # Get all default shifts for the user's location (or all locations if location not specified)
        # First, try to find location based on check-in coordinates
        # Note: Some locations in DB may not have lat/lon, so we only check locations that have coordinates
        location = None
        if location_lat and location_lon:
            # Find nearest location (within reasonable distance, e.g., 1km)
            # Only check locations that have both latitude and longitude
            locations = Location.objects.filter(
                is_deleted=False,
                latitude__isnull=False,
                longitude__isnull=False
            )
            min_distance = float('inf')
            for loc in locations:
                dist = geodesic((location_lat, location_lon), (loc.latitude, loc.longitude)).meters
                if dist < min_distance and dist < 1000:  # Within 1km
                    min_distance = dist
                    location = loc
        
        # Fallback: If location not found via coordinates (or coordinates not provided, or locations don't have coordinates),
        # use user's location from token
        if not location and user.location:
            location = user.location
        
        # Get default shifts
        if location:
            default_shifts = Shift.objects.filter(
                is_default=True,
                is_deleted=False,
                location=location
            ).select_related('checkpoint_template')
        else:
            # If no location found, get all default shifts (user will need to select location)
            default_shifts = Shift.objects.filter(
                is_default=True,
                is_deleted=False
            ).select_related('checkpoint_template')
        
        if not default_shifts.exists():
            return None
        
        # Find shift that matches check-in time
        # Priority 1: Nearest shift based on start time (±2 hours)
        # Priority 2: If user is in active shift ending within 2 hours, and upcoming shift starts within 2 hours, assign upcoming shift
        matching_shift = None
        best_match_score = -1
        WINDOW_MINUTES = 120  # ±2 hours = 120 minutes
        
        checkin_minutes = checkin_time.hour * 60 + checkin_time.minute
        
        # First pass: Find all shifts within ±2 hours of start time (nearest shift logic)
        candidate_shifts = []
        active_shift_ending_soon = None
        
        for shift in default_shifts:
            is_overnight = shift.end_time <= shift.start_time
            start_minutes = shift.start_time.hour * 60 + shift.start_time.minute
            end_minutes = shift.end_time.hour * 60 + shift.end_time.minute
            matches = False
            minutes_from_start = None
            is_active = False
            is_upcoming = False
            
            # Check if check-in is within active shift window
            if is_overnight:
                # Overnight shift: Check if in active window
                if checkin_time >= shift.start_time or checkin_time < shift.end_time:
                    is_active = True
                    # Calculate minutes until end
                    if checkin_time >= shift.start_time:
                        # Check-in is after start, end is next day
                        minutes_until_end = (24 * 60 - checkin_minutes) + end_minutes
                    else:
                        # Check-in is before end (after midnight part)
                        minutes_until_end = end_minutes - checkin_minutes
                    
                    # Check if ending within 2 hours
                    if minutes_until_end <= WINDOW_MINUTES:
                        active_shift_ending_soon = shift
            else:
                # Regular shift: Check if in active window
                if shift.start_time <= checkin_time <= shift.end_time:
                    is_active = True
                    minutes_until_end = end_minutes - checkin_minutes
                    if minutes_until_end <= WINDOW_MINUTES:
                        active_shift_ending_soon = shift
            
            # Check if within ±2 hours of start time
            if is_overnight:
                if checkin_time >= shift.start_time:
                    minutes_from_start = checkin_minutes - start_minutes
                    if minutes_from_start <= WINDOW_MINUTES:
                        matches = True
                else:
                    minutes_until_start = start_minutes - checkin_minutes
                    if minutes_until_start <= WINDOW_MINUTES:
                        matches = True
                        minutes_from_start = minutes_until_start
                        is_upcoming = True
                    else:
                        # Check yesterday's start
                        minutes_since_yesterday_start = (24 * 60 - start_minutes) + checkin_minutes
                        if minutes_since_yesterday_start <= WINDOW_MINUTES:
                            matches = True
                            minutes_from_start = minutes_since_yesterday_start
            else:
                if checkin_time >= shift.start_time:
                    minutes_from_start = checkin_minutes - start_minutes
                    if minutes_from_start <= WINDOW_MINUTES:
                        matches = True
                else:
                    minutes_until_start = start_minutes - checkin_minutes
                    if minutes_until_start <= WINDOW_MINUTES:
                        matches = True
                        minutes_from_start = minutes_until_start
                        is_upcoming = True
            
            if matches and minutes_from_start is not None:
                candidate_shifts.append({
                    'shift': shift,
                    'minutes_from_start': minutes_from_start,
                    'is_upcoming': is_upcoming,
                    'is_active': is_active
                })
        
        # Priority logic:
        # 1. If active shift ending soon → find next upcoming shift (any shift starting after current time)
        # 2. Otherwise → assign nearest shift (closest to start time, within ±2 hours)
        if active_shift_ending_soon:
            # Find next upcoming shift (any shift that starts after current time, no ±2 hours restriction)
            upcoming_shift = None
            min_minutes_until_start = float('inf')
            
            for shift in default_shifts:
                if shift == active_shift_ending_soon:
                    continue  # Skip the active shift
                
                is_overnight = shift.end_time <= shift.start_time
                start_minutes = shift.start_time.hour * 60 + shift.start_time.minute
                minutes_until_start = None
                
                if is_overnight:
                    # For overnight shift, check if start time is today or tomorrow
                    if checkin_time >= shift.start_time:
                        # Start time already passed today, next occurrence is tomorrow
                        minutes_until_start = (24 * 60 - checkin_minutes) + start_minutes
                    else:
                        # Start time is later today
                        minutes_until_start = start_minutes - checkin_minutes
                else:
                    # Regular shift
                    if checkin_time >= shift.start_time:
                        # Start time already passed today, next occurrence is tomorrow
                        minutes_until_start = (24 * 60 - checkin_minutes) + start_minutes
                    else:
                        # Start time is later today
                        minutes_until_start = start_minutes - checkin_minutes
                
                # Find the shift that starts soonest (next upcoming)
                if minutes_until_start < min_minutes_until_start:
                    min_minutes_until_start = minutes_until_start
                    upcoming_shift = shift
            
            if upcoming_shift:
                # Assign next upcoming shift
                matching_shift = upcoming_shift
            else:
                # No upcoming shift found, assign nearest shift from candidates
                if candidate_shifts:
                    best_candidate = min(candidate_shifts, key=lambda x: x['minutes_from_start'])
                    matching_shift = best_candidate['shift']
        else:
            # No active shift ending soon, assign nearest shift (within ±2 hours)
            if candidate_shifts:
                best_candidate = min(candidate_shifts, key=lambda x: x['minutes_from_start'])
                matching_shift = best_candidate['shift']
        
        if not matching_shift:
            return None
        
        # Get checkpoints from template
        checkpoints = []
        if matching_shift.checkpoint_template:
            template = matching_shift.checkpoint_template
            if template.checkpoints:
                checkpoints = template.checkpoints  # Already in format [{"checkpoint_id": "uuid", "time": "HH:MM"}]
        
        # Determine assignment date range
        start_date = checkin_date
        if matching_shift.end_time <= matching_shift.start_time:
            # Overnight shift
            # If check-in time is before end_time (e.g., 1 AM), shift started yesterday
            if checkin_time < matching_shift.end_time:
                start_date = checkin_date - timedelta(days=1)  # Shift started yesterday
            # End date is the day the shift ends (today for overnight shifts)
            end_date = checkin_date
        else:
            # Regular shift - same day
            end_date = checkin_date
        
        # Create assignment
        assignment = Assignment.objects.create(
            guard=user,
            location=matching_shift.location,
            shift=matching_shift,
            start_date=start_date,
            end_date=end_date,
            checkpoints=checkpoints
        )
        
        return assignment

    @action(detail=False, methods=["get"])
    def shift_today(self, request):
        """OLD: Simple shift_today for current mobile app"""
        user = request.user
        user_id = user.id

        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        today = user_now.date()

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

        start_utc, end_utc = convert_date_range_to_utc(today, today, user_tz)

        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__gte=start_utc,
            checkin_time__lt=end_utc + timedelta(days=1)
        ).first()

        show_checkin = False
        show_checkout = False
        message = ""

        shift_start_dt_user = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
        shift_start_dt_user = shift_start_dt_user.astimezone(user_tz)
  
        if shift.end_time <= shift.start_time:
            shift_end_dt_user = combine_date_time_in_user_tz(today + timedelta(days=1), shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        else:
            shift_end_dt_user = combine_date_time_in_user_tz(today, shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        
        earliest_checkin = shift_start_dt_user - timedelta(minutes=30)
        latest_checkin = shift_end_dt_user
        earliest_checkout = shift_start_dt_user
        latest_checkout = shift_end_dt_user + timedelta(minutes=30)

        if not attendance:
            if earliest_checkin <= user_now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif user_now < earliest_checkin:
                message = "Too early to check in"
            else:
                message = "Shift ended"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                checkin_time_user = to_user_timezone(attendance.checkin_time, user_tz)
                if user_now <= shift_end_dt_user:
                    show_checkout = True
                    message = "You are checked in, checkout when done"
                else:
                    message = "Shift ended"
            elif attendance.checkin_time and attendance.checkout_time:
                checkout_time_user = to_user_timezone(attendance.checkout_time, user_tz)
                if user_now <= shift_end_dt_user:
                    start_utc, end_utc = convert_date_range_to_utc(today, today, user_tz)
                    latest_checkin = CheckInLog.objects.filter(
                        guard=user,
                        assignment=assignment,
                        shift=shift,
                        org_location=location,
                        type="checkin",
                        timestamp__gte=start_utc,
                        timestamp__lt=end_utc + timedelta(days=1),
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
                    message = "Shift ended"

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

    @action(detail=False, methods=["get"], url_path="shift_today_v2")
    def shift_today_v2(self, request):
        """V2: Enhanced shift_today with overnight shift support"""
        user = request.user
        user_id = user.id

        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        today = user_now.date()
        yesterday = today - timedelta(days=1)

        assignments = Assignment.objects.filter(
            guard_id=user_id,
            start_date__lte=today,
            end_date__gte=yesterday
        ).select_related('shift', 'location').order_by('-start_date', '-created_on')
        assignment = None
        shift_start_date = today
        
        # 1) Prefer the assignment that is active right now.
        for assgn in assignments:
            shift = assgn.shift
            if not shift:
                continue
                
            is_overnight = shift.end_time <= shift.start_time

            if is_overnight:
                # If it's after midnight and before shift end, active shift instance started yesterday.
                logical_start_date = yesterday if user_now.time() < shift.end_time else today
                if not (assgn.start_date <= logical_start_date <= assgn.end_date):
                    continue
                if user_now.time() >= shift.start_time or user_now.time() < shift.end_time:
                    assignment = assgn
                    shift_start_date = logical_start_date
                    break
            else:
                if not (assgn.start_date <= today <= assgn.end_date):
                    continue
                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz)
                if shift_start_dt <= user_now <= shift_end_dt:
                    assignment = assgn
                    shift_start_date = today
                    break

        # 2) If nothing is active, still return today's scheduled shift (pre-shift behavior).
        if not assignment:
            best_scheduled = None
            best_distance = None
            for assgn in assignments:
                shift = assgn.shift
                if not shift:
                    continue
                if not (assgn.start_date <= today <= assgn.end_date):
                    continue

                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                distance_seconds = abs((shift_start_dt - user_now).total_seconds())

                if best_distance is None or distance_seconds < best_distance:
                    best_distance = distance_seconds
                    best_scheduled = assgn

            if best_scheduled:
                assignment = best_scheduled
                shift_start_date = today

        if not assignment:
            return Response({
                "has_shift": False,
                "show_checkin": True,
                "show_checkout": False,
                "message": "No shifts today"
            }, status=status.HTTP_200_OK)

        shift = assignment.shift
        location = assignment.location

        # Use shift_start_date (could be yesterday for overnight) for UTC conversion
        start_utc, end_utc = convert_date_range_to_utc(shift_start_date, shift_start_date, user_tz)
        
        # Exact logical boundary search window (D 00:00:00 to D+1 23:59:59)
        search_start_utc = start_utc
        search_end_utc = end_utc + timedelta(days=1)

        attendance = AttendanceCheckin.objects.filter(
            guard=user, shift=shift, assignment=assignment,
            checkin_time__gte=search_start_utc,
            checkin_time__lt=search_end_utc
        ).first()

        show_checkin = False
        show_checkout = False
        message = ""

        # Calculate shift window using shift_start_date
        shift_start_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.start_time, user_tz)
        shift_start_dt_user = shift_start_dt_user.astimezone(user_tz)
  
        if shift.end_time <= shift.start_time:
            # Overnight shift - end is next day
            shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date + timedelta(days=1), shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        else:
            shift_end_dt_user = combine_date_time_in_user_tz(shift_start_date, shift.end_time, user_tz)
            shift_end_dt_user = shift_end_dt_user.astimezone(user_tz)
        
        # Define check-in and check-out windows (in user timezone)
        earliest_checkin = shift_start_dt_user - timedelta(minutes=30)
        latest_checkin = shift_end_dt_user
        earliest_checkout = shift_start_dt_user
        latest_checkout = shift_end_dt_user + timedelta(minutes=30)

        # Scenario logic (all comparisons in user timezone)
        if not attendance:
            # Scenario 1: No check-in yet
            if earliest_checkin <= user_now <= latest_checkin:
                show_checkin = True
                message = "You can check in"
            elif user_now < earliest_checkin:
                message = "Too early to check in"
            else:
                # Scenario 5: Shift ended without check-out
                message = "Shift ended"
        else:
            if attendance.checkin_time and not attendance.checkout_time:
                # Convert stored UTC time to user timezone for comparison
                checkin_time_user = to_user_timezone(attendance.checkin_time, user_tz)
                if user_now <= shift_end_dt_user:
                    # Scenario 2: Checked in, not yet checked out
                    show_checkout = True
                    message = "You are checked in, checkout when done"
                else:
                    # Scenario 5: Shift ended, no checkout
                    message = "Shift ended"
            elif attendance.checkin_time and attendance.checkout_time:
                checkout_time_user = to_user_timezone(attendance.checkout_time, user_tz)
                if user_now <= shift_end_dt_user:
                    # Check if user has checked in again after checkout
                    # Use expanded date range for query
                    latest_checkin = CheckInLog.objects.filter(
                        guard=user,
                        assignment=assignment,
                        shift=shift,
                        org_location=location,
                        type="checkin",
                        timestamp__gte=search_start_utc,
                        timestamp__lt=search_end_utc,
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
        """OLD: Simple checkin for current mobile app"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment = self.get_today_assignment(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))
        distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters

        if distance > 100:
            return Response({"error": "Not within >100m of assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        user_today = get_user_today(user_tz)
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)

        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon
        )

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={'created_on': timezone.now()}
        )
        attendance_list = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=start_utc,
            created_on__lt=end_utc + timedelta(days=1)
        )
        if attendance_list.exists():
            attendance = attendance_list.first()

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=start_utc,
            timestamp__lt=end_utc + timedelta(days=1)
        ).order_by("timestamp").first()

        if earliest_checkin:
            attendance.checkin_time = earliest_checkin.timestamp
        attendance.latitude = earliest_checkin.latitude
        attendance.longitude = earliest_checkin.longitude
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"])
    def checkout(self, request):
        """OLD: Simple checkout for current mobile app"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment = self.get_today_assignment(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        user_today = get_user_today(user_tz)
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=start_utc,
            created_on__lt=end_utc + timedelta(days=1)
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

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
            timestamp__gte=start_utc,
            timestamp__lt=end_utc + timedelta(days=1)
        ).order_by("-timestamp").first()

        if latest_checkout:
            attendance.checkout_time = latest_checkout.timestamp
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_200_OK)

    # ===== V2 CHECKIN/CHECKOUT (enhanced with overnight + auto-assignment) =====

    @action(detail=False, methods=["post"], url_path="checkin_v2")
    def checkin_v2(self, request):
        """V2: Enhanced checkin with overnight shift + auto-assignment support"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        user_now = get_user_now(user_tz)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        # If no assignment, try to create one from default shift
        if not assignment:
            # lat = float(request.data.get("latitude", 0))
            # lon = float(request.data.get("longitude", 0))
            # assignment = self.find_and_create_default_assignment(user, user_now, user_tz, lat, lon)
            
            # if not assignment:
            #     return Response({"message": "No shifts today and no default shift found"}, status=status.HTTP_400_BAD_REQUEST)
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))
        
        # Validate location if location has coordinates
        if org_location.latitude and org_location.longitude:
            distance = geodesic((lat, lon), (org_location.latitude, org_location.longitude)).meters
            if distance > 100:
                return Response({"error": "Not within >100m of assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        # Use shift_start_date (could be yesterday for overnight) for precise UTC conversion
        start_utc, end_utc = convert_date_range_to_utc(shift_start_date, shift_start_date, user_tz)
        
        # Exact logical boundary search window (D 00:00:00 to D+1 23:59:59)
        search_start_utc = start_utc
        search_end_utc = end_utc + timedelta(days=1)

        CheckInLog.objects.create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            latitude=lat,
            longitude=lon
        )

        attendance, _ = AttendanceCheckin.objects.get_or_create(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            defaults={'created_on': timezone.now()}
        )
        
        attendance_list = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=search_start_utc,
            created_on__lt=search_end_utc
        )
        if attendance_list.exists():
            attendance = attendance_list.first()

        earliest_checkin = CheckInLog.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            type="checkin",
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("timestamp").first()

        if earliest_checkin:
            attendance.checkin_time = earliest_checkin.timestamp
            attendance.latitude = earliest_checkin.latitude
            attendance.longitude = earliest_checkin.longitude
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance, context={'request': request}).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="checkout_v2")
    def checkout_v2(self, request):
        """V2: Enhanced checkout with overnight shift support"""
        user = request.user
        user_tz = get_user_timezone_from_request(request)
        assignment, shift_start_date = self.get_today_assignment_v2(user, request)

        if not assignment:
            return Response({"message": "No shifts today"}, status=status.HTTP_400_BAD_REQUEST)

        shift = assignment.shift
        org_location = assignment.location
        lat = float(request.data.get("latitude"))
        lon = float(request.data.get("longitude"))

        # Use shift_start_date (could be yesterday for overnight) for precise UTC conversion
        start_utc, end_utc = convert_date_range_to_utc(shift_start_date, shift_start_date, user_tz)
        
        # Exact logical boundary search window (D 00:00:00 to D+1 23:59:59)
        search_start_utc = start_utc
        search_end_utc = end_utc + timedelta(days=1)

        attendance = AttendanceCheckin.objects.filter(
            guard=user,
            assignment=assignment,
            shift=shift,
            org_location=org_location,
            created_on__gte=search_start_utc,
            created_on__lt=search_end_utc
        ).first()

        if not attendance or not attendance.checkin_time:
            return Response({"message": "Cannot checkout before checkin"}, status=status.HTTP_400_BAD_REQUEST)

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
            timestamp__gte=search_start_utc,
            timestamp__lt=search_end_utc
        ).order_by("-timestamp").first()

        if latest_checkout:
            attendance.checkout_time = latest_checkout.timestamp
        attendance.save()

        return Response(AttendanceCheckinSerializer(attendance).data, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"])
    def list_default_shifts(self, request):
        """
        List default shifts for guard's location.
        Only returns shifts if guard has no shift for today.
        Query params: guard_id
        """
        guard_id = request.query_params.get("guard_id")
        
        if not guard_id:
            return Response(
                {"error": "guard_id is required"}, 
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            guard = User.objects.get(id=guard_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Guard not found"}, 
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if guard has shift for today
        user_tz = get_user_timezone_from_request(request)
        today = get_user_today(user_tz)
        yesterday = today - timedelta(days=1)
        user_now = get_user_now(user_tz)
        
        # Check assignments that overlap with today OR yesterday (for overnight shifts)
        assignments = Assignment.objects.filter(
            guard_id=guard_id,
            start_date__lte=today,
            end_date__gte=yesterday
        ).select_related('shift', 'location')
        
        # Check if guard has active shift today
        has_shift_today = False
        for assignment in assignments:
            shift = assignment.shift
            if not shift:
                continue
                
            is_overnight = shift.end_time <= shift.start_time
            
            # Check if this is yesterday's overnight shift still active today
            if assignment.start_date <= yesterday and is_overnight:
                shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz)
                if user_now < shift_end_dt:
                    has_shift_today = True
                    break
            
            # Check if this is today's shift (regular or overnight starting today)
            if assignment.start_date <= today:
                shift_start_dt = combine_date_time_in_user_tz(today, shift.start_time, user_tz)
                if is_overnight:
                    if user_now.time() >= shift.start_time or user_now.time() < shift.end_time:
                        has_shift_today = True
                        break
                else:
                    shift_end_dt = combine_date_time_in_user_tz(today, shift.end_time, user_tz)
                    if shift_start_dt <= user_now <= shift_end_dt:
                        has_shift_today = True
                        break
        
        if has_shift_today:
            return Response(
                {"message": "Guard already has a shift for today", "has_shift": True},
                status=status.HTTP_200_OK
            )
        
        # Get guard's location
        if not guard.location:
            return Response(
                {"error": "Guard does not have an assigned location"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get default shifts for guard's location
        default_shifts = Shift.objects.filter(
            is_default=True,
            is_deleted=False,
            location=guard.location
        ).select_related('checkpoint_template', 'location')
        
        # Serialize shifts
        shifts_data = []
        for shift in default_shifts:
            shifts_data.append({
                "id": str(shift.id),
                "name": shift.name,
                "start_time": shift.start_time.strftime("%H:%M:%S"),
                "end_time": shift.end_time.strftime("%H:%M:%S"),
                "location_id": str(shift.location.id) if shift.location else None,
                "location_name": shift.location.name if shift.location else None,
                "checkpoint_template_id": str(shift.checkpoint_template.id) if shift.checkpoint_template else None,
                "checkpoint_template_name": shift.checkpoint_template.template_name if shift.checkpoint_template else None,
                "is_overnight": shift.end_time <= shift.start_time
            })
        
        return Response({
            "has_shift": False,
            "default_shifts": shifts_data,
            "location_id": str(guard.location.id),
            "location_name": guard.location.name
        }, status=status.HTTP_200_OK)

    @action(detail=False, methods=["post"])
    def create_assignment(self, request):
        """
        Create assignment for guard with specified shift.
        Only creates if guard has no shift for today.
        Body: guard_id, shift_id
        """
        guard_id = request.data.get("guard_id")
        shift_id = request.data.get("shift_id")
        
        if not guard_id or not shift_id:
            return Response(
                {"error": "guard_id and shift_id are required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            guard = User.objects.get(id=guard_id)
        except User.DoesNotExist:
            return Response(
                {"error": "Guard not found"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        try:
            shift = Shift.objects.get(id=shift_id, is_deleted=False)
        except Shift.DoesNotExist:
            return Response(
                {"error": "Shift not found or deleted"},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Check if guard has shift for today
        user_tz = get_user_timezone_from_request(request)
        today = get_user_today(user_tz)
        yesterday = today - timedelta(days=1)
        user_now = get_user_now(user_tz)
        
        # Check assignments that overlap with today OR yesterday (for overnight shifts)
        assignments = Assignment.objects.filter(
            guard_id=guard_id,
            start_date__lte=today,
            end_date__gte=yesterday
        ).select_related('shift', 'location')
        
        # Check if guard has active shift today
        has_shift_today = False
        for assignment in assignments:
            assignment_shift = assignment.shift
            if not assignment_shift:
                continue
                
            is_overnight = assignment_shift.end_time <= assignment_shift.start_time
            
            # Check if this is yesterday's overnight shift still active today
            if assignment.start_date <= yesterday and is_overnight:
                shift_end_dt = combine_date_time_in_user_tz(today, assignment_shift.end_time, user_tz)
                if user_now < shift_end_dt:
                    has_shift_today = True
                    break
            
            # Check if this is today's shift (regular or overnight starting today)
            if assignment.start_date <= today:
                shift_start_dt = combine_date_time_in_user_tz(today, assignment_shift.start_time, user_tz)
                if is_overnight:
                    if user_now.time() >= assignment_shift.start_time or user_now.time() < assignment_shift.end_time:
                        has_shift_today = True
                        break
                else:
                    shift_end_dt = combine_date_time_in_user_tz(today, assignment_shift.end_time, user_tz)
                    if shift_start_dt <= user_now <= shift_end_dt:
                        has_shift_today = True
                        break
        
        if has_shift_today:
            return Response(
                {"error": "Guard already has a shift for today", "has_shift": True},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get checkpoints from shift's checkpoint_template
        checkpoints = []
        if shift.checkpoint_template:
            template = shift.checkpoint_template
            if template.checkpoints:
                checkpoints = template.checkpoints  # Format: [{"checkpoint_id": "uuid", "time": "HH:MM"}]
        
        # Determine assignment date range
        # Even for overnight shifts, the assignment is conceptually bound to a single logical day.
        # The get_today_assignment_v2 API will still fetch it correctly tomorrow morning
        # because it specifically queries `end_date__gte=yesterday`.
        start_date = today
        end_date = today
        
        # Create assignment
        assignment = Assignment.objects.create(
            guard=guard,
            location=shift.location,
            shift=shift,
            start_date=start_date,
            end_date=end_date,
            checkpoints=checkpoints
        )
        
        return Response({
            "message": "Assignment created successfully",
            "assignment_id": str(assignment.id),
            "guard_id": str(guard.id),
            "guard_name": guard.name if hasattr(guard, 'name') else guard.username,
            "shift_id": str(shift.id),
            "shift_name": shift.name,
            "location_id": str(shift.location.id) if shift.location else None,
            "location_name": shift.location.name if shift.location else None,
            "start_date": str(start_date),
            "end_date": str(end_date),
            "checkpoints_count": len(checkpoints)
        }, status=status.HTTP_201_CREATED)


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

def _get_checkin_report_data(filter_type='today', start_date_str=None, end_date_str=None, user_id=None, location_id=None, shift_id=None, request=None):
    """
    Internal helper function that contains ALL business logic for check-in reports.
    This ensures consistency across JSON API, Excel exports, and Celery tasks.
    
    Args:
        filter_type: 'today', 'this_week', 'this_month', or 'custom'
        start_date_str: For custom filter (YYYY-MM-DD string)
        end_date_str: For custom filter (YYYY-MM-DD string)
        user_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
        shift_id: Optional UUID string to filter by shift
        request: Optional request object for timezone detection (for API calls)
    
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
    
    # Get user timezone from request (authenticated user making the request)
    # For superadmin viewing location-specific reports, uses location admin's timezone
    # This ensures filters use the correct timezone for the location being viewed
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        today = get_user_today(user_tz)
        # Log timezone being used for debugging
        logger.info(f"[CHECKIN_REPORT] Using timezone: {user_tz.zone} for location_id: {location_id}")
    else:
        # For Celery tasks or non-request contexts, use default timezone
        # Note: In Celery tasks, we should ideally get timezone from the user_id if provided
        user_tz = pytz.timezone('Asia/Kolkata')
        today = get_user_today(user_tz)  # Use get_user_today instead of timezone.now().date()
    
    # Parse filter and determine date range in user timezone
    if filter_type == 'today':
        start_dt_user = datetime.combine(today, datetime.min.time())
        end_dt_user = datetime.combine(today, datetime.max.time())
    elif filter_type == 'this_week':
        start_week = today - timedelta(days=today.weekday())
        end_week = start_week + timedelta(days=6)
        start_dt_user = datetime.combine(start_week, datetime.min.time())
        end_dt_user = datetime.combine(end_week, datetime.max.time())
    elif filter_type == 'this_month':
        start_dt_user = datetime(today.year, today.month, 1)
        next_month = start_dt_user.replace(day=28) + timedelta(days=4)
        end_dt_user = datetime(next_month.year, next_month.month, 1) - timedelta(seconds=1)
    elif filter_type == 'custom' and start_date_str and end_date_str:
        try:
            start_date_obj = datetime.strptime(start_date_str, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date_str, "%Y-%m-%d").date()
            start_dt_user = datetime.combine(start_date_obj, datetime.min.time())
            end_dt_user = datetime.combine(end_date_obj, datetime.max.time())
        except ValueError as e:
            raise ValueError(f"Invalid date format. Use YYYY-MM-DD. Error: {e}")
    else:
        raise ValueError("Invalid filter or missing dates")
    
    # Get assignments that overlap with the date range
    # Extend 1 day before filter start to capture overnight shifts from previous day
    # whose after-midnight checkpoints fall within the filter range
    query_start_date = start_dt_user.date() - timedelta(days=1)
    query_end_date = end_dt_user.date()
    assignments = Assignment.objects.filter(
        start_date__lte=query_end_date,
        end_date__gte=query_start_date
    ).select_related('guard', 'location', 'shift')
    
    if user_id:
        assignments = assignments.filter(guard_id=user_id)
    if location_id:
        assignments = assignments.filter(location_id=location_id)
    if shift_id:
        assignments = assignments.filter(shift_id=shift_id)
    
    report = []
    
    # Generate date range for the filter period (in user timezone)
    date_range = []
    current_date = start_dt_user.date()
    end_date = end_dt_user.date()
    
    # For "this_week", "this_month", and "custom" filters, limit to only include dates up to today
    # This prevents showing checkpoints from future shifts
    if filter_type in ['this_week', 'this_month', 'custom']:
        end_date = min(end_date, today)
    
    while current_date <= end_date:
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
            
            # Check if shift is overnight
            is_overnight = shift.end_time <= shift.start_time
            
            # For each date in the range where the assignment is active
            # For ALL filters (not just today), check the day before filter start
            # to catch overnight shift checkpoints that spill into the filter range
            # Example: Feb 2 filter → also check Feb 1's overnight shift for 3 AM Feb 2 checkpoints
            dates_to_check = list(date_range)
            if is_overnight:
                prev_day = start_dt_user.date() - timedelta(days=1)
                if assignment.start_date <= prev_day <= assignment.end_date:
                    if prev_day not in dates_to_check:
                        dates_to_check.insert(0, prev_day)
            
            for check_date in dates_to_check:
                # Skip future dates - don't process shifts that haven't started yet
                if check_date > today:
                    continue
                
                # Only process if assignment is active on this date
                if not (assignment.start_date <= check_date <= assignment.end_date):
                    continue
                
                # Determine which calendar date this checkpoint occurs on
                # For overnight shifts: checkpoints after midnight occur on the NEXT calendar day
                # For normal shifts: checkpoints occur on the same day
                if is_overnight:
                    # For overnight shifts, check if checkpoint time is before or after midnight
                    if expected_time_obj >= shift.start_time:
                        # Checkpoint is before midnight - occurs on the same calendar day as shift start
                        checkpoint_date = check_date
                    else:
                        # Checkpoint is after midnight - occurs on the NEXT calendar day
                        checkpoint_date = check_date + timedelta(days=1)
                else:
                    # Normal shift - checkpoint is on the same day
                    checkpoint_date = check_date
                
                # UNIFIED FILTER: Include checkpoint only if it actually occurs within the filter date range
                # This works consistently for ALL filter types (today, custom, this_week, this_month)
                if not (start_dt_user.date() <= checkpoint_date <= end_date):
                    continue
                
                # Calculate expected time for this specific date in user timezone
                expected_datetime_user = combine_date_time_in_user_tz(checkpoint_date, expected_time_obj, user_tz)
                
                # Define ±30 min search window around expected time (in UTC) to find matching CheckIn
                search_start_utc = (expected_datetime_user - timedelta(minutes=30)).astimezone(pytz.UTC)
                search_end_utc = (expected_datetime_user + timedelta(minutes=30)).astimezone(pytz.UTC)

                checkin = CheckIn.objects.filter(
                    guard=guard,
                    shift=shift,
                    checkpoint_id=checkpoint_id,
                    timestamp__gte=search_start_utc,
                    timestamp__lt=search_end_utc
                ).order_by('timestamp').first()
                
                actual_time = None
                delay = None
                
                # Determine default status based on whether scheduled time has passed
                # Convert expected time to user timezone for comparison
                if expected_datetime_user:
                    expected_time_user = expected_datetime_user.astimezone(user_tz)
                    user_now = get_user_now(user_tz)
                    # Check if scheduled time + 15 minutes grace period has passed
                    if user_now > expected_time_user + timedelta(minutes=15):
                        status = "Missed"  # Time has passed, no check-in = Missed
                    else:
                        status = "Pending"  # Time hasn't passed yet = Pending
                else:
                    status = "Missed"  # Fallback if no expected time
                
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
                        
                        # Log UTC time before conversion
                        logger.info(f"[CHECKIN_REPORT] UTC time (before conversion): {actual_time}, timezone: {actual_time.tzinfo if actual_time else None}")
                        
                        # Convert both to user timezone for delay calculation
                        actual_time_user = to_user_timezone(actual_time, user_tz)
                        expected_time_user = expected_datetime_user.astimezone(user_tz)
                        
                        # Log converted time
                        logger.info(f"[CHECKIN_REPORT] Converted time (after conversion): {actual_time_user}, target timezone: {user_tz.zone}")
                        logger.info(f"[CHECKIN_REPORT] Expected time: {expected_time_user}, timezone: {expected_time_user.tzinfo if expected_time_user else None}")
                        
                        # Calculate delay in minutes (in user timezone)
                        delay = int((actual_time_user - expected_time_user).total_seconds() / 60)
                        
                        # Determine status based on delay
                        if delay <= 15:
                            status = "On Time"
                        elif 15 < delay <= 30:
                            status = "Delayed"
                        else:
                            status = "Missed"
                
                # Add to report (convert times to user timezone for display)
                expected_time_display = expected_datetime_user.astimezone(user_tz) if expected_datetime_user else None
                if actual_time:
                    # Log before final conversion for display
                    logger.info(f"[CHECKIN_REPORT] Display conversion - UTC: {actual_time}, Converting to: {user_tz.zone}")
                    actual_time_display = to_user_timezone(actual_time, user_tz)
                    logger.info(f"[CHECKIN_REPORT] Display conversion - Result: {actual_time_display}, timezone: {actual_time_display.tzinfo if actual_time_display else None}")
                else:
                    actual_time_display = None
                
                # Use checkpoint_date as the report date
                # This is the actual calendar date the checkpoint occurs on
                # For overnight shifts: after-midnight checkpoints show on the next day
                # For normal shifts: same as check_date
                report_date = checkpoint_date
                
                report.append({
                    'date': report_date.strftime('%Y-%m-%d'),
                    'guard_id': str(guard.id),
                    'guard_name': guard.name,
                    'location_id': str(location.id) if location else None,
                    'location_name': location.name if location else "",
                    'shift_id': str(shift.id) if shift else None,
                    'shift_name': shift.name if shift else "",
                    'checkpoint_id': str(checkpoint_id),
                    'checkpoint_name': checkpoint_name,
                    'expected_time': expected_time_display,  # In user timezone
                    'actual_checkin_time': actual_time_display,  # In user timezone
                    'status': status,
                    'delay_minutes': delay
                })
    
    # Sort report by date, then by guard_name, then by checkpoint_name, then by expected_time
    # This ensures all checkpoints for a date are grouped together
    report.sort(key=lambda x: (
        x['date'],
        x['expected_time'] if x['expected_time'] else '',
        x['guard_name'],
        x['checkpoint_name'],
    ))
    
    logger.info(f"[CHECKIN_REPORT] Generated {len(report)} records for filter: {filter_type}")
    return report


# def _get_checkin_report_data(filter_type='today', start_date_str=None, end_date_str=None, user_id=None, location_id=None, shift_id=None, request=None):
#     from django.utils.timezone import now as django_now
    
#     # 1. IMPROVED TIMEZONE HANDLING
#     # Prioritize: Request Location -> Admin's Location -> Request User Profile -> Default
#     user_tz = None
#     if request:
#         # This utility already handles Superadmin vs Admin logic if location_id is passed
#         user_tz = get_user_timezone_from_request(request, location_id=location_id)
    
#     if not user_tz:
#         user_tz = pytz.timezone('Asia/Kolkata') # Final fallback
        
#     today = get_user_today(user_tz)
#     logger.info(f"[CHECKIN_REPORT] Generating report using timezone: {user_tz.zone}")
    
#     # 2. PARSE DATE RANGE (FIXED: Added missing logic)
#     start_date = today
#     end_date = today

#     if filter_type == 'custom' and start_date_str and end_date_str:
#         try:
#             start_date = datetime.strptime(start_date_str, "%Y-%m-%d").date()
#             end_date = datetime.strptime(end_date_str, "%Y-%m-%d").date()
#         except (ValueError, TypeError):
#             pass
#     elif filter_type == 'week':
#         start_date = today - timedelta(days=today.weekday())
#         end_date = today
#     elif filter_type == 'month':
#         start_date = today.replace(day=1)
#         end_date = today

#     # Convert local dates to UTC range for database query
#     start_utc, end_utc = convert_date_range_to_utc(start_date, end_date, user_tz)
    
#     # 3. COLLECT ACTUAL SCANS
#     checkins = CheckIn.objects.filter(
#         timestamp__gte=start_utc,
#         timestamp__lte=end_utc
#     ).select_related('guard', 'checkpoint', 'shift', 'shift__location')

#     if user_id:
#         checkins = checkins.filter(guard_id=user_id)
#     if location_id:
#         checkins = checkins.filter(shift__location_id=location_id)
#     if shift_id:
#         checkins = checkins.filter(shift_id=shift_id)

#     report = []

#     print(f"DEBUG: Found {checkins.count()} checkins between {start_utc} and {end_utc}")
#     for checkin in checkins:
#         print(f"DEBUG: CheckIn - Guard: {checkin.guard.name}, Time: {checkin.timestamp}, Checkpoint: {checkin.checkpoint.label}")

#         expected_time_display = None
#         status = "On Time"
#         delay = 0

#         # FIND CLOSEST MATCH FOR REPEATING CHECKPOINTS
#         assignment = Assignment.objects.filter(
#             guard=checkin.guard,
#             shift=checkin.shift,
#             start_date__lte=checkin.timestamp.date(),
#             end_date__gte=checkin.timestamp.date()
#         ).first()

#         if assignment:
#             best_match_dt = None
#             min_seconds_diff = float('inf')

#             # Loop through ALL assigned times for this checkpoint
#             for cp in assignment.checkpoints:
#                 if str(cp.get('checkpoint_id')) == str(checkin.checkpoint.id):
#                     try:
#                         slot_time = datetime.strptime(cp.get('time'), "%H:%M").time()
#                         # Create a full datetime for this slot in user local time
#                         slot_dt = datetime.combine(checkin.timestamp.astimezone(user_tz).date(), slot_time)
#                         slot_dt = user_tz.localize(slot_dt)
                        
#                         # Compare scan time vs this specific slot
#                         diff = abs((checkin.timestamp - slot_dt.astimezone(pytz.UTC)).total_seconds())
                        
#                         if diff < min_seconds_diff:
#                             min_seconds_diff = diff
#                             best_match_dt = slot_dt
#                     except:
#                         continue

#             if best_match_dt:
#                 expected_time_display = best_match_dt
#                 delay_seconds = (checkin.timestamp - best_match_dt.astimezone(pytz.UTC)).total_seconds()
#                 delay = int(delay_seconds / 60)
                
#                 # Use standard 15-minute window for "On Time"
#                 if abs(delay) <= 15:
#                     status = "On Time"
#                 elif delay > 0:
#                     status = "Delayed"
#                 else:
#                     status = "Early"

#         report.append({
#             'date': checkin.timestamp.astimezone(user_tz).date().isoformat(),
#             'guard_id': str(checkin.guard.id),
#             'guard_name': checkin.guard.name,
#             'location_id': str(checkin.shift.location.id) if checkin.shift and checkin.shift.location else None,
#             'location_name': checkin.shift.location.name if checkin.shift and checkin.shift.location else "",
#             'shift_id': str(checkin.shift.id) if checkin.shift else None,
#             'shift_name': checkin.shift.name if checkin.shift else "",
#             'checkpoint_id': str(checkin.checkpoint.id),
#             'checkpoint_name': checkin.checkpoint.label,
#             'expected_time': expected_time_display,
#             'actual_checkin_time': checkin.timestamp.astimezone(user_tz),
#             'status': status,
#             'delay_minutes': delay
#         })

#     # SORT BY EXPECTED TIME
#     report.sort(key=lambda x: (x['expected_time'].isoformat() if x['expected_time'] else '9999', x['actual_checkin_time']))

#     return report

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
        shift_id = request.query_params.get('shift_id')
        
        # Log request details (both logger and print for visibility)
        
        try:
            # Get report data using shared core function
            report_data = _get_checkin_report_data(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id,
                shift_id=shift_id,
                request=request  # Pass request for timezone detection
            )
            
            # Format datetime fields for JSON response (already in user timezone from _get_checkin_report_data)
            for idx, item in enumerate(report_data):
                if item['expected_time']:
                    # Log before formatting
                    logger.info(f"[CHECKIN_REPORT] Item {idx} - Expected time before format: {item['expected_time']}, timezone: {item['expected_time'].tzinfo if hasattr(item['expected_time'], 'tzinfo') else 'N/A'}")
                    # Convert to ISO format string (timezone-aware)
                    if hasattr(item['expected_time'], 'isoformat'):
                        item['expected_time'] = item['expected_time'].isoformat()
                    else:
                        item['expected_time'] = item['expected_time'].strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(f"[CHECKIN_REPORT] Item {idx} - Expected time after format: {item['expected_time']}")
                if item['actual_checkin_time']:
                    # Log before formatting
                    logger.info(f"[CHECKIN_REPORT] Item {idx} - Actual time before format: {item['actual_checkin_time']}, timezone: {item['actual_checkin_time'].tzinfo if hasattr(item['actual_checkin_time'], 'tzinfo') else 'N/A'}")
                    # Convert to ISO format string (timezone-aware)
                    if hasattr(item['actual_checkin_time'], 'isoformat'):
                        item['actual_checkin_time'] = item['actual_checkin_time'].isoformat()
                    else:
                        item['actual_checkin_time'] = item['actual_checkin_time'].strftime('%Y-%m-%d %H:%M:%S')
                    logger.info(f"[CHECKIN_REPORT] Item {idx} - Actual time after format: {item['actual_checkin_time']}")
            
            # Serialize and return
            serializer = CheckInReportSerializer(report_data, many=True)
            return Response(serializer.data, status=status.HTTP_200_OK)
            
        except ValueError as e:
            logger.error(f"[CHECKIN_REPORT_API] ValueError: {str(e)}")
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.error(f"[CHECKIN_REPORT_API] Exception: {str(e)}", exc_info=True)
            return Response(
                {"error": f"An error occurred while generating the report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class AttendanceCheckinListView(generics.ListAPIView):
    serializer_class = AttendanceCheckinDashboardSerializer

    def get_queryset(self):
        queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

        # Get location_id for timezone (for superadmin viewing location-specific reports)
        location_id = self.request.query_params.get("location")
        
        # Get user timezone - for superadmin viewing location-specific reports, uses location admin's timezone
        user_tz = get_user_timezone_from_request(self.request, location_id=location_id)
        date_filter = self.request.query_params.get("date_filter", "today")

        user_today = get_user_today(user_tz)

        if date_filter == "today":
            # Convert today to UTC range for query
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        elif date_filter == "week":
            start_week = user_today - timedelta(days=user_today.weekday())
            # Limit to user_today to prevent showing future dates
            end_week = min(start_week + timedelta(days=6), user_today)
            start_utc, end_utc = convert_date_range_to_utc(start_week, end_week, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        elif date_filter == "month":
            # Get month boundaries in user timezone
            start_of_month = user_today.replace(day=1)
            # Limit to user_today to prevent showing future dates
            end_of_month = min(
                user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
                else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
                user_today
            )
            start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
        elif date_filter == "custom":
            start_date = self.request.query_params.get("start_date")
            end_date = self.request.query_params.get("end_date")
            if start_date and end_date:
                try:
                    start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
                    end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
                    # Limit to user_today to prevent showing future dates
                    end_date_obj = min(end_date_obj, user_today)
                    # Convert to UTC range for query
                    start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
                    queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
                except ValueError:
                    pass  # Invalid date format, skip custom filter

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
            # Use UTC date range for today
            start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
            queryset = queryset.filter(
                checkin_time__gte=start_utc,
                checkin_time__lt=end_utc + timedelta(days=1),
                checkout_time__isnull=True
            )

        return queryset


def generate_checkin_excel_report_internal(filter_type='today', start_date=None, end_date=None, user_id=None, location_id=None, request=None):
    """
    Internal helper function to generate check-in Excel report.
    Used by both the API endpoint and Celery tasks.
    
    Args:
        filter_type: 'today', 'this_week', 'this_month', or 'custom'
        start_date: For custom filter (YYYY-MM-DD string)
        end_date: For custom filter (YYYY-MM-DD string)
        user_id: Optional UUID string to filter by guard
        location_id: Optional UUID string to filter by location
        request: Optional request object for timezone (None for Celery tasks)
    
    Returns:
        dict with 'file_path', 'filename', and 'row_count'
    """
    # Get report data using shared core function (pass request for timezone)
    report_data = _get_checkin_report_data(
        filter_type=filter_type,
        start_date_str=start_date,
        end_date_str=end_date,
        user_id=user_id,
        location_id=location_id,
        request=request
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
        shift_id = request.query_params.get('shift_id')
        
        
        try:
            # Get report data using shared core function (pass request for timezone)
            report_data = _get_checkin_report_data(
                filter_type=filter_type,
                start_date_str=start_date,
                end_date_str=end_date,
                user_id=user_id,
                location_id=location_id,
                shift_id=shift_id,
                request=request
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

            # Return Excel file as HTTP response (not JSON)
            excel_content = buffer.getvalue()
            
            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
            # Generate a meaningful filename based on filter
            if filter_type == 'custom' and start_date and end_date:
                filename = f"checkin_report_{start_date}_{end_date}.xlsx"
            else:
                filename = f"checkin_report_{timezone.now().strftime('%Y%m%d')}.xlsx"
            
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response(
                {"error": f"An error occurred while generating the Excel report: {str(e)}"}, 
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


def generate_attendance_excel_report_internal(date_filter='today', start_date=None, end_date=None, guard_id=None, location_id=None, shift_id=None, status_filter=None, defaulters=False, request=None):
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
        request: Optional request object for timezone detection (required for proper timezone handling)
    
    Returns: dict with 'file_path' and 'filename'
    """
    queryset = AttendanceCheckin.objects.select_related("guard", "shift", "org_location")

    # Get user timezone from request (authenticated user making the request)
    # For superadmin viewing location-specific reports, uses location admin's timezone
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        # Fallback: use default timezone if no request (should not happen in normal API calls)
        user_tz = pytz.timezone('Asia/Kolkata')
        user_today = get_user_today(user_tz)

    if date_filter == "today":
        # Convert today to UTC range for query
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
        queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
    elif date_filter == "week":
        start_week = user_today - timedelta(days=user_today.weekday())
        # Limit to user_today to prevent showing future dates
        end_week = min(start_week + timedelta(days=6), user_today)
        start_utc, end_utc = convert_date_range_to_utc(start_week, end_week, user_tz)
        queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
    elif date_filter == "month":
        # Get month boundaries in user timezone
        start_of_month = user_today.replace(day=1)
        # Limit to user_today to prevent showing future dates
        end_of_month = min(
            user_today.replace(month=user_today.month + 1, day=1) - timedelta(days=1) if user_today.month != 12
            else user_today.replace(year=user_today.year + 1, month=1, day=1) - timedelta(days=1),
            user_today
        )
        start_utc, end_utc = convert_date_range_to_utc(start_of_month, end_of_month, user_tz)
        queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
    elif date_filter == "custom" and start_date and end_date:
        try:
            start_date_obj = datetime.strptime(start_date, "%Y-%m-%d").date()
            end_date_obj = datetime.strptime(end_date, "%Y-%m-%d").date()
            # Limit to user_today to prevent showing future dates
            end_date_obj = min(end_date_obj, user_today)
            # Convert to UTC range for query
            start_utc, end_utc = convert_date_range_to_utc(start_date_obj, end_date_obj, user_tz)
            queryset = queryset.filter(checkin_time__gte=start_utc, checkin_time__lt=end_utc + timedelta(days=1))
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
        # Use UTC date range for today
        start_utc, end_utc = convert_date_range_to_utc(user_today, user_today, user_tz)
        queryset = queryset.filter(
            checkin_time__gte=start_utc,
            checkin_time__lt=end_utc + timedelta(days=1),
            checkout_time__isnull=True
        )

    # Create Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Attendance Checkins"

    # Header with reordered columns and added Date + Duration
    ws.append(["Shift Date", "Name", "Shift", "Location", "Checkin Time", "Checkout Time", "Duration (HH:MM)", "Status", "Remarks"])

    row_count = 0  # Track number of data rows
    for obj in queryset:
        # Convert UTC times to user timezone for display in Excel
        checkin = to_user_timezone(obj.checkin_time, user_tz) if obj.checkin_time else None
        checkout = to_user_timezone(obj.checkout_time, user_tz) if obj.checkout_time else None
        shift = obj.shift
        attendance_status = ""
        other_statuses = []
        duration = ""

        if shift:
            # Check if shift is overnight
            is_overnight = shift.end_time <= shift.start_time
            
            if checkin:
                # checkin is already in user timezone (converted above)
                checkin_date = checkin.date()
                
                if is_overnight:
                    # For overnight shifts, end time is on the next day
                    # Combine date and time in user timezone
                    shift_start = user_tz.localize(datetime.combine(checkin_date, shift.start_time))
                    # If checkout is on the same date as checkin, shift end is next day
                    # If checkout is on next day, shift end is on checkout date
                    if checkout:
                        checkout_date = checkout.date()
                        if checkout_date > checkin_date:
                            # Shift spans two days, end is on checkout date
                            shift_end = user_tz.localize(datetime.combine(checkout_date, shift.end_time))
                        else:
                            # Both on same date (shouldn't happen for overnight, but handle it)
                            shift_end = user_tz.localize(datetime.combine(checkin_date + timedelta(days=1), shift.end_time))
                    else:
                        # No checkout yet, assume end is next day
                        shift_end = user_tz.localize(datetime.combine(checkin_date + timedelta(days=1), shift.end_time))
                else:
                    # Normal shift - both times on same day
                    shift_start = user_tz.localize(datetime.combine(checkin_date, shift.start_time))
                    shift_end = user_tz.localize(datetime.combine(checkin_date, shift.end_time))
            else:
                shift_start = None
                shift_end = None

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
            if shift_end:
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
        
        log_msg = f"[ATTENDANCE_EXPORT_API] GET request - Filter: {date_filter}, Start: {start_date}, End: {end_date}, Guard ID: {guard_id}, Location ID: {location_id}, Shift ID: {shift_id}, Status: {status_filter}, Defaulters: {defaulters}"
        
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
                defaulters=defaulters,
                request=request  # Pass request for timezone detection
            )
            
            file_path = result['file_path']
            
            # Return Excel file as HTTP response (not JSON)
            with open(file_path, 'rb') as f:
                excel_content = f.read()
            
            response = HttpResponse(
                excel_content,
                content_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
            
            # Generate a meaningful filename based on date filter
            if date_filter == 'custom' and start_date and end_date:
                filename = f"attendance_report_{start_date}_{end_date}.xlsx"
            else:
                filename = f"attendance_report_{timezone.now().strftime('%Y%m%d')}.xlsx"
            
            response["Content-Disposition"] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            error_msg = f"[ATTENDANCE_EXPORT_API] ValueError: {str(e)}"
            logger.error(error_msg)
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            error_msg = f"[ATTENDANCE_EXPORT_API] Exception: {str(e)}"
            logger.error(error_msg, exc_info=True)
            return Response({"error": f"Failed to generate report: {str(e)}"}, status=500)


def _get_monthly_attendance_summary_data(month=None, start_date_str=None, end_date_str=None, location_id=None, user_id=None, request=None):
    """
    Internal helper function to generate monthly attendance summary data.
    This contains the business logic shared by both JSON and Excel endpoints.
    
    Args:
        month: Optional string in YYYY-MM format
        start_date_str: Optional string in YYYY-MM-DD format (for custom range)
        end_date_str: Optional string in YYYY-MM-DD format (for custom range)
        location_id: Optional UUID string to filter by location
        user_id: Optional UUID string to filter by guard
        request: Optional request object for timezone detection (to determine "today" for future date check)
    
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

    # Get user timezone and today for future date check
    # IMPORTANT: Get user_today BEFORE generating date_range to ensure correct future date detection
    # For superadmin viewing location-specific reports, uses location admin's timezone
    if request:
        user_tz = get_user_timezone_from_request(request, location_id=location_id)
        user_today = get_user_today(user_tz)
    else:
        # Fallback: use default timezone if no request (for Celery tasks)
        user_tz = pytz.timezone('Asia/Kolkata')
        user_today = get_user_today(user_tz)

    # Generate date range
    # For month parameter, always include full month (even future dates will show as "-")
    # For custom range, limit to user_today if end_date is in future
    if month:
        # Keep full month range - future dates will be handled in the loop to show "-"
        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
    else:
        # For custom range, limit to user_today if end_date is in future
        date_range = [start_date + timedelta(days=i) for i in range((end_date - start_date).days + 1)]
        if end_date > user_today:
            date_range = [d for d in date_range if d <= user_today]
            end_date = user_today

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
            # Check if date is in the future - if so, show "-" instead of "A"
            if date > user_today:
                row[date.strftime("%d-%b")] = "-"
                continue

            active_assignments = [
                a for a in data["assignments"]
                if a.start_date <= date <= a.end_date
            ]

            if not active_assignments:
                row[date.strftime("%d-%b")] = "-"
                continue

            # Convert date to UTC range for query using user timezone
            start_utc, end_utc = convert_date_range_to_utc(date, date, user_tz)
            has_checkin = AttendanceCheckin.objects.filter(
                guard=guard,
                assignment__in=active_assignments,
                checkin_time__gte=start_utc,
                checkin_time__lt=end_utc + timedelta(days=1)
            ).exists()

            row[date.strftime("%d-%b")] = "P" if has_checkin else "A"

        summary_data.append(row)

    return {
        'summary_data': summary_data,
        'date_range': date_range,
        'start_date': start_date,
        'end_date': end_date
    }


def generate_monthly_attendance_summary_excel_internal(month=None, start_date_str=None, end_date_str=None, location_id=None, user_id=None, request=None):
    """
    Internal helper function to generate monthly attendance summary Excel report.
    This contains the business logic that both the API endpoint and Celery tasks can use.
    
    Args:
        month: Optional string in YYYY-MM format
        start_date_str: Optional string in YYYY-MM-DD format (for custom range)
        end_date_str: Optional string in YYYY-MM-DD format (for custom range)
        location_id: Optional UUID string to filter by location
        user_id: Optional UUID string to filter by guard
        request: Optional request object for timezone detection (to determine "today" for future date check)
    
    Returns: dict with 'file_path', 'filename', 'start_date', 'end_date', 'row_count'
    """
    # Get summary data using core helper (pass request for timezone detection)
    result = _get_monthly_attendance_summary_data(
        month=month,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        location_id=location_id,
        user_id=user_id,
        request=request  # Pass request for timezone detection
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
            # Use internal helper function (pass request for timezone detection)
            result = _get_monthly_attendance_summary_data(
                month=month,
                start_date_str=start_date,
                end_date_str=end_date,
                location_id=location_id,
                user_id=user_id,
                request=request  # Pass request for timezone detection
            )
            
            return Response(result['summary_data'])
            
        except ValueError as e:
            error_msg = f"[MONTHLY_ATTENDANCE_SUMMARY_API] ValueError: {str(e)}"
            logger.error(error_msg)
            return Response({"error": str(e)}, status=400)
        except Exception as e:
            error_msg = f"[MONTHLY_ATTENDANCE_SUMMARY_API] Exception: {str(e)}"
            logger.error(error_msg, exc_info=True)
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
                request=request,  # Pass request for timezone detection
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

