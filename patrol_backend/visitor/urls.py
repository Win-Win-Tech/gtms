from django.urls import path

from .views import (
    VisitorCheckInView,
    VisitorCheckOutView,
    VisitorEntryExportView,
    VisitorEntryListView,
    VisitorSearchView,
)

urlpatterns = [
    path("search/", VisitorSearchView.as_view(), name="visitor-search"),
    path("entries/", VisitorEntryListView.as_view(), name="visitor-entries-list"),
    path("entries/export/", VisitorEntryExportView.as_view(), name="visitor-entries-export"),
    path("entries/checkin/", VisitorCheckInView.as_view(), name="visitor-entries-checkin"),
    path(
        "entries/<uuid:entry_id>/checkout/",
        VisitorCheckOutView.as_view(),
        name="visitor-entries-checkout",
    ),
]
