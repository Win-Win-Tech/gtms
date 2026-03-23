from .views import *
from .views import AttendanceCheckinViewSet, AttendanceCheckinListView
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import DashboardCheckInReportView, DashboardCheckInReportExcelView, MonthlyAttendanceSummaryViewSet, MonthlyAttendanceExcelViewSet


router = DefaultRouter()
router.register(r'attendance', AttendanceCheckinViewSet, basename="attendance")
router.register(r'attendance-summary', MonthlyAttendanceSummaryViewSet, basename="attendance-summary")  # ✅ Add this
router.register(r'attendance-excel', MonthlyAttendanceExcelViewSet, basename="attendance-excel")  # ✅ Add this

urlpatterns = [
    path('performance/', GuardPerformanceView.as_view()),
    path('stats/', TourStatsView.as_view()),
    path('export/csv/', ExportTourLogsCSV.as_view()),
    path('routes/<uuid:guard_id>/', PatrolRouteView.as_view()),
    path('', include(router.urls)),
    path('dashboard-checkin-report/', DashboardCheckInReportView.as_view(), name='dashboard-checkin-report'),
    path("api/attendance/", AttendanceCheckinListView.as_view(), name="attendance-api"),
    path("api/attendance_v3/", AttendanceCheckinV3ListView.as_view(), name="attendance-api-v3"),
    path('dashboard-checkin-report-excel/', DashboardCheckInReportExcelView.as_view(), name='dashboard-checkin-report-excel'),
    path("api/attendance/export/", AttendanceCheckinExportView.as_view(), name="attendance-export-api"),
    path("api/attendance/export_v3/", AttendanceCheckinV3ExportView.as_view(), name="attendance-export-api-v3"),
  
]




