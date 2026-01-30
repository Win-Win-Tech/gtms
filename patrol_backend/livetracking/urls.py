from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LiveTrackingViewSet

router = DefaultRouter()
router.register(r'', LiveTrackingViewSet, basename='livetracking')

urlpatterns = [
    path('', include(router.urls)),
]

