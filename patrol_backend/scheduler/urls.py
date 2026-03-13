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
)

router = DefaultRouter()
router.register('locations', LocationViewSet)
router.register('shifts', ShiftViewSet)
router.register('assignments', AssignmentViewSet)
router.register('checkpoints', CheckpointViewSet)
router.register('site-settings', SiteSettingViewSet)
router.register('checkpoint-templates', CheckpointTemplateViewSet, basename='checkpointtemplate')
router.register('checklist-items', ChecklistItemViewSet, basename='checklistitem')
router.register('checklist-templates', ChecklistTemplateViewSet, basename='checklisttemplate-master')

#router.register('checkpoint-templates/shift/<uuid:shift_id>/', CheckpointTemplateByShiftView, basename='checkpoint-templates-by-shift')

#path('checkpoint-templates/shift/<uuid:shift_id>/', CheckpointTemplateByShiftView.as_view(), name='checkpoint-templates-by-shift'),

urlpatterns = router.urls

