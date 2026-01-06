"""
Custom MySQL database backend with automatic connection retry.
"""
from django.db.backends.mysql.base import (
    DatabaseWrapper as MySQLDatabaseWrapper,
    Database as MySQLDatabase
)
from django.db.utils import OperationalError
import logging
import time

logger = logging.getLogger(__name__)

# Import the Database class for compatibility
Database = MySQLDatabase

class DatabaseWrapper(MySQLDatabaseWrapper):
    """
    Custom MySQL database wrapper that automatically retries failed connections.
    """
    
    def get_new_connection(self, conn_params):
        """
        Override to add retry logic for connection timeouts.
        """
        max_retries = 3
        retry_delay = 1.0  # Start with 1 second delay
        
        for attempt in range(max_retries):
            try:
                # Try to get connection
                connection = super().get_new_connection(conn_params)
                if attempt > 0:
                    logger.info(f"Database connection succeeded on attempt {attempt + 1}")
                return connection
            except OperationalError as e:
                error_str = str(e)
                # Check if it's a connection timeout error
                if '2003' in error_str or 'timed out' in error_str.lower():
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Database connection timeout (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {retry_delay} seconds..."
                        )
                        time.sleep(retry_delay)
                        retry_delay *= 2  # Exponential backoff: 1s, 2s, 4s
                        continue
                    else:
                        logger.error(f"Database connection failed after {max_retries} attempts: {e}")
                        raise
                else:
                    # Not a connection timeout, re-raise immediately
                    raise
            except Exception as e:
                # Non-connection errors, re-raise immediately
                raise
        
        # Should never reach here, but just in case
        raise OperationalError("Database connection failed after retries")
    
    def ensure_connection(self):
        """
        Override to add retry logic when ensuring connection.
        """
        if self.connection is not None:
            # Connection exists, check if it's still valid
            try:
                # Try a simple query to check if connection is alive
                with self.cursor() as cursor:
                    cursor.execute("SELECT 1")
                return
            except (OperationalError, Exception):
                # Connection is dead, close it
                logger.warning("Database connection is dead, closing it")
                try:
                    self.close()
                except Exception:
                    pass
                self.connection = None
        
        # Try to get new connection with retry logic
        max_retries = 2
        retry_delay = 0.5
        
        for attempt in range(max_retries):
            try:
                super().ensure_connection()
                if attempt > 0:
                    logger.info(f"Database connection ensured on attempt {attempt + 1}")
                return
            except OperationalError as e:
                error_str = str(e)
                if '2003' in error_str or 'timed out' in error_str.lower() or 'gone away' in error_str.lower():
                    if attempt < max_retries - 1:
                        logger.warning(
                            f"Database connection error when ensuring connection (attempt {attempt + 1}/{max_retries}): {e}. "
                            f"Retrying in {retry_delay} seconds..."
                        )
                        # Close any stale connections
                        try:
                            self.close()
                        except Exception:
                            pass
                        self.connection = None
                        time.sleep(retry_delay)
                        retry_delay *= 2
                        continue
                    else:
                        logger.error(f"Database connection failed after {max_retries} attempts: {e}")
                        raise
                else:
                    raise
            except Exception as e:
                raise

