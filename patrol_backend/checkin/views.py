from rest_framework import viewsets, status, serializers
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils.timezone import now
from django.contrib.auth import get_user_model
from geopy.distance import geodesic
from datetime import timedelta
from datetime import datetime, time
import logging
from .models import CheckIn
from .serializers import CheckInSerializer
from scheduler.models import Assignment, Checkpoint, Shift, SiteSetting
from django.utils.timezone import make_aware
import uuid
from patrol_backend.utils.timezone_utils import (
    get_user_timezone_from_request,
    get_user_today,
    combine_date_time_in_user_tz,
    to_user_timezone
)

User = get_user_model()
logger = logging.getLogger(__name__)

# class CheckInViewSet(viewsets.ModelViewSet):
#     queryset = CheckIn.objects.all()
#     serializer_class = CheckInSerializer
#     permission_classes = [IsAuthenticated]

#     def create(self, request, *args, **kwargs):
#         try:
#             data = request.data
#             site_settings = SiteSetting.objects.all()
       
#             guard_id = data.get('guard')
#             shift_id = data.get('shift')
#             assigned_id = data.get('assign_id')
#             checkpoint_id = data.get('checkpoint')
#             timestamp = data.get('timestamp')
#             latitude = float(data.get('latitude'))
#             longitude = float(data.get('longitude'))
#             qr_data = data.get('data')
            
#             # Get user's timezone for logging
#             user_tz = get_user_timezone_from_request(request)
#             user_today = get_user_today(user_tz)
#             user_now = to_user_timezone(now(), user_tz)
            

#             # Rule 1: Validate guard exists
#             try:
#                 guard = User.objects.get(id=guard_id)
#             except User.DoesNotExist:
#                 return Response({"error": "Invalid guard ID"}, status=status.HTTP_400_BAD_REQUEST)
            
#             # Rule 2 & 3: Validate assignment with matching guard, shift, and checkpoint
#             assignment = Assignment.objects.filter(
#                 id=assigned_id,
#                 guard=guard,
#                 shift_id=shift_id,
#                 checkpoints__contains=[{'checkpoint_id': checkpoint_id}]
#             ).first()

      
#             if not assignment:
#                 return Response({"error": "No matching assignment found for guard, shift, and checkpoint"},
#                                 status=status.HTTP_403_FORBIDDEN)
       
#             # Rule 4: Validate lat/lon within 50 meters of checkpoint
#             checkpoint = Checkpoint.objects.filter(id=checkpoint_id).first()
#             if not checkpoint:
#                 return Response({"error": "Checkpoint not found"}, status=status.HTTP_400_BAD_REQUEST)
            
            
#             if qr_data != checkpoint.data:
#                 return Response(
#                     {"detail": "Check-in not allowed. QR code mismatch."},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )
                      
#             checkpoint_coords = (checkpoint.latitude, checkpoint.longitude)
#             user_coords = (latitude, longitude)
#             distance = geodesic(checkpoint_coords, user_coords).meters
       
       
#             settings = list(SiteSetting.objects.all().values())
            
#             if distance > int(settings[1]['value']):
#                 return Response({"error": f"Check-in location is too far from checkpoint (>{int(distance)}m)"},
#                                 status=status.HTTP_403_FORBIDDEN)

#             #Rule 5: Validate timestamp within allowed window

#             shift = Shift.objects.filter(id=shift_id).first()

#             def get_all_checkpoint_times(assignment, checkpoint_id):
#                 """Get all time slots for this checkpoint (in case it appears multiple times)"""
#                 times = []
#                 for checkpoint in assignment.checkpoints:
#                     if checkpoint.get("checkpoint_id") == checkpoint_id:
#                         times.append(checkpoint.get("time"))
#                 return times  # Returns list of all times for this checkpoint

#             if not shift:
#                 return Response({"error": "Shift not found"}, status=status.HTTP_400_BAD_REQUEST)

#             # Get user's timezone from request or user model
#             user_tz = get_user_timezone_from_request(request)
            
#             # Get all checkpoint times for this checkpoint (handles multiple occurrences)
#             checkpoint_times_str = get_all_checkpoint_times(assignment, checkpoint_id)
#             if not checkpoint_times_str:
#                 logger.warning(f"[SCAN_CHECKPOINT_API] Checkpoint time not found - Assignment ID: {assigned_id}, Checkpoint ID: {checkpoint_id}")
#                 return Response({"error": "Checkpoint time not found in assignment"}, status=status.HTTP_400_BAD_REQUEST)
            
