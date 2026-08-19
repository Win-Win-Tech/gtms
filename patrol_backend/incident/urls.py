from django.urls import path
from .views import (
    IncidentReportView,
    IncidentAssignView,
    IncidentResolveView,
    IncidentFilterView,
    IncidentExportExcelView,
    IncidentExportPdfView,
    MyTicketsExportExcelView,
)
from .views_v5 import (
    IncidentReportViewV5,
    IncidentAssignViewV5,
    IncidentResolveViewV5,
    IncidentFilterViewV5,
    IncidentExportExcelViewV5,
    IncidentExportPdfViewV5,
    MyTicketsExportExcelViewV5,
)

urlpatterns = [
    path('report/', IncidentReportView.as_view(), name='incident-report'),
    path('assign/<str:ticket_number>/', IncidentAssignView.as_view(), name='incident-assign'),
    path('resolve/<str:ticket_number>/', IncidentResolveView.as_view(), name='incident-resolve'),
    path('dashboard/filter/', IncidentFilterView.as_view(), name='incident-filter'),
    path('dashboard/export-excel/', IncidentExportExcelView.as_view(), name='incident-export-excel'),
    path('dashboard/export-pdf/', IncidentExportPdfView.as_view(), name='incident-export-pdf'),
    path('mytickets/export-excel/', MyTicketsExportExcelView.as_view(), name='mytickets-export-excel'),

    path('v5/report/', IncidentReportViewV5.as_view(), name='incident-report-v5'),
    path('v5/assign/<str:ticket_number>/', IncidentAssignViewV5.as_view(), name='incident-assign-v5'),
    path('v5/resolve/<str:ticket_number>/', IncidentResolveViewV5.as_view(), name='incident-resolve-v5'),
    path('v5/dashboard/filter/', IncidentFilterViewV5.as_view(), name='incident-filter-v5'),
    path('v5/dashboard/export-excel/', IncidentExportExcelViewV5.as_view(), name='incident-export-excel-v5'),
    path('v5/dashboard/export-pdf/', IncidentExportPdfViewV5.as_view(), name='incident-export-pdf-v5'),
    path('v5/mytickets/export-excel/', MyTicketsExportExcelViewV5.as_view(), name='mytickets-export-excel-v5'),
]
