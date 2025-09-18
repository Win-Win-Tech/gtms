from django.urls import path
from .views import *

urlpatterns = [
    path('performance/', GuardPerformanceView.as_view()),
    path('stats/', TourStatsView.as_view()),
    path('export/csv/', ExportTourLogsCSV.as_view()),
    path('routes/<uuid:guard_id>/', PatrolRouteView.as_view()),
]
