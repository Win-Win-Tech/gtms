from rest_framework import permissions
from authapp.models import Role, PagePermission
import logging

logger = logging.getLogger(__name__)

class HasPagePermission:
    """
    A factory function that returns a custom DRF Permission class.
    Checks if the user has the required PagePermission assigned to their role.
    Master Admins automatically bypass this check.
    
    Usage:
        permission_classes = [IsAuthenticated, HasPagePermission('Shift Assignment')]
    """
    def __init__(self, required_permission_name):
        self.required_permission_name = required_permission_name

    def __call__(self):
        required_perm = self.required_permission_name
        
        class _HasPagePermission(permissions.BasePermission):
            def has_permission(self, request, view):
                user = request.user
                
                # Must be authenticated
                if not user or not user.is_authenticated:
                    return False
                    
                # Admin role bypasses EVERYTHING in their location
                if user.role and user.role.lower() == 'admin':
                    return True
                    
                # Otherwise, fetch their Role config for this specific location
                try:
                    # Prefer a location-specific custom role setup, fallback to global default 'night guard' config
                    role_obj = Role.objects.filter(name__iexact=user.role, location=user.location).first()
                    if not role_obj:
                        role_obj = Role.objects.filter(name__iexact=user.role, is_default=True).first()
                        
                    if not role_obj:
                        return False # Role does not exist in DB config
                    
                    # Check if the role's pages array contains the required permission
                    return required_perm in role_obj.pages
                except Exception as e:
                    logger.error(f"Error checking permissions for {user.email}: {e}")
                    return False
                    
        return _HasPagePermission