#             # Get current time in UTC (for storage)
#             checkin_time_utc = now()  # This is already UTC when USE_TZ=True
            
#             # Convert checkin_time to user timezone for comparison
#             checkin_time_user = to_user_timezone(checkin_time_utc, user_tz)
            
#             # Get today's date in user's timezone
#             user_today = get_user_today(user_tz)
            
            
#             # Check if shift is overnight
#             is_overnight = shift.end_time <= shift.start_time
            
#             # Find the closest matching checkpoint time slot
#             best_match = None
#             min_time_diff = float('inf')
            
#             for checkpoint_time_str in checkpoint_times_str:
#                 checkpoint_time = datetime.strptime(checkpoint_time_str, "%H:%M").time()

#                 # Determine which calendar date this checkpoint occurs on
#                 # For overnight shifts: checkpoints after midnight occur on TODAY's calendar date
#                 # For normal shifts: checkpoints always occur on today's date
#                 if is_overnight:
#                     # For overnight shifts:
#                     # - If checkpoint_time >= shift.start_time: Checkpoint is before midnight (e.g., 8:00 PM)
#                     #   Occurs on today's calendar date
#                     # - If checkpoint_time < shift.start_time: Checkpoint is after midnight (e.g., 1:30 AM)
#                     #   Occurs on TODAY's calendar date (not tomorrow!)
#                     #   Example: Scan at 1:30 AM on Jan 10 → checkpoint_date = Jan 10 (not Jan 11)
#                     #   This checkpoint belongs to the shift that STARTED on Jan 9, but occurs on Jan 10
#                     checkpoint_date = user_today
#                 else:
#                     # Normal shift - checkpoint is on the same day
#                     checkpoint_date = user_today
                
#                 # Combine date and checkpoint time in user's timezone, then convert to UTC
#                 expected_checkpoint_dt_utc = combine_date_time_in_user_tz(
#                     checkpoint_date, 
#                     checkpoint_time, 
#                     user_tz
#                 )

#                 # Convert to user timezone for comparison
#                 expected_checkpoint_dt_user = to_user_timezone(expected_checkpoint_dt_utc, user_tz)

#                 # Calculate time difference in minutes
#                 time_diff = abs((checkin_time_user - expected_checkpoint_dt_user).total_seconds()) / 60
                
#                 # Keep track of the closest match
#                 if time_diff < min_time_diff:
#                     min_time_diff = time_diff
#                     best_match = {
#                         'time': checkpoint_time,
#                         'date': checkpoint_date,
#                         'expected_dt_utc': expected_checkpoint_dt_utc,
#                         'time_diff': time_diff
#                     }
            
#             if not best_match:
#                 return Response({"error": "Could not determine checkpoint time"}, status=status.HTTP_400_BAD_REQUEST)
            
#             # Check if scan is within allowed window
#             delayed = min_time_diff > int(settings[0]['value'])

#             serializer = self.get_serializer(data=data)
#             serializer.is_valid(raise_exception=True)

#             if delayed:
#                 return Response(
#                     {"error": "Check-in not allowed. Delayed."},
#                     status=status.HTTP_400_BAD_REQUEST
#                 )

#             # Save the check-in (timestamp will be stored in UTC)
#             # Pass request context to serializer for timezone conversion
#             serializer.context['request'] = request
#             instance = serializer.save()

#             # Get serialized data with timezone conversion
#             response_data = serializer.data
#             response_data['delayed'] = delayed
#             response_data['message'] = "Checkpoint Successfully Scanned"
#             response_data['success'] = True
#             response_data['distance_from_checkpoint_m'] = round(distance, 2)

#             logger.info(f"[SCAN_CHECKPOINT_API] Check-in successful - Guard: {guard_id}, Checkpoint: {checkpoint_id}, Delay: {min_time_diff:.1f}min")

#             return Response(
#                 response_data,
#                 status=status.HTTP_201_CREATED
#             )


