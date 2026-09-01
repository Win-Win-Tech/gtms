from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from django.utils.timezone import now
from .models import UserLiveLocation, UserLocationHistory
from .live_location_payload import build_location_update_payload
from .ws_groups import _is_superadmin, get_on_duty_user_ids, user_can_view_live_map
from patrol_backend.utils.timezone_utils import to_user_timezone, get_user_timezone_from_request, from_user_timezone
from datetime import datetime, timedelta
import logging
import re

logger = logging.getLogger(__name__)


class LiveTrackingViewSet(viewsets.ViewSet):
    """
    ViewSet for live tracking operations.
    Provides API endpoints to fetch on-duty users' last known locations.
    """

    def _resolve_location_scope(self, user, param_location_id):
        """Return org location_id used to scope on-duty users, or None for all orgs."""
        if _is_superadmin(user):
            if param_location_id and param_location_id not in ("All", "all"):
                return param_location_id
            return None

        user_location_id = getattr(user, "location_id", None)
        if not user_location_id:
            return None
        return user_location_id

    @action(detail=False, methods=['get'], url_path='last-known-locations')
    def last_known_locations(self, request):
        """
        Returns last known locations for users with an open check-in.
        - Superadmin: all on-duty users (optional location_id filter)
        - Live map viewers: on-duty users in their organisation
        """
        try:
            user = request.user
            if user.is_anonymous:
                return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

            if not user_can_view_live_map(user):
                return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

            role = getattr(user, 'role', None)
            param_location_id = request.query_params.get('location_id')
            location_scope = self._resolve_location_scope(user, param_location_id)

            if not _is_superadmin(user) and not location_scope:
                logger.warning("[LKL] User %s has no location assigned.", user.email)
                return Response([], status=status.HTTP_200_OK)

            on_duty_ids = get_on_duty_user_ids(location_id=location_scope)

            queryset = UserLiveLocation.objects.filter(
                user_id__in=on_duty_ids,
                user__is_active=True,
                user__is_deleted=False,
                latitude__isnull=False,
                longitude__isnull=False,
            ).select_related('user', 'location', 'assigned_site')

            if location_scope:
                queryset = queryset.filter(location_id=location_scope)

            result = [build_location_update_payload(live_loc, user) for live_loc in queryset]

            logger.info(
                "[LAST_KNOWN_LOCATIONS] User %s (%s) fetched %s on-duty locations (scope=%s)",
                user.email,
                role,
                len(result),
                location_scope or "all",
            )
            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"[LAST_KNOWN_LOCATIONS] Error: {str(e)}", exc_info=True)
            return Response({'error': 'Failed to retrieve last known locations'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'], url_path='history')
    def history(self, request):
        """
        Returns paginated historical locations for a specific on-duty user.
        Required query params: user_id, timeframe (e.g., '1h', '24h', '2d', '1w', '1m')
        """
        try:
            user = request.user
            if user.is_anonymous:
                return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

            if not user_can_view_live_map(user):
                return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

            subject_user_id = request.query_params.get('user_id')
            timeframe_str = request.query_params.get('timeframe', '24h')

            if not subject_user_id:
                return Response({'error': 'user_id parameter is required'}, status=status.HTTP_400_BAD_REQUEST)

            user_location_id = getattr(user, 'location_id', None)
            queryset = UserLocationHistory.objects.filter(user_id=subject_user_id).select_related('user')

            if not _is_superadmin(user):
                if not user_location_id:
                    return Response({'error': 'Access denied'}, status=status.HTTP_403_FORBIDDEN)

                from authapp.models import User
                try:
                    subject_user = User.objects.get(id=subject_user_id)
                except User.DoesNotExist:
                    return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

                if str(subject_user.location_id) != str(user_location_id):
                    return Response(
                        {'error': 'Access denied to context of this user'},
                        status=status.HTTP_403_FORBIDDEN,
                    )

                queryset = queryset.filter(location_id=user_location_id)

            subject_location_id = None
            if not _is_superadmin(user):
                subject_location_id = str(user_location_id) if user_location_id else None
            else:
                try:
                    from authapp.models import User as AuthUser
                    subject_user_obj = AuthUser.objects.get(id=subject_user_id)
                    subject_location_id = (
                        str(subject_user_obj.location_id) if subject_user_obj.location_id else None
                    )
                except Exception:
                    pass

            user_tz = get_user_timezone_from_request(request, subject_location_id)

            start_date_str = request.query_params.get('start_date')
            end_date_str = request.query_params.get('end_date')
            time_delta = None

            if start_date_str and end_date_str:
                try:
                    start_local = datetime.fromisoformat(start_date_str)
                    end_local = datetime.fromisoformat(end_date_str)

                    if (end_local - start_local).days > 31:
                        return Response(
                            {'error': 'Custom date range must not exceed 1 month'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )

                    start_utc = from_user_timezone(start_local, user_tz)
                    end_utc = from_user_timezone(end_local, user_tz)
                    queryset = queryset.filter(timestamp__gte=start_utc, timestamp__lte=end_utc)
                    time_delta = end_local - start_local
                except (ValueError, TypeError):
                    return Response(
                        {'error': 'Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM)'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            elif timeframe_str.lower().strip() == 'current_month':
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

            queryset = queryset.order_by('-timestamp')
            total_count = queryset.count()

            if total_count <= 5000:
                sample_rate = 1
            elif total_count <= 20000:
                sample_rate = 5
            else:
                sample_rate = 10

            results = []
            for idx, history_loc in enumerate(queryset):
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
