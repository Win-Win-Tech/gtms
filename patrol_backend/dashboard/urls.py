from .views import *
from .views import AttendanceCheckinViewSet, AttendanceCheckinListView
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    AttendanceWeekOffViewSet,
    DashboardCheckInReportView,
    DashboardCheckInReportExcelView,
    MonthlyAttendanceExcelViewSet,
    MonthlyAttendanceSummaryViewSet,
)


router = DefaultRouter()
router.register(r'attendance', AttendanceCheckinViewSet, basename="attendance")
router.register(r'attendance-summary', MonthlyAttendanceSummaryViewSet, basename="attendance-summary")
router.register(r'attendance-summary-v2', MonthlyAttendanceSummaryViewSetV2, basename="attendance-summary-v2")
router.register(r'attendance-excel', MonthlyAttendanceExcelViewSet, basename="attendance-excel")
router.register(r'attendance-excel-v2', MonthlyAttendanceExcelViewSetV2, basename="attendance-excel-v2")
router.register(r"attendance-weekoff", AttendanceWeekOffViewSet, basename="attendance-weekoff")
router.register(r"attendance-weekoff-v2", AttendanceWeekOffViewSetV2, basename="attendance-weekoff-v2")

urlpatterns = [
    path('performance/', GuardPerformanceView.as_view()),
    path('stats/', TourStatsView.as_view()),
    path('export/csv/', ExportTourLogsCSV.as_view()),
    path('routes/<uuid:guard_id>/', PatrolRouteView.as_view()),
    path('', include(router.urls)),
    path('dashboard-checkin-report/', DashboardCheckInReportView.as_view(), name='dashboard-checkin-report'),
    path('dashboard-checkin-report/v2/', DashboardCheckInReportViewV2.as_view(), name='dashboard-checkin-report-v2'),
    path("api/attendance/", AttendanceCheckinListView.as_view(), name="attendance-api"),
    path("api/attendance_v3/", AttendanceCheckinV3ListView.as_view(), name="attendance-api-v3"),
    path("api/attendance_v4/", AttendanceCheckinV4ListView.as_view(), name="attendance-api-v4"),
    path('dashboard-checkin-report-excel/', DashboardCheckInReportExcelView.as_view(), name='dashboard-checkin-report-excel'),
    path('dashboard-checkin-report-excel/v2/', DashboardCheckInReportExcelViewV2.as_view(), name='dashboard-checkin-report-excel-v2'),
    path("api/attendance/export/", AttendanceCheckinExportView.as_view(), name="attendance-export-api"),
    path("api/attendance/export_v3/", AttendanceCheckinV3ExportView.as_view(), name="attendance-export-api-v3"),
    path("api/attendance/export_v4/", AttendanceCheckinV4ExportView.as_view(), name="attendance-export-api-v4"),
  
]




