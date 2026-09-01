from django.urls import path, include
from rest_framework.routers import DefaultRouter

from .views import LiveTrackingViewSet
from .views_alerts import (
    SiteAlertRecipientConfigView,
    TrackingAlertInboxView,
    TrackingAlertReadAllView,
    TrackingAlertReadView,
)

router = DefaultRouter()
router.register(r'', LiveTrackingViewSet, basename='livetracking')

urlpatterns = [
    path('alerts/', TrackingAlertInboxView.as_view(), name='tracking-alerts-inbox'),
    path('alerts/read-all/', TrackingAlertReadAllView.as_view(), name='tracking-alerts-read-all'),
    path('alerts/<uuid:alert_id>/read/', TrackingAlertReadView.as_view(), name='tracking-alert-read'),
    path(
        'sites/<uuid:site_id>/alert-recipients/',
        SiteAlertRecipientConfigView.as_view(),
        name='site-alert-recipients',
    ),
    path('', include(router.urls)),
]
