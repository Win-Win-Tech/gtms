from rest_framework import generics, permissions, filters, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User
from .serializers import UserSerializer
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
                
                return Response(api_response("success", "Login successful", {
                    'access': str(refresh.access_token),
                    'refresh': str(refresh),
                    'user':model_to_dict(user, fields=[field.name for field in user._meta.fields]),
                    'user_id': str(user.id),
                    'role': user.role,
                    'is_superuser': user.is_superuser,
                    'location_id': str(user.location.id) if user.location else None
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
        valid_roles = dict(User.ROLE_CHOICES).keys()
        # Filter out invalid roles
        roles = [role for role in roles if role in valid_roles]
        print("roles:", roles )
        if not roles:
            return JsonResponse({'error': 'No valid roles provided.'}, status=400)
        if location_id:
            users = users.filter(location_id=location_id)
            
        users = User.get_by_roles(roles)
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
    