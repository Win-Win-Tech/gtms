from django.urls import path
from .views import UserCreateView, LoginView, UserListView

urlpatterns = [
    path('auth/users/', UserCreateView.as_view(), name='create_user'),
    path('auth/login/', LoginView.as_view(), name='login_user'),
    path('auth/users/list/', UserListView.as_view(), name='list_users'),
]
