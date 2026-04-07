from rest_framework import generics, permissions, filters, status, viewsets
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User, Role
from .serializers import UserSerializer, RoleSerializer, UserListSerializer
from patrol_backend.utils.response import api_response
from django.forms.models import model_to_dict


def resolve_role_for_user(user):
    """
    Match User.role to a Role row: prefer location-specific copy, then global template (location=NULL).
    Without this, logins fail open (no webapp / no pages) when a location has no Role rows yet.
    """
    if not user or not user.role:
        return None
    name = user.role.strip()
    if user.location_id:
        role_obj = Role.objects.filter(name__iexact=name, location_id=user.location_id).first()
        if role_obj:
            return role_obj
    return Role.objects.filter(name__iexact=name, location__isnull=True).first()


# 1. Create user
class UserCreateView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

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
    parser_classes = [JSONParser, MultiPartParser, FormParser]

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

                role_obj = resolve_role_for_user(user)
                if role_obj:
                    role_permissions = list(role_obj.pages or [])
                    is_allow_webapp = bool(role_obj.is_allow_webapp)

                # Master superuser (is_superuser=True) gets all permissions if no role found
                if user.is_superuser and not role_permissions:
                    # Master fallback for pure superadmins (hardcoded to ensure access)
                    role_permissions = ['Dashboard', 'Users', 'Organization', 'Payslip', 'Live Tracking', 'Incident', 'Settings', 'Role Management']
                    is_allow_webapp = True

                is_qr_scan_enabled = getattr(user.location, 'is_qr_scan_enable', False) if user.location else False

                face_photo_url = None
                try:
                    if getattr(user, "face_photo", None) and user.face_photo.name:
                        face_photo_url = request.build_absolute_uri(user.face_photo.url)
                except Exception:
                    face_photo_url = None

                user_payload = {
                    "id": str(user.id),
                    "last_login": user.last_login,
                    "is_superuser": user.is_superuser,
                    "aadhar_no": user.aadhar_no,
                    "email": user.email,
                    "name": user.name,
                    "phone_no": user.phone_no,
                    "role": user.role,
                    "location": str(user.location_id) if user.location_id else None,
                    "employee_code": user.employee_code,
                    "timezone": user.timezone,
                    "is_active": user.is_active,
                    "is_staff": user.is_staff,
                    "created_by": str(user.created_by_id) if user.created_by_id else None,
                    "modified_by": str(user.modified_by_id) if user.modified_by_id else None,
                    "is_deleted": user.is_deleted,
                    "deleted_on": user.deleted_on,
                    "deleted_by": str(user.deleted_by_id) if user.deleted_by_id else None,
                    "face_photo": face_photo_url,
                }
                return Response(api_response("success", "Login successful", {
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                    'user': user_payload,
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
    serializer_class = UserListSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'email', 'employee_code']

    def get_queryset(self):
        queryset = User.objects.filter(is_deleted=False)
        role = self.request.query_params.get('role')
        location_id = self.request.query_params.get('location_id')
        
        # Admin restriction: Only superusers can see Admin users
        if not self.request.user.is_superuser:
            queryset = queryset.exclude(role__iexact='admin')

        if role and role.lower() != 'all':
            queryset = queryset.filter(role__iexact=role)
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
                'employee_code': user.employee_code,
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

        if location_id:
            # Security: Non-superadmins should only fetch for their own location
            if user.location and str(user.location.id) != location_id and not user.is_superuser:
                return Role.objects.none()
            loc_qs = Role.objects.filter(location_id=location_id)
            # If this location has no Role rows yet (populate_roles not run), expose global templates
            queryset = loc_qs if loc_qs.exists() else Role.objects.filter(location__isnull=True)
        elif user.is_superuser or not user.location or (user.role and user.role.lower() == 'superadmin'):
            # Superuser or Global admin: See global roles by default if no location specified
            queryset = Role.objects.filter(location__isnull=True)
        else:
            # Org admin: own location's roles, else global templates (same as login resolution)
            loc_qs = Role.objects.filter(location=user.location)
            queryset = loc_qs if loc_qs.exists() else Role.objects.filter(location__isnull=True)

        # Admin restriction: Only superusers can see Admin role
        if not user.is_superuser:
            queryset = queryset.exclude(name__iexact='admin')
        
        return queryset

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


    