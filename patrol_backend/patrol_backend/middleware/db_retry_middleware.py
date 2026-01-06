"""
Middleware to automatically retry database operations on connection errors.
"""
import logging
from django.db.utils import OperationalError
from django.http import JsonResponse
from patrol_backend.utils.db_retry import retry_db_operation, close_old_connections

logger = logging.getLogger(__name__)

class DatabaseRetryMiddleware:
    """
    Middleware that automatically retries database operations on connection errors.
    """
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # Try to get response, retry on connection errors
        try:
            response = self.get_response(request)
            return response
        except OperationalError as e:
            error_str = str(e)
            # Check if it's a connection timeout error
            if '2003' in error_str or 'timed out' in error_str.lower():
                logger.warning(f"Database connection timeout in middleware, closing stale connections: {e}")
                # Close all connections and retry once
                close_old_connections()
                try:
                    response = self.get_response(request)
                    return response
                except OperationalError as retry_error:
                    logger.error(f"Database connection failed after retry: {retry_error}")
                    return JsonResponse({
                        'status': 'error',
                        'message': 'Database connection timeout. Please try again.',
                        'data': {},
                        'status_code': 503
                    }, status=503)
            else:
                # Re-raise other database errors
                raise

