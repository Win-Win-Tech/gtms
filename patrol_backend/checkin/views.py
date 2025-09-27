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

User = get_user_model()

class CheckInViewSet(viewsets.ModelViewSet):
    queryset = CheckIn.objects.all()
    serializer_class = CheckInSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        try:
            data = request.data
            site_settings = SiteSetting.objects.all()
            # print(site_settings)
            guard_id = data.get('guard')
            shift_id = data.get('shift')
            checkpoint_id = data.get('checkpoint')
            timestamp = data.get('timestamp')
            latitude = float(data.get('latitude'))
            longitude = float(data.get('longitude'))

            # Rule 1: Validate guard exists
            try:
                guard = User.objects.get(id=guard_id)
            except User.DoesNotExist:
                return Response({"error": "Invalid guard ID"}, status=status.HTTP_400_BAD_REQUEST)
            
            print("36")

            # Rule 2 & 3: Validate assignment with matching guard, shift, and checkpoint
            assignment = Assignment.objects.filter(
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
            print("Line-49")

            # Rule 4: Validate lat/lon within 50 meters of checkpoint
            checkpoint = Checkpoint.objects.filter(id=checkpoint_id).first()
            if not checkpoint:
                return Response({"error": "Checkpoint not found"}, status=status.HTTP_400_BAD_REQUEST)

            checkpoint_coords = (checkpoint.latitude, checkpoint.longitude)
            user_coords = (latitude, longitude)
            distance = geodesic(checkpoint_coords, user_coords).meters
            print("Line-59")
            # if distance > 50:
            if distance > site_settings.distance:
                return Response({"error": f"Check-in location is too far from checkpoint (>{int(distance)}m)"},
                                status=status.HTTP_403_FORBIDDEN)

            # Rule 5: Validate timestamp within ±15 minutes of shift start
            shift = Shift.objects.filter(id=shift_id).first()
            if not shift:
                return Response({"error": "Shift not found"}, status=status.HTTP_400_BAD_REQUEST)

            checkin_time = now() if not timestamp else shift.timezone.localize(now()) if hasattr(shift, 'timezone') else now()
            shift_start = shift.start_time

            # Convert shift_start (time) to datetime for subtraction
#            shift_start_dt = datetime.combine(checkin_time.date(), shift_start)
#            time_diff = abs((checkin_time - shift_start_dt).total_seconds()) / 60

                        # Combine shift_start with checkin_time's date
            shift_start_dt = datetime.combine(checkin_time.date(), shift_start)

            # Make shift_start_dt timezone-aware using the same timezone as checkin_time
            if checkin_time.tzinfo:
                shift_start_dt = shift_start_dt.replace(tzinfo=checkin_time.tzinfo)
            else:
                shift_start_dt = make_aware(shift_start_dt)

            # Now subtraction works
            time_diff = abs((checkin_time - shift_start_dt).total_seconds()) / 60

            # delayed = time_diff > 15
            delayed = time_diff > site_settings.time
            print("Line-77")
            print(data)
            # Save the check-in
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)

            response_data = serializer.data
            response_data['delayed'] = delayed
            response_data['distance_from_checkpoint_m'] = round(distance, 2)

            return Response(response_data,
                            status=status.HTTP_202_ACCEPTED if delayed else status.HTTP_201_CREATED)

        except Exception as e:
            print(f"Check-in creation error: {e}")
            return Response({"error": "An unexpected error occurred during check-in."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
