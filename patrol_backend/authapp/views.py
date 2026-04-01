from rest_framework import generics, permissions, filters, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, Role
from .serializers import UserSerializer, RoleSerializer
from patrol_backend.utils.response import api_response
from django.forms.models import model_to_dict 


# 1. Create user
class UserCreateView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]

    def create(self, request, *args, **kwargs):
        try:
            response = super().create(request, *args, **kwargs)
            return Response(api_response("success", "User created successfully", response.data, status.HTTP_201_CREATED))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))

class UserDetailView(generics.RetrieveAPIView):
    queryset = User.objects.filter(is_deleted=False)
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'id'

    def retrieve(self, request, *args, **kwargs):
        try:
            response = super().retrieve(request, *args, **kwargs)
            return Response(api_response("success", "User details fetched", response.data, status.HTTP_200_OK))
        except User.DoesNotExist:
            return Response(api_response("error", "User not found", None, status.HTTP_404_NOT_FOUND))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))

class UserUpdateView(generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'id'

    def update(self, request, *args, **kwargs):
        try:
            response = super().update(request, *args, **kwargs)
            return Response(api_response("success", "User updated successfully", response.data, status.HTTP_200_OK))
        except User.DoesNotExist:
            return Response(api_response("error", "User not found", None, status.HTTP_404_NOT_FOUND))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))

class UserDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, id):
        try:
            user = User.objects.get(id=id)
            user.delete(user=request.user)
            return Response(api_response("success", "User deleted successfully", None, status.HTTP_204_NO_CONTENT))
        except User.DoesNotExist:
            return Response(api_response("error", "User not found", None, status.HTTP_404_NOT_FOUND))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))

class ToggleUserActiveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, id):
        try:
            user = User.objects.get(id=id)
            user.is_active = not user.is_active
            user.save()
            return Response(api_response("success", "User active status toggled", {
                "id": str(user.id),
                "is_active": user.is_active
            }, status.HTTP_200_OK))
        except User.DoesNotExist:
            return Response(api_response("error", "User not found", None, status.HTTP_404_NOT_FOUND))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))

# 2. Login user
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        try:
            email = request.data.get('email')
            password = request.data.get('password')
            user = authenticate(request, email=email, password=password)
            if user:
                refresh = RefreshToken.for_user(user)
                # From location: False = use other check-in flow (no QR). No location = False so app doesn't assume QR.
                # Fetch dynamic permissions and web app allowance from Role table
                role_permissions = []
                is_allow_webapp = False
                
                if user.role:
                    # Look for Role specific to location
                    role_obj = Role.objects.filter(name__iexact=user.role, location=user.location).first()
                    
                    if role_obj:
                        role_permissions = role_obj.pages
                        is_allow_webapp = role_obj.is_allow_webapp
                        
                # Master superuser (is_superuser=True) gets all permissions if no role found
                if user.is_superuser and not role_permissions:
                    # Master fallback for pure superadmins (hardcoded to ensure access)
                    role_permissions = ['Dashboard', 'Users', 'Organization', 'Payslip', 'Live Tracking', 'Incident', 'Settings', 'Role Management']
                    is_allow_webapp = True

                is_qr_scan_enabled = getattr(user.location, 'is_qr_scan_enable', False) if user.location else False

                return Response(api_response("success", "Login successful", {
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                    'user':model_to_dict(user, fields=[field.name for field in user._meta.fields]),
                    'user_id': str(user.id),
                    'role': user.role,
                    'is_allow_webapp': is_allow_webapp,
                    'permissions': role_permissions,
                    'is_superuser': user.is_superuser,
                    'location_id': str(user.location.id) if user.location else None,
                    'timezone': user.timezone,
                    'is_qr_scan_enabled': is_qr_scan_enabled
                }, status.HTTP_200_OK))
            return Response(api_response("error", "Invalid credentials", None, status.HTTP_401_UNAUTHORIZED))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))

# 3. List users with search and filter
class UserListView(generics.ListAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'email']

    def get_queryset(self):
        queryset = User.objects.filter(is_deleted=False)
        role = self.request.query_params.get('role')
        location_id = self.request.query_params.get('location_id')
        if role:
            queryset = queryset.filter(role=role)
        if location_id:
            queryset = queryset.filter(location_id=location_id)
        return queryset

    def list(self, request, *args, **kwargs):
        try:
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(api_response("success", "Users fetched", serializer.data, status.HTTP_200_OK))
            serializer = self.get_serializer(queryset, many=True)
            return Response(api_response("success", "Users fetched", serializer.data, status.HTTP_200_OK))
        except Exception as e:
            return Response(api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR))
        


