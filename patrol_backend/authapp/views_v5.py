"""v5 user APIs — copies of live user views with site assignment. Live URLs unchanged."""

from django.db.models import Q
from rest_framework import filters, permissions, status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.response import Response
from rest_framework.views import APIView

from patrol_backend.utils.response import api_response

from .models import User
from .serializers_v5 import UserListSerializerV5, UserSerializerV5
from .site_access import (
    assert_caller_can_access_site,
    get_site_or_error,
    header_sites_payload,
    is_org_admin,
    users_queryset_for_site,
)
from .views import UserCreateView, UserDetailView, UserListView, UserUpdateView


class UserCreateViewV5(UserCreateView):
    serializer_class = UserSerializerV5

    def create(self, request, *args, **kwargs):
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            return Response(
                api_response("success", "User created successfully", serializer.data, status.HTTP_201_CREATED)
            )
        except ValidationError as e:
            return Response(
                api_response("error", "Validation failed", e.detail, status.HTTP_400_BAD_REQUEST),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserDetailViewV5(UserDetailView):
    serializer_class = UserSerializerV5


class UserUpdateViewV5(UserUpdateView):
    serializer_class = UserSerializerV5

    def update(self, request, *args, **kwargs):
        try:
            partial = kwargs.pop("partial", False)
            instance = self.get_object()
            serializer = self.get_serializer(instance, data=request.data, partial=partial)
            serializer.is_valid(raise_exception=True)
            self.perform_update(serializer)
            return Response(
                api_response("success", "User updated successfully", serializer.data, status.HTTP_200_OK)
            )
        except User.DoesNotExist:
            return Response(
                api_response("error", "User not found", None, status.HTTP_404_NOT_FOUND),
                status=status.HTTP_404_NOT_FOUND,
            )
        except ValidationError as e:
            return Response(
                api_response("error", "Validation failed", e.detail, status.HTTP_400_BAD_REQUEST),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except Exception as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class UserListViewV5(UserListView):
    serializer_class = UserListSerializerV5
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [filters.SearchFilter]
    search_fields = ["name", "email", "employee_code"]

    def get_queryset(self):
        queryset = super().get_queryset().prefetch_related("user_sites__site")
        request = self.request
        caller = request.user
        site_id = request.query_params.get("site_id")

        if not caller.is_superuser and is_org_admin(caller) and caller.location_id:
            queryset = queryset.filter(location_id=caller.location_id)
        elif not caller.is_superuser and not is_org_admin(caller):
            if caller.location_id:
                queryset = queryset.filter(location_id=caller.location_id)

        if site_id:
            site = get_site_or_error(site_id)
            assert_caller_can_access_site(caller, site)
            if not caller.is_superuser and caller.location_id:
                if str(site.location_id) != str(caller.location_id):
                    raise PermissionDenied("Site does not belong to your organisation.")
            queryset = queryset.filter(
                Q(id__in=users_queryset_for_site(site).values("id"))
            )
        elif not caller.is_superuser and not is_org_admin(caller):
            from .site_access import allowed_site_ids

            allowed = allowed_site_ids(caller)
            queryset = queryset.filter(
                Q(user_sites__site_id__in=allowed)
                | Q(all_org_sites=True, location_id=caller.location_id)
            ).distinct()

        return queryset

    def list(self, request, *args, **kwargs):
        try:
            return super().list(request, *args, **kwargs)
        except ValidationError as e:
            return Response(
                api_response("error", "Validation failed", e.detail, status.HTTP_400_BAD_REQUEST),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PermissionDenied as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_403_FORBIDDEN),
                status=status.HTTP_403_FORBIDDEN,
            )


class UserByRoleViewV5(APIView):
    """Copy of UserByRoleView with site_id filter. Live /users/by-role/ unchanged."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        roles = request.GET.getlist("roles")
        location_id = request.GET.get("location_id", None)
        site_id = request.GET.get("site_id", None)

        if not roles:
            return Response(
                api_response("error", "No roles provided.", None, status.HTTP_400_BAD_REQUEST),
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            users = User.get_by_roles(roles)
            caller = request.user

            if location_id:
                users = users.filter(location_id=location_id)

            if not caller.is_superuser and is_org_admin(caller) and caller.location_id:
                users = users.filter(location_id=caller.location_id)
            elif not caller.is_superuser and not is_org_admin(caller) and caller.location_id:
                users = users.filter(location_id=caller.location_id)

            if site_id:
                site = get_site_or_error(site_id)
                assert_caller_can_access_site(caller, site)
                users = users.filter(id__in=users_queryset_for_site(site).values("id"))
            elif not caller.is_superuser and not is_org_admin(caller):
                from .site_access import allowed_site_ids

                allowed = allowed_site_ids(caller)
                users = users.filter(
                    Q(user_sites__site_id__in=allowed)
                    | Q(all_org_sites=True, location_id=caller.location_id)
                ).distinct()

            users = users.prefetch_related("user_sites")
            data = [
                {
                    "id": str(user.id),
                    "email": user.email,
                    "name": user.name,
                    "role": user.role,
                    "phone_no": user.phone_no,
                    "employee_code": user.employee_code,
                    "all_org_sites": bool(user.all_org_sites),
                    "site_ids": [str(s) for s in user.user_sites.values_list("site_id", flat=True)]
                    if not user.all_org_sites
                    else [],
                }
                for user in users
            ]
            return Response(api_response("success", "Users fetched", data, status.HTTP_200_OK))
        except ValidationError as e:
            return Response(
                api_response("error", "Validation failed", e.detail, status.HTTP_400_BAD_REQUEST),
                status=status.HTTP_400_BAD_REQUEST,
            )
        except PermissionDenied as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_403_FORBIDDEN),
                status=status.HTTP_403_FORBIDDEN,
            )
        except Exception as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


class MySitesViewV5(APIView):
    """Assigned sites for the header dropdown. POST (select/switch) comes with shift-assign tables."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        caller = request.user
        location_id = request.query_params.get("location_id") or None
        try:
            if location_id and not caller.is_superuser:
                if not caller.location_id or str(caller.location_id) != str(location_id):
                    raise PermissionDenied("You can only list sites for your organisation.")
            assigned = header_sites_payload(caller, location_id=location_id)
            data = {
                "assigned_sites": assigned,
                "all_org_sites": bool(
                    caller.is_superuser
                    or is_org_admin(caller)
                    or getattr(caller, "all_org_sites", False)
                ),
                "current_site": None,
            }
            return Response(api_response("success", "Sites fetched", data, status.HTTP_200_OK))
        except PermissionDenied as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_403_FORBIDDEN),
                status=status.HTTP_403_FORBIDDEN,
            )
        except Exception as e:
            return Response(
                api_response("error", str(e), None, status.HTTP_500_INTERNAL_SERVER_ERROR),
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
