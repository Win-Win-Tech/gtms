from rest_framework import viewsets, status, serializers
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from django.utils.timezone import now
from django.contrib.auth import get_user_model
from geopy.distance import geodesic
from datetime import timedelta
from datetime import datetime, time
import logging
from .models import CheckIn, CheckInChecklistAnswer
from .serializers import CheckInSerializer
from scheduler.models import Assignment, Checkpoint, Shift, SiteSetting, ChecklistTemplate, ChecklistItem
from django.utils.timezone import make_aware
import pytz
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
                for cp in (assignment.checkpoints or []):
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

    def _validate_scan_request(self, request):
        """
        Run the same validation as create() (guard, assignment, checkpoint, QR, distance, time window).
        Returns (error_response, None) on validation failure, or (None, context_dict) on success.
        Context includes: guard, assignment, checkpoint, shift, best_match, best_match_str,
        matched_entry, min_time_diff, allowed_delay, delayed, distance, data, user_tz, etc.
        """
        data = request.data
        guard_id = data.get('guard')
        shift_id = data.get('shift')
        assigned_id = data.get('assign_id')
        checkpoint_id = data.get('checkpoint')
        latitude = data.get('latitude')
        longitude = data.get('longitude')
        qr_data = data.get('data')
        try:
            latitude = float(latitude)
            longitude = float(longitude)
        except (TypeError, ValueError):
            return Response({"error": "Invalid latitude or longitude"}, status=status.HTTP_400_BAD_REQUEST), None

        user_tz = get_user_timezone_from_request(request)
        user_today = get_user_today(user_tz)

        try:
            guard = User.objects.get(id=guard_id)
        except (User.DoesNotExist, TypeError):
            return Response({"error": "Invalid guard ID"}, status=status.HTTP_400_BAD_REQUEST), None

        assignment = Assignment.objects.filter(
            id=assigned_id,
            guard=guard,
            shift_id=shift_id,
            checkpoints__contains=[{'checkpoint_id': checkpoint_id}]
        ).first()
        if not assignment:
            return Response(
                {"error": "No matching assignment found for guard, shift, and checkpoint"},
                status=status.HTTP_403_FORBIDDEN
            ), None

        checkpoint = Checkpoint.objects.filter(id=checkpoint_id).first()
        if not checkpoint:
            return Response({"error": "Checkpoint not found"}, status=status.HTTP_400_BAD_REQUEST), None

        if qr_data != checkpoint.data:
            return Response(
                {"detail": "Check-in not allowed. QR code mismatch."},
                status=status.HTTP_400_BAD_REQUEST
            ), None

        checkpoint_coords = (checkpoint.latitude, checkpoint.longitude)
        user_coords = (latitude, longitude)
        distance = geodesic(checkpoint_coords, user_coords).meters
        allowed_distance_str = SiteSetting.get_setting(
            key='distance', location_id=guard.location_id, default_value="150"
        )
        allowed_distance = int(allowed_distance_str)
        if distance > allowed_distance:
            return Response(
                {"error": f"Check-in location is too far from checkpoint ({int(distance)}m)"},
                status=status.HTTP_403_FORBIDDEN
            ), None

        shift = Shift.objects.filter(id=shift_id).first()
        if not shift:
            return Response({"error": "Shift not found"}, status=status.HTTP_400_BAD_REQUEST), None

        def get_all_checkpoint_times(assignment, cid):
            return [cp.get("time") for cp in (assignment.checkpoints or []) if cp.get("checkpoint_id") == cid]

        checkpoint_times_str = get_all_checkpoint_times(assignment, checkpoint_id)
        if not checkpoint_times_str:
            return Response(
                {"error": "Checkpoint time not found in assignment"},
                status=status.HTTP_400_BAD_REQUEST
            ), None

        # Use scan timestamp from request if present (from scan_v2 scan_context); else use now().
        # submit_checklist_v2 must send the timestamp returned by scan_v2 so we validate against scan time, not submit time.
        scan_timestamp_str = data.get("timestamp")
        if scan_timestamp_str:
            try:
                # Parse ISO format (e.g. from scan_context.timestamp)
                if isinstance(scan_timestamp_str, str) and scan_timestamp_str.strip():
                    s = scan_timestamp_str.strip().replace("Z", "+00:00")
                    parsed = datetime.fromisoformat(s)
                    if parsed.tzinfo is None:
                        parsed = make_aware(parsed, pytz.UTC)
                    checkin_time_utc = parsed
                else:
                    checkin_time_utc = now()
            except (ValueError, TypeError):
                checkin_time_utc = now()
        else:
            checkin_time_utc = now()
        checkin_time_user = to_user_timezone(checkin_time_utc, user_tz)
        is_overnight = shift.end_time <= shift.start_time

        best_match = None
        best_match_str = None
        min_time_diff = float('inf')
        for checkpoint_time_str in checkpoint_times_str:
            checkpoint_time = datetime.strptime(checkpoint_time_str, "%H:%M").time()
            checkpoint_date = user_today
            expected_dt_utc = combine_date_time_in_user_tz(checkpoint_date, checkpoint_time, user_tz)
            expected_dt_user = to_user_timezone(expected_dt_utc, user_tz)
            time_diff = abs((checkin_time_user - expected_dt_user).total_seconds()) / 60
            if time_diff < min_time_diff:
                min_time_diff = time_diff
                best_match = checkpoint_time
                best_match_str = checkpoint_time_str

        allowed_delay_str = SiteSetting.get_setting(
            key='time', location_id=guard.location_id, default_value="15"
        )
        allowed_delay = int(allowed_delay_str)
        delayed = min_time_diff > allowed_delay

        matched_entry = None
        if best_match_str is not None:
            for cp in (assignment.checkpoints or []):
                if str(cp.get("checkpoint_id")) == str(checkpoint_id) and cp.get("time") == best_match_str:
                    matched_entry = cp
                    break

        ctx = {
            "guard": guard,
            "assignment": assignment,
            "checkpoint": checkpoint,
            "shift": shift,
            "best_match": best_match,
            "best_match_str": best_match_str,
            "matched_entry": matched_entry,
            "min_time_diff": min_time_diff,
            "allowed_delay": allowed_delay,
            "delayed": delayed,
            "distance": distance,
            "data": data,
            "user_tz": user_tz,
            "checkin_time_utc": checkin_time_utc,
            "latitude": latitude,
            "longitude": longitude,
            "guard_id": guard_id,
            "shift_id": shift_id,
            "assigned_id": assigned_id,
            "checkpoint_id": checkpoint_id,
            'timestamp': checkin_time_user,
        }
        return None, ctx

    @action(detail=False, methods=["post"], url_path="scan_v2")
    def scan_v2(self, request):
        """
        Same validation as scan. If the matched checkpoint slot has a checklist_template_id,
        return requires_checklist + template + items + scan_context (no CheckIn created).
        Otherwise create CheckIn with has_checklist=False and return success.
        """
        err, ctx = self._validate_scan_request(request)
        if err is not None:
            return err

        if ctx["delayed"]:
            return Response(
                {"error": "Check-in not allowed. Delayed."},
                status=status.HTTP_400_BAD_REQUEST
            )

        matched_entry = ctx.get("matched_entry") or {}
        checklist_template_id = matched_entry.get("checklist_template_id")

        if checklist_template_id:
            template = ChecklistTemplate.objects.filter(
                id=checklist_template_id, is_deleted=False
            ).first()
            if not template:
                return Response(
                    {"error": "Checklist template not found"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            items = []
            for item in (template.checklist_items or []):
                item = dict(item)
                if item.get("checklist_item_id") and not item.get("label"):
                    try:
                        ci = ChecklistItem.objects.get(id=item["checklist_item_id"], is_deleted=False)
                        item["label"] = ci.label
                    except ChecklistItem.DoesNotExist:
                        pass
                items.append(item)
            return Response({
                "requires_checklist": True,
                "checklist_template_id": str(template.id),
                "checklist_template_name": template.name,
                "items": items,
                "scan_context": {
                    "guard_id": str(ctx["guard"].id),
                    "shift_id": str(ctx["shift"].id),
                    "assign_id": str(ctx["assignment"].id),
                    "checkpoint_id": str(ctx["checkpoint"].id),
                    "latitude": ctx["latitude"],
                    "longitude": ctx["longitude"],
                    "timestamp": ctx["checkin_time_utc"].isoformat(),
                    "data": ctx["data"].get("data"),
                },
            }, status=status.HTTP_200_OK)

        # No checklist: create CheckIn as usual with has_checklist=False
        data = dict(ctx["data"])
        data["has_checklist"] = False
        serializer = self.get_serializer(data=data)
        serializer.is_valid(raise_exception=True)
        serializer.context["request"] = request
        instance = serializer.save()
        response_data = serializer.data
        response_data.update({
            "requires_checklist": False,
            "delayed": ctx["delayed"],
            "message": "Checkpoint Successfully Scanned",
            "success": True,
            "distance_from_checkpoint_m": round(ctx["distance"], 2),
        })
        return Response(response_data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="submit_checklist_v2")
    def submit_checklist_v2(self, request):
        """
        Accepts scan context (guard, shift, assign_id, checkpoint, lat/lon/timestamp/data) plus
        checklist_template_id, answers, remarks. Re-validates scan; then creates CheckIn with
        has_checklist=True and one CheckInChecklistAnswer.
        """
        data = request.data
        checklist_template_id = data.get("checklist_template_id")
        answers = data.get("answers")
        remarks = data.get("remarks", "")

        if not checklist_template_id:
            return Response(
                {"error": "checklist_template_id is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        if answers is None:
            return Response(
                {"error": "answers is required"},
                status=status.HTTP_400_BAD_REQUEST
            )

        err, ctx = self._validate_scan_request(request)
        if err is not None:
            return err

        if ctx["delayed"]:
            return Response(
                {"error": "Check-in not allowed. Delayed."},
                status=status.HTTP_400_BAD_REQUEST
            )

        template = ChecklistTemplate.objects.filter(
            id=checklist_template_id, is_deleted=False
        ).first()
        if not template:
            return Response(
                {"error": "Checklist template not found"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Template's allowed item IDs (from checklist_items JSON)
        template_item_ids = {
            str(entry.get("checklist_item_id"))
            for entry in (template.checklist_items or [])
            if entry.get("checklist_item_id")
        }
        if not template_item_ids:
            return Response(
                {"error": "Checklist template has no items"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate: each answer must reference a template item; we must have exactly one answer per template item
        seen_ids = set()
        for entry in answers:
            item_id = entry.get("checklist_item_id")
            if not item_id:
                return Response(
                    {"error": "Each answer must have checklist_item_id"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            item_id_str = str(item_id)
            if item_id_str not in template_item_ids:
                return Response(
                    {"error": f"checklist_item_id {item_id_str} is not part of this template"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if item_id_str in seen_ids:
                return Response(
                    {"error": f"Duplicate answer for checklist_item_id {item_id_str}"},
                    status=status.HTTP_400_BAD_REQUEST
                )
            seen_ids.add(item_id_str)

        if seen_ids != template_item_ids:
            missing = template_item_ids - seen_ids
            return Response(
                {"error": f"Missing answers for template items: {list(missing)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Normalize answers: ensure each entry has label (from request or from ChecklistItem)
        answers_with_labels = []
        for entry in answers:
            entry = dict(entry)
            if not entry.get("label") and entry.get("checklist_item_id"):
                try:
                    ci = ChecklistItem.objects.get(
                        id=entry["checklist_item_id"], is_deleted=False
                    )
                    entry["label"] = ci.label
                except ChecklistItem.DoesNotExist:
                    pass
            answers_with_labels.append(entry)

        # Create CheckIn with has_checklist=True; use validated scan time as check-in timestamp
        payload = dict(ctx["data"])
        payload["has_checklist"] = True
        payload["timestamp"] = ctx["checkin_time_utc"]
        serializer = self.get_serializer(data=payload)
        serializer.is_valid(raise_exception=True)
        serializer.context["request"] = request
        checkin = serializer.save()

        # Create CheckInChecklistAnswer (store answers with labels)
        CheckInChecklistAnswer.objects.create(
            user=ctx["guard"],
            checkin=checkin,
            location=ctx["guard"].location,
            checkpoint=ctx["checkpoint"],
            checklist_template=template,
            answers=answers_with_labels,
            remarks=remarks or "",
        )

        response_data = self.get_serializer(checkin).data
        response_data["message"] = "Checklist submitted successfully"
        response_data["success"] = True
        return Response(response_data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=["get"], url_path="checklist")
    def checklist(self, request, pk=None):
        """
        Returns checklist details for a check-in (only if has_checklist=True).
        """
        checkin = self.get_queryset().filter(id=pk).first()
        if not checkin:
            return Response({"error": "CheckIn not found"}, status=status.HTTP_404_NOT_FOUND)

        if not checkin.has_checklist:
            return Response({"error": "This check-in has no checklist"}, status=status.HTTP_400_BAD_REQUEST)

        answer = CheckInChecklistAnswer.objects.filter(
            checkin=checkin,
            is_deleted=False
        ).select_related("checklist_template").first()

        if not answer:
            return Response({"error": "Checklist answers not found"}, status=status.HTTP_404_NOT_FOUND)

        items = []
        answers = answer.answers or []
        # Ensure stable ordering if sort_order exists
        try:
            items = sorted(answers, key=lambda x: x.get("sort_order", 10**9))
        except Exception:
            items = answers

        return Response({
            "checkin_id": str(checkin.id),
            "checklist_template_id": str(answer.checklist_template.id) if answer.checklist_template else None,
            "checklist_template_name": answer.checklist_template.name if answer.checklist_template else None,
            "remarks": answer.remarks or "",
            "answers": items,
        }, status=status.HTTP_200_OK)
