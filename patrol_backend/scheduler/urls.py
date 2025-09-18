from rest_framework.routers import DefaultRouter
from .views import LocationViewSet, ShiftViewSet, AssignmentViewSet, CheckpointViewSet

router = DefaultRouter()
router.register('locations', LocationViewSet)
router.register('shifts', ShiftViewSet)
router.register('assignments', AssignmentViewSet)
router.register('checkpoints', CheckpointViewSet)

urlpatterns = router.urls

