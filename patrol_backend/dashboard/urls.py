from .views import *
from .views import AttendanceCheckinViewSet, AttendanceCheckinListView
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views_v5 import (
    AttendanceCheckinV4ExportViewV5,
    AttendanceCheckinV4ListViewV5,
    AttendanceCheckinV4PdfExportViewV5,
    AttendanceCheckinViewSetV5,
    CreateAssignmentV5,
    DashboardCheckInReportExcelViewV5,
    DashboardCheckInReportPdfViewV5,
    DashboardCheckInReportViewV5,
    MonthlyAttendanceExcelViewSetV5,
    MonthlyAttendanceSummaryViewSetV5,
    ShiftTodayV5,
)


router = DefaultRouter()
router.register(r'attendance', AttendanceCheckinViewSet, basename="attendance")
router.register(r'attendance-summary', MonthlyAttendanceSummaryViewSet, basename="attendance-summary")
router.register(r'attendance-summary-v2', MonthlyAttendanceSummaryViewSetV2, basename="attendance-summary-v2")
router.register(r'attendance-excel', MonthlyAttendanceExcelViewSet, basename="attendance-excel")
router.register(r'attendance-excel-v2', MonthlyAttendanceExcelViewSetV2, basename="attendance-excel-v2")
router.register(r"attendance-weekoff", AttendanceWeekOffViewSet, basename="attendance-weekoff")
router.register(r"attendance-weekoff-v2", AttendanceWeekOffViewSetV2, basename="attendance-weekoff-v2")

v5_router = DefaultRouter()
v5_router.register(r'attendance', AttendanceCheckinViewSetV5, basename='attendance-v5')
v5_router.register(r'attendance-summary-v5', MonthlyAttendanceSummaryViewSetV5, basename="attendance-summary-v5")
v5_router.register(r'attendance-excel-v5', MonthlyAttendanceExcelViewSetV5, basename="attendance-excel-v5")

urlpatterns = [
    path('performance/', GuardPerformanceView.as_view()),
    path('stats/', TourStatsView.as_view()),
    path('export/csv/', ExportTourLogsCSV.as_view()),
    path('routes/<uuid:guard_id>/', PatrolRouteView.as_view()),
    path('dashboard-checkin-report/', DashboardCheckInReportView.as_view(), name='dashboard-checkin-report'),
    path('dashboard-checkin-report/v5/', DashboardCheckInReportViewV5.as_view(), name='dashboard-checkin-report-v5'),
    path('dashboard-checkin-report/v2/', DashboardCheckInReportViewV2.as_view(), name='dashboard-checkin-report-v2'),
    path("api/attendance/", AttendanceCheckinListView.as_view(), name="attendance-api"),
    path("api/attendance_v3/", AttendanceCheckinV3ListView.as_view(), name="attendance-api-v3"),
    path("api/attendance_v5/", AttendanceCheckinV4ListViewV5.as_view(), name="attendance-api-v5"),
    path("api/attendance_v4/", AttendanceCheckinV4ListView.as_view(), name="attendance-api-v4"),
    path('dashboard-checkin-report-excel/', DashboardCheckInReportExcelView.as_view(), name='dashboard-checkin-report-excel'),
    path('dashboard-checkin-report-excel/v5/', DashboardCheckInReportExcelViewV5.as_view(), name='dashboard-checkin-report-excel-v5'),
    path('dashboard-checkin-report-excel/v2/', DashboardCheckInReportExcelViewV2.as_view(), name='dashboard-checkin-report-excel-v2'),
    path('dashboard-checkin-report-pdf/v5/', DashboardCheckInReportPdfViewV5.as_view(), name='dashboard-checkin-report-pdf-v5'),
    path('dashboard-checkin-report-pdf/v2/', DashboardCheckInReportPdfViewV2.as_view(), name='dashboard-checkin-report-pdf-v2'),
    path("api/attendance/export/", AttendanceCheckinExportView.as_view(), name="attendance-export-api"),
    path("api/attendance/export_v3/", AttendanceCheckinV3ExportView.as_view(), name="attendance-export-api-v3"),
    path("api/attendance/export_v5/", AttendanceCheckinV4ExportViewV5.as_view(), name="attendance-export-api-v5"),
    path("api/attendance/export_v5_pdf/", AttendanceCheckinV4PdfExportViewV5.as_view(), name="attendance-export-api-v5-pdf"),
    path("api/attendance/export_v4/", AttendanceCheckinV4ExportView.as_view(), name="attendance-export-api-v4"),
    path("api/attendance/export_v4_pdf/", AttendanceCheckinV4PdfExportView.as_view(), name="attendance-export-api-v4-pdf"),
    path("attendance/shift_today_v5/", ShiftTodayV5.as_view(), name="shift_today_v5"),
    path("attendance/create_assignment_v5/", CreateAssignmentV5.as_view(), name="create_assignment_v5"),
    path('', include(v5_router.urls)),
    path('', include(router.urls)),
]




