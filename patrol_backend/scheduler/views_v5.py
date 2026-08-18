"""v5 assignment views — copies of live AssignmentViewSet with posted site. Live URLs unchanged."""

from datetime import datetime

from rest_framework import permissions, status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response

from authapp.site_access import assert_caller_can_access_site, get_site_or_error
from scheduler.daily_site import clear_daily_sites
from scheduler.models import Assignment, AssignmentDailySite
from scheduler.serializers_v5 import AssignmentSerializerV5
from scheduler.views import AssignmentViewSet
from patrol_backend.utils.timezone_utils import get_user_today, get_user_timezone_from_request


class AssignmentViewSetV5(AssignmentViewSet):
    serializer_class = AssignmentSerializerV5
    permission_classes = [permissions.IsAuthenticated]

    def _posted_on_date(self):
        raw = self.request.query_params.get("date")
        if raw:
            try:
                return datetime.strptime(raw, "%Y-%m-%d").date()
            except ValueError:
                raise ValidationError({"date": "Use YYYY-MM-DD."})
        tz = get_user_timezone_from_request(self.request)
        return get_user_today(tz)

    def _filter_by_posted_site(self, qs):
        site_id = self.request.query_params.get("site_id")
        if not site_id:
            return qs
        site = get_site_or_error(site_id)
        assert_caller_can_access_site(self.request.user, site)
        on_date = self._posted_on_date()
        guard_ids = AssignmentDailySite.objects.filter(
            site_id=site.id,
            date=on_date,
        ).values_list("guard_id", flat=True)
        return qs.filter(guard_id__in=guard_ids)

    def get_queryset(self):
        return self._filter_by_posted_site(
            super().get_queryset().prefetch_related("daily_sites__site")
        )

    def list(self, request, *args, **kwargs):
        try:
            return super().list(request, *args, **kwargs)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)

    def create(self, request, *args, **kwargs):
        try:
            return super().create(request, *args, **kwargs)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

    def update(self, request, *args, **kwargs):
        try:
            return super().update(request, *args, **kwargs)
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

    def destroy(self, request, *args, **kwargs):
        instance = self.get_object()
        clear_daily_sites(instance)
        return super().destroy(request, *args, **kwargs)

    @action(detail=False, methods=["get"], url_path="by-guard/(?P<guard_id>[^/.]+)")
    def by_guard(self, request, guard_id=None):
        try:
            try:
                limit = min(max(int(request.query_params.get("limit") or 25), 1), 250)
            except (TypeError, ValueError):
                limit = 25
            try:
                offset = max(int(request.query_params.get("offset") or 0), 0)
            except (TypeError, ValueError):
                offset = 0

            qs = Assignment.objects.filter(guard_id=guard_id, is_deleted=False).prefetch_related(
                "daily_sites__site"
            )
            qs = self._filter_by_posted_site(qs).order_by("-start_date", "-end_date", "-created_on")
            total = qs.count()
            page = qs[offset : offset + limit]
            serializer = self.get_serializer(page, many=True)
            return Response({
                "count": total,
                "limit": limit,
                "offset": offset,
                "results": serializer.data,
            })
        except ValidationError as e:
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)

    @action(detail=False, methods=["post"], url_path="bulk-create")
    def bulk_create(self, request):
        assignments_data = request.data
        if not isinstance(assignments_data, list):
            return Response({"error": "Expected a list of assignments"}, status=status.HTTP_400_BAD_REQUEST)

        created = []
        errors = []
        for idx, data in enumerate(assignments_data):
            serializer = self.get_serializer(data=data)
            if serializer.is_valid():
                try:
                    serializer.save()
                    created.append(serializer.data)
                except ValidationError as e:
                    errors.append({"index": idx, "errors": e.detail, "data": data})
                except PermissionDenied as e:
                    errors.append({"index": idx, "errors": {"site_id": [str(e)]}, "data": data})
            else:
                errors.append({"index": idx, "errors": serializer.errors, "data": data})

        if errors:
            return Response({"created": created, "errors": errors}, status=status.HTTP_207_MULTI_STATUS)
        return Response({"created": created}, status=status.HTTP_201_CREATED)