#         except Exception as e:
#             logger.error(f"[SCAN_CHECKPOINT_API] Error: {str(e)}", exc_info=True)
#             return Response({"error": "An unexpected error occurred during check-in."},
#                             status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class CheckInViewSet(viewsets.ModelViewSet):
    queryset = CheckIn.objects.all()
    serializer_class = CheckInSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        logger.info("========== [SCAN_CHECKPOINT_API] START ==========")

        try:
            logger.info("[SCAN_CHECKPOINT_API] API HIT")
            logger.info(f"[SCAN_CHECKPOINT_API] Auth user: {request.user}")
            logger.info(f"[SCAN_CHECKPOINT_API] Raw request data: {request.data}")

            data = request.data
            site_settings = SiteSetting.objects.all()

            # -------- PARAM EXTRACTION --------
            guard_id = data.get('guard')
            shift_id = data.get('shift')
            assigned_id = data.get('assign_id')
            checkpoint_id = data.get('checkpoint')
            timestamp = data.get('timestamp')
            latitude = data.get('latitude')
            longitude = data.get('longitude')
            qr_data = data.get('data')

            logger.info(
                "[SCAN_CHECKPOINT_API] Params received → "
                f"guard_id={guard_id}, shift_id={shift_id}, "
                f"assign_id={assigned_id}, checkpoint_id={checkpoint_id}, "
                f"timestamp={timestamp}, latitude={latitude}, longitude={longitude}, qr_data={qr_data}"
            )

            latitude = float(latitude)
            longitude = float(longitude)

            # -------- TIMEZONE INFO --------
            user_tz = get_user_timezone_from_request(request)
            user_today = get_user_today(user_tz)
            user_now = to_user_timezone(now(), user_tz)

            logger.info(
                f"[SCAN_CHECKPOINT_API] Timezone info → "
                f"user_tz={user_tz}, user_today={user_today}, user_now={user_now}"
            )

            # -------- GUARD VALIDATION --------
            logger.info(f"[SCAN_CHECKPOINT_API] Fetching Guard id={guard_id}")
            try:
                guard = User.objects.get(id=guard_id)
                logger.info(f"[SCAN_CHECKPOINT_API] Guard FOUND → id={guard.id}")
            except User.DoesNotExist:
                logger.error(f"[SCAN_CHECKPOINT_API] Guard NOT FOUND → id={guard_id}")
                return Response({"error": "Invalid guard ID"}, status=status.HTTP_400_BAD_REQUEST)

            # -------- ASSIGNMENT VALIDATION --------
            logger.info(
                "[SCAN_CHECKPOINT_API] Checking Assignment → "
                f"assign_id={assigned_id}, guard_id={guard.id}, "
                f"shift_id={shift_id}, checkpoint_id={checkpoint_id}"
            )

            assignment = Assignment.objects.filter(
                id=assigned_id,
                guard=guard,
                shift_id=shift_id,
                checkpoints__contains=[{'checkpoint_id': checkpoint_id}]
            ).first()

            if assignment:
                logger.info(f"[SCAN_CHECKPOINT_API] Assignment FOUND → id={assignment.id}")
                logger.info(f"[SCAN_CHECKPOINT_API] Assignment checkpoints → {assignment.checkpoints}")
            else:
                logger.error("[SCAN_CHECKPOINT_API] Assignment NOT FOUND")
                return Response(
                    {"error": "No matching assignment found for guard, shift, and checkpoint"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # -------- CHECKPOINT VALIDATION --------
            logger.info(f"[SCAN_CHECKPOINT_API] Fetching Checkpoint id={checkpoint_id}")
            checkpoint = Checkpoint.objects.filter(id=checkpoint_id).first()

            if not checkpoint:
                logger.error(f"[SCAN_CHECKPOINT_API] Checkpoint NOT FOUND → id={checkpoint_id}")
                return Response({"error": "Checkpoint not found"}, status=status.HTTP_400_BAD_REQUEST)

            logger.info(
                "[SCAN_CHECKPOINT_API] Checkpoint FOUND → "
                f"id={checkpoint.id}, lat={checkpoint.latitude}, lng={checkpoint.longitude}, data={checkpoint.data}"
            )

            if qr_data != checkpoint.data:
                logger.error("[SCAN_CHECKPOINT_API] QR DATA MISMATCH")
                return Response(
                    {"detail": "Check-in not allowed. QR code mismatch."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # -------- DISTANCE CHECK --------
            checkpoint_coords = (checkpoint.latitude, checkpoint.longitude)
            user_coords = (latitude, longitude)
            distance = geodesic(checkpoint_coords, user_coords).meters

            # Fetch allowed distance from SiteSetting (with fallback)
            allowed_distance_str = SiteSetting.get_setting(
                key='distance', 
                location_id=guard.location_id, 
                default_value="150"
            )
            allowed_distance = int(allowed_distance_str)

            logger.info(
                "[SCAN_CHECKPOINT_API] Distance calculation → "
                f"user_coords={user_coords}, checkpoint_coords={checkpoint_coords}, "
                f"distance={distance:.2f}m, allowed={allowed_distance}m"
            )

            if distance > allowed_distance:
                logger.error("[SCAN_CHECKPOINT_API] Distance validation FAILED")
                return Response(
                    {"error": f"Check-in location is too far from checkpoint ({int(distance)}m)"},
                    status=status.HTTP_403_FORBIDDEN
                )

            # -------- SHIFT VALIDATION --------
            logger.info(
                f"[SCAN_CHECKPOINT_API] Fetching Shift → shift_id={shift_id} (type={type(shift_id)})"
            )

            shift = Shift.objects.filter(id=shift_id).first()

            if not shift:
                logger.error(f"[SCAN_CHECKPOINT_API] Shift NOT FOUND → shift_id={shift_id}")
                return Response({"error": "Shift not found"}, status=status.HTTP_400_BAD_REQUEST)

            logger.info(
                "[SCAN_CHECKPOINT_API] Shift FOUND → "
                f"id={shift.id}, start={shift.start_time}, end={shift.end_time}"
            )

            # -------- CHECKPOINT TIMES --------
            def get_all_checkpoint_times(assignment, checkpoint_id):
                times = []
                for cp in assignment.checkpoints:
                    if cp.get("checkpoint_id") == checkpoint_id:
                        times.append(cp.get("time"))
                return times

            checkpoint_times_str = get_all_checkpoint_times(assignment, checkpoint_id)

            logger.info(
                f"[SCAN_CHECKPOINT_API] Checkpoint times for checkpoint {checkpoint_id} → "
                f"{checkpoint_times_str}"
            )

            if not checkpoint_times_str:
                logger.error("[SCAN_CHECKPOINT_API] No checkpoint times found in assignment")
                return Response(
                    {"error": "Checkpoint time not found in assignment"},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # -------- TIME MATCH LOGIC --------
            checkin_time_utc = now()
            checkin_time_user = to_user_timezone(checkin_time_utc, user_tz)
            is_overnight = shift.end_time <= shift.start_time

            logger.info(
                "[SCAN_CHECKPOINT_API] Time evaluation → "
                f"checkin_utc={checkin_time_utc}, "
                f"checkin_user={checkin_time_user}, "
                f"is_overnight={is_overnight}"
            )

            best_match = None
            min_time_diff = float('inf')

            for checkpoint_time_str in checkpoint_times_str:
                checkpoint_time = datetime.strptime(checkpoint_time_str, "%H:%M").time()
                checkpoint_date = user_today

                expected_dt_utc = combine_date_time_in_user_tz(
                    checkpoint_date, checkpoint_time, user_tz
                )
                expected_dt_user = to_user_timezone(expected_dt_utc, user_tz)

                time_diff = abs(
                    (checkin_time_user - expected_dt_user).total_seconds()
                ) / 60

                logger.info(
                    "[SCAN_CHECKPOINT_API] Time compare → "
                    f"checkpoint_time={checkpoint_time}, "
                    f"expected_user={expected_dt_user}, "
                    f"diff_min={time_diff:.2f}"
                )

                if time_diff < min_time_diff:
                    min_time_diff = time_diff
                    best_match = checkpoint_time

            # Fetch allowed delay from SiteSetting (with fallback)
            allowed_delay_str = SiteSetting.get_setting(
                key='time', 
                location_id=guard.location_id, 
                default_value="15"
            )
            allowed_delay = int(allowed_delay_str)

            delayed = min_time_diff > allowed_delay

            logger.info(
                "[SCAN_CHECKPOINT_API] Delay decision → "
                f"min_diff={min_time_diff:.2f}, "
                f"allowed={allowed_delay}, delayed={delayed}"
            )

            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)

            if delayed:
                logger.error("[SCAN_CHECKPOINT_API] Check-in DELAYED")
                return Response(
                    {"error": "Check-in not allowed. Delayed."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            serializer.context['request'] = request
            instance = serializer.save()

            response_data = serializer.data
            response_data.update({
                "delayed": delayed,
                "message": "Checkpoint Successfully Scanned",
                "success": True,
                "distance_from_checkpoint_m": round(distance, 2)
            })

            logger.info(
                "[SCAN_CHECKPOINT_API] SUCCESS → "
                f"guard_id={guard_id}, checkpoint_id={checkpoint_id}"
            )
            logger.info("========== [SCAN_CHECKPOINT_API] END ==========")

            return Response(response_data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.exception("[SCAN_CHECKPOINT_API] UNEXPECTED ERROR")
            return Response(
                {"error": "An unexpected error occurred during check-in."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