from django.http import JsonResponse
from django.views import View
from .models import User

class UserByRoleView(View):
    def get(self, request):
        roles = request.GET.getlist('roles')
        location_id = request.GET.get('location_id', None)
        
        # We no longer strictly validate against hardcoded User.ROLE_CHOICES natively
        if not roles:
            return JsonResponse({'error': 'No roles provided.'}, status=400)
        
        # User.get_by_roles now filters by matching string names
        users = User.get_by_roles(roles)
            
        if location_id:
            users = users.filter(location_id=location_id)
            
        # users = User.get_by_roles(roles)
        data = [
            {
                'id': str(user.id),
                'email': user.email,
                'name': user.name,
                'role': user.role,
                'phone_no': user.phone_no,
            }
            for user in users
        ]
        return JsonResponse(data, safe=False)


class TimezoneListView(APIView):
    """
    API endpoint to get all available timezones from pytz.
    Returns timezones in a flat list for easy dropdown usage.
    """
    permission_classes = [IsAuthenticated]  # Only authenticated users can access
    
    def get(self, request):
        import pytz
        
        # Get all timezones from pytz
        all_timezones = pytz.all_timezones
        
        # Return flat list with formatted labels
        timezones_list = [
            {'value': tz, 'label': tz.replace('_', ' ')}
            for tz in all_timezones
        ]
        
        return Response(api_response(
            "success",
            "Timezones fetched successfully",
            {
                'timezones': timezones_list,
                'total_count': len(all_timezones)
            },
            status.HTTP_200_OK
        ))

class RoleViewSet(viewsets.ModelViewSet):
    """
    CRUD endpoint for Roles. Filters by the token's user.location or Global (superadmin).
    """
    serializer_class = RoleSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        location_id = self.request.query_params.get('location_id')

        # If location_id is provided in params, fetch roles for that location
        if location_id:
            # Security: Non-superadmins should only fetch for their own location
            if user.location and str(user.location.id) != location_id and not user.is_superuser:
                return Role.objects.none()
            
            queryset = Role.objects.filter(location_id=location_id)
            
            # Security: Only superusers can see/assign the 'Admin' role
            if not user.is_superuser:
                queryset = queryset.exclude(name__iexact='admin')
                
            return queryset
        
        # Default behavior:
        # Superuser or Superadmin (no location) manages Global role templates (NULL location)
        if user.is_superuser or not user.location or (user.role and user.role.lower() == 'superadmin'):
            return Role.objects.filter(location__isnull=True)

        # Org Admin: Strictly manage roles for their own location
        return Role.objects.filter(location=user.location)

    def perform_create(self, serializer):
        user = self.request.user
        if user.is_superuser or not user.location or (user.role and user.role.lower() == 'superadmin'):
            # Superadmin creates global default roles
            serializer.save(location=None, is_default=True)
        else:
            # Org admin creates organization-specific role (override or custom)
            serializer.save(location=user.location, is_default=False)
            
    def perform_update(self, serializer):
        user = self.request.user
        instance = self.get_object()
        old_name = instance.name
        old_pages = instance.pages or []
        
        updated = serializer.save()
        new_name = updated.name
        new_pages = updated.pages or []
        new_is_allow_webapp = updated.is_allow_webapp

        # 1. CASCADE SYSTEM: If a Global Role Template (location=None) is modified
        if updated.location is None:
            # Sync by name: Update all roles with the same name across all locations
            local_roles = Role.objects.filter(name__iexact=old_name).exclude(id=updated.id)
            local_roles.update(
                name=new_name,
                pages=new_pages,
                is_allow_webapp=new_is_allow_webapp
            )

            # Special case: Global Revocation
            # If pages were REMOVED from the Global 'Admin' role, remove them from ALL roles globally
            if old_name.lower() == 'admin':
                removed_pages = [p for p in old_pages if p not in new_pages]
                if removed_pages:
                    all_roles = Role.objects.all().exclude(id=updated.id)
                    for role in all_roles:
                        if role.pages:
                            updated_pages = [p for p in role.pages if p not in removed_pages]
                            if len(updated_pages) != len(role.pages):
                                role.pages = updated_pages
                                role.save()

        # 2. USER SYNC: If the role name changed, update the string field in the User model
        if old_name.lower() != new_name.lower():
            qs = User.objects.filter(role__iexact=old_name)
            if user.location:
                qs = qs.filter(location=user.location)
            qs.update(role=new_name)


    