from rest_framework.routers import DefaultRouter
from .views import CheckInViewSet

router = DefaultRouter()
router.register('checkins', CheckInViewSet)

urlpatterns = router.urls
