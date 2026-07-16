from django.urls import path

from .views import (
    RollCallDashboardFilterView,
    RollCallEndSessionView,
    RollCallSessionListView,
    RollCallStartSessionView,
)

urlpatterns = [
    path("sessions/", RollCallSessionListView.as_view(), name="rollcall-sessions-list"),
    path("sessions/start/", RollCallStartSessionView.as_view(), name="rollcall-sessions-start"),
    path(
        "sessions/<uuid:session_id>/end/",
        RollCallEndSessionView.as_view(),
        name="rollcall-sessions-end",
    ),
    path(
        "dashboard/filter/",
        RollCallDashboardFilterView.as_view(),
        name="rollcall-dashboard-filter",
    ),
]
