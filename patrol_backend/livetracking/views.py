from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Q
from django.utils.timezone import now
from .models import UserLiveLocation, UserLocationHistory
from patrol_backend.utils.timezone_utils import to_user_timezone, get_user_timezone_from_request, from_user_timezone
from datetime import datetime, timedelta
import logging
import re

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

    @action(detail=False, methods=['get'], url_path='history')
    def history(self, request):
        """
        Returns paginated historical locations for a specific guard.
        Required query params: user_id, timeframe (e.g., '1h', '24h', '2d', '1w', '1m')
        """
        try:
            user = request.user
            if user.is_anonymous:
                return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

            guard_id = request.query_params.get('user_id')
            timeframe_str = request.query_params.get('timeframe', '24h')
            
            if not guard_id:
                return Response({'error': 'user_id parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

            role = getattr(user, 'role', None)
            user_location_id = getattr(user, 'location_id', None)

            # Build base queryset for the specific guard
            queryset = UserLocationHistory.objects.filter(user_id=guard_id).select_related('user')

            # --- Permission Checking ---
            is_super = role in ['superadmin', 'super_admin'] or user.is_superuser
            
            if not is_super:
                if role in ['admin', 'so', 'fo']:
                    if user_location_id:
                        # Verify the admin's location matches the requested guard's location
                        from authapp.models import User
                        try:
                            guard_user = User.objects.get(id=guard_id)
                            if str(guard_user.location_id) != str(user_location_id):
                                return Response({'error': 'Access denied to context of this guard'}, status=status.HTTP_403_FORBIDDEN)
                        except User.DoesNotExist:
                            return Response({'error': 'Guard not found'}, status=status.HTTP_404_NOT_FOUND)
                        
                        # Prevent seeing history from when the guard was at a completely different site previously
                        queryset = queryset.filter(location_id=user_location_id)
                    else:
                        return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)
                else:
                    return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

            # --- Resolve timezone for the guard's location ---
            # Use the guard's location_id to get the correct timezone
            # (same pattern as dashboard/checkin views)
            guard_location_id = None
            if not is_super:
                guard_location_id = str(user_location_id) if user_location_id else None
            else:
                # For superadmin, get guard's location from the guard user object
                try:
                    from authapp.models import User as AuthUser
                    guard_user_obj = AuthUser.objects.get(id=guard_id)
                    guard_location_id = str(guard_user_obj.location_id) if guard_user_obj.location_id else None
                except Exception:
                    pass
            
            user_tz = get_user_timezone_from_request(request, guard_location_id)

            # --- Time Filtering ---
            # Custom date range takes priority over preset timeframe
            start_date_str = request.query_params.get('start_date')
            end_date_str = request.query_params.get('end_date')
            time_delta = None
            
            if start_date_str and end_date_str:
                try:
                    # Parse datetime strings from the frontend (in user's local time)
                    start_local = datetime.fromisoformat(start_date_str)
                    end_local = datetime.fromisoformat(end_date_str)
                    
                    # Enforce max 1 month for custom range
                    if (end_local - start_local).days > 31:
                        return Response({'error': 'Custom date range must not exceed 1 month'}, status=status.HTTP_400_BAD_REQUEST)
                    
                    # Convert user's local times to UTC for database query
                    start_utc = from_user_timezone(start_local, user_tz)
                    end_utc = from_user_timezone(end_local, user_tz)
                    queryset = queryset.filter(timestamp__gte=start_utc, timestamp__lte=end_utc)
                    time_delta = end_local - start_local
                except (ValueError, TypeError):
                    return Response({'error': 'Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM)'}, status=status.HTTP_400_BAD_REQUEST)
            elif timeframe_str.lower().strip() == 'current_month':
                # Current month: from 1st of this month to now
                current = now()
                start_time = current.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
                queryset = queryset.filter(timestamp__gte=start_time)
                time_delta = current - start_time
            else:
                match = re.match(r'^(\d+)(h|d|w|m)$', timeframe_str.lower().strip())
                if match:
                    value = int(match.group(1))
                    unit = match.group(2)
                    
                    if unit == 'h':
                        time_delta = timedelta(hours=value)
                    elif unit == 'd':
                        time_delta = timedelta(days=value)
                    elif unit == 'w':
                        time_delta = timedelta(weeks=value)
                    elif unit == 'm':
                        time_delta = timedelta(days=30 * value)
                else:
                    time_delta = timedelta(days=1)
                    
                start_time = now() - time_delta
                queryset = queryset.filter(timestamp__gte=start_time)
            
            # Ordered descending for tables to show newest first
            queryset = queryset.order_by('-timestamp')

            # --- Performance Sampling ---
            # Get total count BEFORE sampling (for frontend display)
            total_count = queryset.count()
            
            # Determine sampling rate based on ACTUAL ROW COUNT (not timeframe)
            # This ensures small datasets always return in full
            if total_count <= 5000:
                sample_rate = 1        # Return all rows
            elif total_count <= 20000:
                sample_rate = 5        # Every 5th point
            else:
                sample_rate = 10       # Every 10th point

            results = []
            
            for idx, history_loc in enumerate(queryset):
                # Skip points based on sample rate
                if sample_rate > 1 and idx % sample_rate != 0:
                    continue
                
                timestamp_utc = history_loc.timestamp
                local_dt = to_user_timezone(timestamp_utc, user_tz)
                
                results.append({
                    'id': history_loc.id,
                    'lat': float(history_loc.latitude),
                    'lng': float(history_loc.longitude),
                    'timestamp': local_dt.strftime('%Y-%m-%d %H:%M:%S'),
                    'timestamp_iso': local_dt.isoformat(),
                    'timestamp_utc': timestamp_utc.isoformat(),
                })
                
            return Response({
                'total_count': total_count,
                'sampled_count': len(results),
                'sample_rate': sample_rate,
                'results': results,
            }, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"[HISTORY] Error: {str(e)}", exc_info=True)
            return Response({'error': 'Failed to retrieve location history'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

