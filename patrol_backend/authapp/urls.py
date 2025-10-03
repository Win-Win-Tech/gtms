from django.urls import path
from .views import UserCreateView, LoginView, UserListView, UserUpdateView, UserDeleteView, ToggleUserActiveView, UserDetailView

urlpatterns = [
    path('auth/users/', UserCreateView.as_view(), name='create_user'),
    path('auth/login/', LoginView.as_view(), name='login_user'),
    path('auth/users/<uuid:id>/detail/', UserDetailView.as_view(), name='user_detail'),
    path('auth/users/list/', UserListView.as_view(), name='list_users'),
    path('auth/users/<uuid:id>/', UserUpdateView.as_view(), name='update_user'),       # PATCH/PUT
    path('auth/users/<uuid:id>/delete/', UserDeleteView.as_view(), name='delete_user'), # DELETE
    path('auth/users/<uuid:id>/toggle-active/', ToggleUserActiveView.as_view(), name='toggle_user_active'), # PATCH
]

