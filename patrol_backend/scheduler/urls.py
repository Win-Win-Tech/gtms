from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    LocationViewSet,
    ShiftViewSet,
    AssignmentViewSet,
    CheckpointViewSet,
    SiteSettingViewSet,
    CheckpointTemplateViewSet,
    ChecklistItemViewSet,
    ChecklistTemplateViewSet,
    AppVersionCheckView,
)
from .views_v5 import AssignmentViewSetV5

router = DefaultRouter()
router.register('locations', LocationViewSet)
router.register('shifts', ShiftViewSet)
router.register('assignments', AssignmentViewSet)
router.register('checkpoints', CheckpointViewSet)
router.register('site-settings', SiteSettingViewSet)
router.register('checkpoint-templates', CheckpointTemplateViewSet, basename='checkpointtemplate')
router.register('checklist-items', ChecklistItemViewSet, basename='checklistitem')
router.register('checklist-templates', ChecklistTemplateViewSet, basename='checklisttemplate-master')

v5_router = DefaultRouter()
v5_router.register('assignments', AssignmentViewSetV5, basename='assignments-v5')

urlpatterns = router.urls + [
    path('app-version-check/', AppVersionCheckView.as_view(), name='app-version-check'),
    path('v5/', include(v5_router.urls)),
]
