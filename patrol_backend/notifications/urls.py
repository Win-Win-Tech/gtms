from django.urls import path

from .views import (
    DeviceTokenView,
    NotificationListView,
    NotificationReadAllView,
    NotificationReadView,
)

urlpatterns = [
    path("device-token/", DeviceTokenView.as_view(), name="notification-device-token"),
    path("", NotificationListView.as_view(), name="notification-list"),
    path("read-all/", NotificationReadAllView.as_view(), name="notification-read-all"),
    path("<uuid:notification_id>/read/", NotificationReadView.as_view(), name="notification-read"),
]
