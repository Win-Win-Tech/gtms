from rest_framework import viewsets, status, serializers
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils.timezone import now
from django.contrib.auth import get_user_model
from geopy.distance import geodesic
from datetime import timedelta
from datetime import datetime, time
from .models import CheckIn
from .serializers import CheckInSerializer
from scheduler.models import Assignment, Checkpoint, Shift, SiteSetting
from django.utils.timezone import make_aware
import uuid

User = get_user_model()

class CheckInViewSet(viewsets.ModelViewSet):
    queryset = CheckIn.objects.all()
    serializer_class = CheckInSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            site_settings = SiteSetting.objects.all()
       
            guard_id = data.get('guard')
            shift_id = data.get('shift')
            assigned_id = data.get('assign_id')
            checkpoint_id = data.get('checkpoint')
            timestamp = data.get('timestamp')
            latitude = float(data.get('latitude'))
            longitude = float(data.get('longitude'))
            qr_data = data.get('data')

            # Rule 1: Validate guard exists
            try:
                guard = User.objects.get(id=guard_id)
            except User.DoesNotExist:
                return Response({"error": "Invalid guard ID"}, status=status.HTTP_400_BAD_REQUEST)
            
            # Rule 2 & 3: Validate assignment with matching guard, shift, and checkpoint
            assignment = Assignment.objects.filter(
                id=assigned_id,
                guard=guard,
                shift_id=shift_id,
                checkpoints__contains=[{'checkpoint_id': checkpoint_id}]
            ).first()

            # today = now().date()
            # assignment = Assignment.objects.filter(
            #     guard=guard,
            #     shift_id=shift_id,
            #     start_date__lte=today,
            #     end_date__gte=today,
            #     checkpoints__contains=[{'checkpoint_id': checkpoint_id}]
            # ).first()
       
            if not assignment:
                return Response({"error": "No matching assignment found for guard, shift, and checkpoint"},
                                status=status.HTTP_403_FORBIDDEN)
       
            # Rule 4: Validate lat/lon within 50 meters of checkpoint
            checkpoint = Checkpoint.objects.filter(id=checkpoint_id).first()
            if not checkpoint:
                return Response({"error": "Checkpoint not found"}, status=status.HTTP_400_BAD_REQUEST)
            
            print("checkpoint.data", checkpoint.data)
            print("qr_data", qr_data)

            if qr_data != checkpoint.data:
                return Response(
                    {"detail": "Check-in not allowed. QR code mismatch."},
                    status=status.HTTP_400_BAD_REQUEST
                )
                      
            checkpoint_coords = (checkpoint.latitude, checkpoint.longitude)
            user_coords = (latitude, longitude)
            distance = geodesic(checkpoint_coords, user_coords).meters
       
       
            settings = list(SiteSetting.objects.all().values())
            #print(checkpoint_coords)
            if distance > int(settings[1]['value']):
                return Response({"error": f"Check-in location is too far from checkpoint (>{int(distance)}m)"},
                                status=status.HTTP_403_FORBIDDEN)

            #Rule 5: Validate timestamp within ±15 minutes of shift start

            shift = Shift.objects.filter(id=shift_id).first()
            #shift = Shift.objects.filter(id=uuid.UUID(shift_id)).first()
            # import uuid

            # try:
            #     shift_uuid = uuid.UUID(shift_id)
            # except ValueError:
            #     return Response({"error": "Invalid shift ID format"}, status=status.HTTP_400_BAD_REQUEST)

            # shift = Shift.objects.filter(id=shift_uuid).first()

            def get_checkpoint_time(assignment, checkpoint_id):
                for checkpoint in assignment.checkpoints:
                    if checkpoint.get("checkpoint_id") == checkpoint_id:
                        return checkpoint.get("time")
                return None  # if not found

            if not shift:
                return Response({"error": "Shift not found"}, status=status.HTTP_400_BAD_REQUEST)

            checkin_time = now() if not timestamp else shift.timezone.localize(now()) if hasattr(shift, 'timezone') else now()
            shift_start = shift.start_time
            shift_start=get_checkpoint_time(assignment,checkpoint_id)
            print("assigned", assignment)
            print("checkpoint_start",shift_start)
            # Convert shift_start (time) to datetime for subtraction
#            shift_start_dt = datetime.combine(checkin_time.date(), shift_start)
#            time_diff = abs((checkin_time - shift_start_dt).total_seconds()) / 60

                        # Combine shift_start with checkin_time's date
            #shift_start_dt = datetime.combine(checkin_time.date(), shift_start)

            # Convert string to time object
            shift_start_time_temp = datetime.strptime(shift_start, "%H:%M").time()

            # Combine with date
            shift_start_dt_temp = datetime.combine(checkin_time.date(), shift_start_time_temp)
            shift_start_dt = shift_start_dt_temp
            # Make shift_start_dt timezone-aware using the same timezone as checkin_time
            if checkin_time.tzinfo:
                shift_start_dt = shift_start_dt.replace(tzinfo=checkin_time.tzinfo)
            else:
                shift_start_dt = make_aware(shift_start_dt)

            # Now subtraction works
            time_diff = abs((checkin_time - shift_start_dt).total_seconds()) / 60
            
            print("checkin_time", checkin_time)
            print("shift_start_dt", shift_start_dt)

            # delayed = time_diff > 15
            delayed = time_diff > int(settings[0]['value'])
            print("time_diff",time_diff)
            print("delayed",delayed)
            print("int", int(settings[0]['value']))
            print("Line-77")
            print(data)
            # Save the check-in
            # serializer = self.get_serializer(data=data)
            # serializer.is_valid(raise_exception=True)
            # self.perform_create(serializer)

            # response_data = serializer.data
            # response_data['delayed'] = delayed
            # response_data['distance_from_checkpoint_m'] = round(distance, 2)

            # return Response(response_data,
            #                 status=status.HTTP_202_ACCEPTED if delayed else status.HTTP_201_CREATED)

            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)

            if delayed:
                return Response(
                    {"detail": "Check-in not allowed. Delayed."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            self.perform_create(serializer)

            response_data = serializer.data
            response_data['delayed'] = delayed
            response_data['distance_from_checkpoint_m'] = round(distance, 2)

            return Response(
                response_data,
                status=status.HTTP_201_CREATED
            )


        except Exception as e:
            print(f"Check-in creation error: {e}")
            return Response({"error": "An unexpected error occurred during check-in."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)

