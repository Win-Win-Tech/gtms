from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q
from .models import UserLiveLocation
from patrol_backend.utils.timezone_utils import to_user_timezone
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class LiveTrackingViewSet(viewsets.ViewSet):
    """
    ViewSet for live tracking operations.
    Provides API endpoints to fetch guards' last known locations.
    """

    @action(detail=False, methods=['get'], url_path='last-known-locations')
    def last_known_locations(self, request):
        """
        Returns all guards' last known locations based on the requesting user's role.
        - Superadmin: sees all guards
        - Admin/SO/FO: sees guards from their location only
        """
        try:
            user = request.user
            if user.is_anonymous:
                return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

            role = getattr(user, 'role', None)
            user_location_id = getattr(user, 'location_id', None)
            
            # Get location_id from query params
            param_location_id = request.query_params.get('location_id')

            logger.info(f"[LKL] User: {user.email}, Role: {role}, UserLoc: {user_location_id}, ParamLoc: {param_location_id}")

            # Initial base queryset
            queryset = UserLiveLocation.objects.filter(
                user__role='guard',
                user__is_active=True,
                user__is_deleted=False,
                latitude__isnull=False,
                longitude__isnull=False
            ).select_related('user', 'location')

            # Build queryset based on role and parameters
            is_super = role in ['superadmin', 'super_admin'] or user.is_superuser
            
            if is_super:
                # Superadmin sees all guards by default, or filtered by param
                if param_location_id and param_location_id != 'All':
                    queryset = queryset.filter(location_id=param_location_id)
                logger.info(f"[LKL] Superadmin access. Queryset count: {queryset.count()}")
            elif role in ['admin', 'so', 'fo']:
                # Admin/SO/FO strictly see guards from their assigned location
                if user_location_id:
                    queryset = queryset.filter(location_id=user_location_id)
                    logger.info(f"[LKL] Filtered by location_id: {user_location_id}. Count: {queryset.count()}")
                else:
                    logger.warning(f"[LKL] Admin {user.email} has no location assigned.")
                    queryset = queryset.none()
            else:
                logger.warning(f"[LKL] Access denied for role: {role}")
                return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

            # Convert to response format matching WebSocket payload
            result = []
            for live_loc in queryset:
                guard_user = live_loc.user
                
                # Convert UTC timestamp to user's timezone
                last_updated_utc = live_loc.last_updated
                if last_updated_utc:
                    local_dt = to_user_timezone(last_updated_utc, user)
                    timestamp_str = local_dt.strftime('%Y-%m-%d %H:%M:%S')
                    timestamp_iso = local_dt.isoformat()
                else:
                    timestamp_str = None
                    timestamp_iso = None

                result.append({
                    'type': 'location_update',
                    'user_id': str(guard_user.id),
                    'name': getattr(guard_user, 'name', getattr(guard_user, 'email', 'Unknown Guard')),
                    'role': getattr(guard_user, 'role', 'guard'),
                    'lat': float(live_loc.latitude),
                    'lng': float(live_loc.longitude),
                    'timestamp': timestamp_str,
                    'timestamp_iso': timestamp_iso,
                    'timestamp_utc': last_updated_utc.isoformat() if last_updated_utc else None,
                })

            logger.info(f"[LAST_KNOWN_LOCATIONS] User {user.email} ({role}) fetched {len(result)} guard locations")
            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"[LAST_KNOWN_LOCATIONS] Error: {str(e)}", exc_info=True)
            return Response({'error': 'Failed to retrieve last known locations'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

