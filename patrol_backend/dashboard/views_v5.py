"""v5 dashboard mobile helpers. Live attendance URLs unchanged."""

from rest_framework import permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from authapp.site_access import assert_caller_can_access_site, caller_can_access_site, get_site_or_error
from patrol_backend.utils.timezone_utils import get_user_today, get_user_timezone_from_request
from scheduler.daily_site import apply_select_site, site_ids_payload
from scheduler.models import Assignment


class ShiftTodayV5(APIView):
    """
    Copy of GET /dashboard/attendance/shift_today_v3/ plus posted site fields.
    Mobile: use instead of shift_today_v3 when site-wise is enabled.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        from dashboard.views import AttendanceCheckinViewSet

        view = AttendanceCheckinViewSet()
        view.request = request
        view.format_kwarg = None
        response = view.shift_today_v3(request)
        if response.status_code != status.HTTP_200_OK:
            return response

        user_tz = get_user_timezone_from_request(request)
        on_date = get_user_today(user_tz)
        data = dict(response.data)
        data.update(site_ids_payload(request.user, on_date))
        return Response(data, status=status.HTTP_200_OK)


class CreateAssignmentV5(APIView):
    """
    Copy of POST /dashboard/attendance/create_assignment/ plus optional site_id.
    Mobile self-assign: guard picks shift (+ template) and sends site_id.
    Site write uses attendance rules (daily vs cache), same as POST /auth/v5/my-sites/.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        site_id = request.data.get("site_id") or None
        guard_id = request.data.get("guard_id")
        site = None
        try:
            if site_id:
                site = get_site_or_error(site_id)
                assert_caller_can_access_site(request.user, site)
                if guard_id:
                    from authapp.models import User

                    guard = User.objects.filter(id=guard_id).first()
                    if guard and not caller_can_access_site(guard, site) and not guard.is_superuser:
                        raise ValidationError({"site_id": "This guard is not assigned to that site."})

            from dashboard.views import AttendanceCheckinViewSet

            view = AttendanceCheckinViewSet()
            view.request = request
            view.format_kwarg = None
            response = view.create_assignment(request)
            if response.status_code == 201 and site_id and response.data.get("assignment_id"):
                assignment = Assignment.objects.filter(id=response.data["assignment_id"]).select_related("guard").first()
                if assignment:
                    user_tz = get_user_timezone_from_request(request)
                    on_date = assignment.start_date or get_user_today(user_tz)
                    selected = apply_select_site(
                        assignment.guard,
                        site_id,
                        on_date,
                        assignment=assignment,
                        caller=request.user,
                    )
                    data = dict(response.data)
                    data["site_id"] = str(site_id)
                    data["site_name"] = site.name
                    data["assigned_site_id"] = str(site_id)
                    data["assigned_site_name"] = site.name
                    if selected:
                        data["last_selected_site_id"] = selected.get("id")
                        data["last_selected_site_name"] = selected.get("name")
                    return Response(data, status=status.HTTP_201_CREATED)
            return response
        except ValidationError as e:
            return Response({"error": e.detail}, status=status.HTTP_400_BAD_REQUEST)
        except PermissionDenied as e:
            return Response({"error": str(e)}, status=status.HTTP_403_FORBIDDEN)
