from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    UserCreateView,
    LoginView,
    UserListView,
    UserUpdateView,
    UserDeleteView,
    ToggleUserActiveView,
    UserDetailView,
    UserByRoleView,
    TimezoneListView,
    RoleViewSet,
    MobileSelfProfileView,
    RefreshTokenView,
)
from .views_v5 import (
    MySitesViewV5,
    UserByRoleViewV5,
    UserCreateViewV5,
    UserDetailViewV5,
    UserListViewV5,
    UserUpdateViewV5,
)

router = DefaultRouter()
router.register(r'auth/roles', RoleViewSet, basename='roles')


urlpatterns = [
    path('auth/users/', UserCreateView.as_view(), name='create_user'),
    path('auth/mobile/profile/', MobileSelfProfileView.as_view(), name='mobile_self_profile'),
    path('auth/login/', LoginView.as_view(), name='login_user'),
    path('auth/refresh-token/', RefreshTokenView.as_view(), name='refresh_token'),
    path('auth/users/<uuid:id>/detail/', UserDetailView.as_view(), name='user_detail'),
    path('auth/users/list/', UserListView.as_view(), name='list_users'),
    path('auth/users/<uuid:id>/', UserUpdateView.as_view(), name='update_user'),       # PATCH/PUT
    path('auth/users/<uuid:id>/delete/', UserDeleteView.as_view(), name='delete_user'), # DELETE
    path('auth/users/<uuid:id>/toggle-active/', ToggleUserActiveView.as_view(), name='toggle_user_active'), # PATCH
    path('users/by-role/', UserByRoleView.as_view(), name='users-by-role'),
    path('auth/timezones/', TimezoneListView.as_view(), name='list_timezones'),
    path('auth/v5/users/', UserCreateViewV5.as_view(), name='create_user_v5'),
    path('auth/v5/users/<uuid:id>/detail/', UserDetailViewV5.as_view(), name='user_detail_v5'),
    path('auth/v5/users/list/', UserListViewV5.as_view(), name='list_users_v5'),
    path('auth/v5/users/<uuid:id>/', UserUpdateViewV5.as_view(), name='update_user_v5'),
    path('users/v5/by-role/', UserByRoleViewV5.as_view(), name='users-by-role-v5'),
    path('auth/v5/my-sites/', MySitesViewV5.as_view(), name='my_sites_v5'),
    path('', include(router.urls)),
]

