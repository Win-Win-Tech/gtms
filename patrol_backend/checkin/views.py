import logging
from rest_framework.exceptions import ValidationError
from rest_framework import viewsets
from .models import CheckIn
from .serializers import CheckInSerializer
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status


logger = logging.getLogger(__name__)

class CheckInViewSet(viewsets.ModelViewSet):
    queryset = CheckIn.objects.all()
    serializer_class = CheckInSerializer
    permission_classes = [IsAuthenticated]

    def create(self, request, *args, **kwargs):
        data = request.data

        try:
            # Site settings as key-value map
            settings = {s.key: s.value for s in SiteSetting.objects.all()}
            max_distance = int(settings.get('max_distance_m', 50))
            max_delay = int(settings.get('max_delay_min', 15))

            # Extract and validate input
            guard_id = data.get('guard')
            shift_id = data.get('shift')
            assigned_id = data.get('assign_id')
            checkpoint_id = data.get('checkpoint')
            timestamp = data.get('timestamp')
            latitude = float(data.get('latitude'))
            longitude = float(data.get('longitude'))
            qr_data = data.get('data')

            print("data", data)

            # Rule 1: Validate guard
            try:
                guard = User.objects.get(id=guard_id)
            except User.DoesNotExist:
                return Response({"error": "Invalid guard ID"}, status=status.HTTP_400_BAD_REQUEST)

            # Rule 2 & 3: Validate assignment
            assignment = Assignment.objects.filter(
                id=assigned_id,
                guard=guard,
                shift_id=shift_id,
                checkpoints__contains=[{'checkpoint_id': checkpoint_id}]
            ).first()

            if not assignment:
                return Response({
                    "error": "No matching assignment found for guard, shift, and checkpoint"
                }, status=status.HTTP_403_FORBIDDEN)

            # Rule 4: Validate checkpoint
            checkpoint = Checkpoint.objects.filter(id=checkpoint_id).first()
            if not checkpoint:
                return Response({"error": "Checkpoint not found"}, status=status.HTTP_400_BAD_REQUEST)

            if qr_data != checkpoint.data:
                return Response({"error": "QR code mismatch"}, status=status.HTTP_400_BAD_REQUEST)

            # Validate location proximity
            checkpoint_coords = (checkpoint.latitude, checkpoint.longitude)
            user_coords = (latitude, longitude)
            distance = geodesic(checkpoint_coords, user_coords).meters

            if distance > max_distance:
                return Response({
                    "error": f"Check-in location is too far from checkpoint (> {int(distance)}m)"
                }, status=status.HTTP_403_FORBIDDEN)

            # Rule 5: Validate timestamp proximity
            shift = Shift.objects.filter(id=shift_id).first()
            if not shift:
                return Response({"error": "Shift not found"}, status=status.HTTP_400_BAD_REQUEST)

            def get_checkpoint_time(assignment, checkpoint_id):
                for checkpoint in assignment.checkpoints:
                    if checkpoint.get("checkpoint_id") == checkpoint_id:
                        return checkpoint.get("time")
                return None

            shift_start_str = get_checkpoint_time(assignment, checkpoint_id)
            if not shift_start_str:
                return Response({"error": "Checkpoint time not found in assignment"}, status=status.HTTP_400_BAD_REQUEST)

            try:
                shift_start_time = datetime.strptime(shift_start_str, "%H:%M").time()
            except ValueError:
                return Response({"error": "Invalid checkpoint time format"}, status=status.HTTP_400_BAD_REQUEST)

            shift_start_dt = make_aware(datetime.combine(now().date(), shift_start_time))
            checkin_time = make_aware(datetime.fromisoformat(timestamp)) if timestamp else now()

            time_diff = abs((checkin_time - shift_start_dt).total_seconds()) / 60
            delayed = time_diff > max_delay

            if delayed:         
                return Response({"error": "Check-in not allowed. Delayed."}, status=status.HTTP_400_BAD_REQUEST)

            # Save check-in
            serializer = self.get_serializer(data=data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)

            response_data = serializer.data
            response_data['delayed'] = delayed
            response_data['distance_from_checkpoint_m'] = round(distance, 2)

            return Response(response_data, status=status.HTTP_201_CREATED)

        except ValueError as ve:
            logger.warning(f"Value error during check-in: {ve}")
            return Response({"error": str(ve)}, status=status.HTTP_400_BAD_REQUEST)

        except Exception as e:
            logger.error(f"Unexpected error during check-in: {e}", exc_info=True)
            return Response(
                {"error": "An unexpected error occurred during check-in."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
