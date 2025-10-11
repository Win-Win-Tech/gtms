from .views import *
from .views import AttendanceCheckinViewSet, AttendanceCheckinListView
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DashboardCheckInReportView


router = DefaultRouter()
router.register(r'attendance', AttendanceCheckinViewSet, basename="attendance")

urlpatterns = [
    path('performance/', GuardPerformanceView.as_view()),
    path('stats/', TourStatsView.as_view()),
    path('export/csv/', ExportTourLogsCSV.as_view()),
    path('routes/<uuid:guard_id>/', PatrolRouteView.as_view()),
    path('', include(router.urls)),
    path('dashboard-checkin-report/', DashboardCheckInReportView.as_view(), name='dashboard-checkin-report'),
    path("api/attendance/", AttendanceCheckinListView.as_view(), name="attendance-api"),
   
]




