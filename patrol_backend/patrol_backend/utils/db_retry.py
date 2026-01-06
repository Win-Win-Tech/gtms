"""
Database connection retry utility for handling MySQL connection timeouts.
"""
from django.db import connections
from django.db.utils import OperationalError
import logging
import time

logger = logging.getLogger(__name__)

def close_old_connections():
    """Close all database connections to force reconnection."""
    for conn in connections.all():
        try:
            conn.close()
        except Exception as e:
            logger.warning(f"Error closing connection: {e}")

def retry_db_operation(func, max_retries=3, retry_delay=0.5):
    """
    Retry a database operation on connection errors.
    
    Args:
        func: Function to execute
        max_retries: Maximum number of retry attempts
        retry_delay: Delay between retries in seconds
    
    Returns:
        Result of func() if successful
    
    Raises:
        OperationalError if all retries fail
    """
    last_exception = None
    
    for attempt in range(max_retries):
        try:
            return func()
        except OperationalError as e:
            error_str = str(e)
            # Check if it's a connection error
            if '2003' in error_str or 'timed out' in error_str.lower() or 'Connection reset' in error_str or 'gone away' in error_str.lower():
                last_exception = e
                if attempt < max_retries - 1:
                    logger.warning(
                        f"Database connection error (attempt {attempt + 1}/{max_retries}): {e}. "
                        f"Retrying in {retry_delay} seconds..."
                    )
                    # Close stale connections
                    close_old_connections()
                    # Wait before retry
                    time.sleep(retry_delay * (attempt + 1))  # Exponential backoff
                    continue
                else:
                    logger.error(f"Database connection failed after {max_retries} attempts: {e}")
                    raise
            else:
                # Not a connection error, re-raise immediately
                raise
        except Exception as e:
            # Non-database errors, re-raise immediately
            raise
    
    # If we get here, all retries failed
    if last_exception:
        raise last_exception
    raise OperationalError("Database connection failed after retries")

