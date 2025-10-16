from django.urls import path
from .views import (
    IncidentReportView,
    IncidentAssignView,
    IncidentResolveView,
    IncidentFilterView,
)

urlpatterns = [
    # Endpoint for guards to report a new incident
    path('report/', IncidentReportView.as_view(), name='incident-report'),

    # Endpoint for assigning a ticket to a user
    path('assign/<str:ticket_number>/', IncidentAssignView.as_view(), name='incident-assign'),

    # Endpoint for resolving a ticket
    path('resolve/<str:ticket_number>/', IncidentResolveView.as_view(), name='incident-resolve'),
    path('dashboard/filter/', IncidentFilterView.as_view(), name='incident-filter'),
]
