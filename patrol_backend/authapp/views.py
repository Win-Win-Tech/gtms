from rest_framework import generics, permissions, filters, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from rest_framework_simplejwt.tokens import RefreshToken
from .models import User
from .serializers import UserSerializer


# 1. Create user
class UserCreateView(generics.CreateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.AllowAny]

class UserDetailView(generics.RetrieveAPIView):
    queryset = User.objects.filter(is_deleted=False)
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'id'  # matches <uuid:id> in URL

class UserUpdateView(generics.UpdateAPIView):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'id'

class UserDeleteView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, id):
        try:
            user = User.objects.get(id=id)
            user.delete(user=request.user)  # soft delete
            return Response(status=status.HTTP_204_NO_CONTENT)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

class ToggleUserActiveView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def patch(self, request, id):
        try:
            user = User.objects.get(id=id)
            user.is_active = not user.is_active
            user.save()
            return Response({'id': str(user.id), 'is_active': user.is_active})
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
# 2. Login user
class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        user = authenticate(request, email=email, password=password)
        if user:
            refresh = RefreshToken.for_user(user)
            return Response({
                'access': str(refresh.access_token),
                'refresh': str(refresh),
                'user_id': str(user.id),
                'role': user.role,
                'is_superuser': user.is_superuser,
                'location_id': str(user.location.id) if user.location else None
            })
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)


# 3. List users with search by name or email AND filter by role
class UserListView(generics.ListAPIView):
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ['name', 'email']

    def get_queryset(self):
        queryset = User.objects.filter(is_deleted=False)
        role = self.request.query_params.get('role', None)
        location_id = self.request.query_params.get('location_id', None)
        if role:
            queryset = queryset.filter(role=role)
        if location_id:
            queryset = queryset.filter(location_id=location_id)            
        return queryset

    def get(self, request, *args, **kwargs):
        print("Authorization Header:", request.headers.get('Authorization'))
        return super().get(request, *args, **kwargs)
