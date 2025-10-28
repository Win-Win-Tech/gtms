from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import LocationViewSet, ShiftViewSet, AssignmentViewSet, CheckpointViewSet, SiteSettingViewSet, CheckpointTemplateViewSet

router = DefaultRouter()
router.register('locations', LocationViewSet)
router.register('shifts', ShiftViewSet)
router.register('assignments', AssignmentViewSet)
router.register('checkpoints', CheckpointViewSet)
router.register('site-settings', SiteSettingViewSet)
router.register('checkpoint-templates', CheckpointTemplateViewSet, basename='checkpointtemplate')

#router.register('checkpoint-templates/shift/<uuid:shift_id>/', CheckpointTemplateByShiftView, basename='checkpoint-templates-by-shift')

#path('checkpoint-templates/shift/<uuid:shift_id>/', CheckpointTemplateByShiftView.as_view(), name='checkpoint-templates-by-shift'),

urlpatterns = router.urls

