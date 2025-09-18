from rest_framework.routers import DefaultRouter
from .views import TourLogViewSet, MissedCheckpointViewSet, IncidentReportViewSet

router = DefaultRouter()
router.register(r'logs', TourLogViewSet)
router.register(r'missed', MissedCheckpointViewSet)
router.register(r'incidents', IncidentReportViewSet)

urlpatterns = router.urls
