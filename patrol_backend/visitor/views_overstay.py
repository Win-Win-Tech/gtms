"""Vehicle overstay whitelist APIs."""

from django.db.models import Q
from django.utils import timezone
from rest_framework import status
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from scheduler.models import Location

from .anpr.gate import normalize_plate
from .models import SiteVehicleOverstayRecipient, VehicleOverstayWhitelist, VisitorEntry
from .serializers_overstay import (
    SiteVehicleOverstayRecipientSerializer,
    VehicleOverstayWhitelistSerializer,
)
from .utils import resolve_location_for_request


def _paginate(qs, request, *, default_page_size=25):
    try:
        page = max(1, int(request.query_params.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.query_params.get("page_size") or default_page_size)
    except (TypeError, ValueError):
        page_size = default_page_size
    page_size = max(1, min(page_size, 100))
    total = qs.count()
    start = (page - 1) * page_size
    end = start + page_size
    return qs[start:end], {
        "page": page,
        "page_size": page_size,
        "total": total,
        "total_pages": (total + page_size - 1) // page_size if page_size else 0,
    }


class VehicleOverstayWhitelistListCreateView(APIView):
    """
    GET  /visitors/overstay-whitelist/
    POST /visitors/overstay-whitelist/
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        location_id, err = resolve_location_for_request(
            request, request.query_params.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        search = (request.query_params.get("search") or "").strip()
        qs = VehicleOverstayWhitelist.objects.filter(
            location_id=location_id,
            is_deleted=False,
        ).select_related("created_by")
        if search:
            plate = normalize_plate(search) or search.upper()
            qs = qs.filter(vehicle_number__icontains=plate)

        page_qs, meta = _paginate(qs, request)
        data = VehicleOverstayWhitelistSerializer(
            page_qs, many=True, context={"request": request}
        ).data
        return Response({"results": data, **meta})

    def post(self, request):
        location_id, err = resolve_location_for_request(
            request, request.data.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            location = Location.objects.get(id=location_id, is_deleted=False)
        except Location.DoesNotExist:
            return Response(
                {"error": "Location not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        plate = normalize_plate(request.data.get("vehicle_number") or "")
        if len(plate) < 4:
            return Response(
                {"error": "vehicle_number is required (min 4 characters)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        notes = (request.data.get("notes") or "").strip()[:255]

        existing = VehicleOverstayWhitelist.objects.filter(
            location_id=location_id,
            vehicle_number=plate,
            is_deleted=False,
        ).first()
        if existing:
            return Response(
                {"error": f"{plate} is already whitelisted"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Revive soft-deleted row if same plate
        soft = VehicleOverstayWhitelist.objects.filter(
            location_id=location_id,
            vehicle_number=plate,
            is_deleted=True,
        ).first()
        if soft:
            soft.is_deleted = False
            soft.deleted_on = None
            soft.deleted_by = None
            soft.notes = notes
            soft.created_by = request.user
            soft.save(
                update_fields=[
                    "is_deleted",
                    "deleted_on",
                    "deleted_by",
                    "notes",
                    "created_by",
                    "modified_on",
                ]
            )
            row = soft
        else:
            row = VehicleOverstayWhitelist.objects.create(
                location=location,
                vehicle_number=plate,
                notes=notes,
                created_by=request.user,
            )

        return Response(
            VehicleOverstayWhitelistSerializer(row, context={"request": request}).data,
            status=status.HTTP_201_CREATED,
        )


class VehicleOverstayWhitelistDetailView(APIView):
    """DELETE /visitors/overstay-whitelist/<id>/"""

    permission_classes = [IsAuthenticated]

    def delete(self, request, whitelist_id):
        try:
            row = VehicleOverstayWhitelist.objects.get(id=whitelist_id, is_deleted=False)
        except VehicleOverstayWhitelist.DoesNotExist:
            return Response(
                {"error": "Whitelist entry not found"},
                status=status.HTTP_404_NOT_FOUND,
            )

        location_id, err = resolve_location_for_request(request, str(row.location_id))
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if str(row.location_id) != str(location_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        row.is_deleted = True
        row.deleted_on = timezone.now()
        row.deleted_by = request.user
        row.save(update_fields=["is_deleted", "deleted_on", "deleted_by", "modified_on"])
        return Response({"ok": True})


class VehicleOverstayVehicleSuggestView(APIView):
    """
    GET /visitors/overstay-whitelist/vehicle-suggestions/
    Distinct vehicle_number values from VisitorEntry for autocomplete.
    Query: location_id, site_id (from header), q (typed keys).
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        location_id, err = resolve_location_for_request(
            request, request.query_params.get("location_id")
        )
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if not location_id:
            return Response(
                {"error": "location_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        raw_site = (request.query_params.get("site_id") or "").strip()
        site_id = None
        if raw_site and raw_site.lower() not in ("all", "null", "undefined"):
            site_id = raw_site
            try:
                from authapp.site_access import assert_caller_can_access_site, get_site_or_error

                site = get_site_or_error(site_id)
                assert_caller_can_access_site(request.user, site)
                if str(site.location_id) != str(location_id):
                    return Response(
                        {"error": "Site does not belong to that organisation"},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
            except Exception as exc:
                return Response(
                    {"error": str(exc)},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        q = (request.query_params.get("q") or "").strip()
        plate_q = normalize_plate(q) or q.upper()
        if len(plate_q) < 1:
            return Response({"results": []})

        try:
            limit = min(30, max(1, int(request.query_params.get("limit") or 15)))
        except (TypeError, ValueError):
            limit = 15

        qs = (
            VisitorEntry.objects.filter(
                location_id=location_id,
                is_deleted=False,
            )
            .exclude(vehicle_number="")
            .exclude(vehicle_number__isnull=True)
        )
        if site_id:
            qs = qs.filter(site_id=site_id)

        qs = (
            qs.filter(vehicle_number__icontains=plate_q)
            .values_list("vehicle_number", flat=True)
            .distinct()
            .order_by("vehicle_number")[:limit]
        )
        # Normalize display; keep distinct after normalize
        seen = set()
        results = []
        for raw in qs:
            plate = normalize_plate(raw) or (raw or "").strip().upper()
            if not plate or plate in seen:
                continue
            seen.add(plate)
            results.append(plate)
        return Response({"results": results})


class SiteVehicleOverstayRecipientView(APIView):
    """
    GET  /visitors/overstay-alert-recipients/?site_id=
    PUT  /visitors/overstay-alert-recipients/  { site_id, role_ids: [...] }
    Replaces the full role list for that site.
    """

    permission_classes = [IsAuthenticated]

    def get(self, request):
        from authapp.site_access import assert_caller_can_access_site, get_site_or_error

        site_id = (request.query_params.get("site_id") or "").strip()
        if not site_id:
            return Response(
                {"error": "site_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            site = get_site_or_error(site_id)
            assert_caller_can_access_site(request.user, site)
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        location_id, err = resolve_location_for_request(request, str(site.location_id))
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if str(site.location_id) != str(location_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        rows = (
            SiteVehicleOverstayRecipient.objects.filter(site=site)
            .select_related("site", "recipient_role")
            .order_by("recipient_role__name")
        )
        data = SiteVehicleOverstayRecipientSerializer(rows, many=True).data
        return Response(
            {
                "site_id": str(site.id),
                "site_name": site.name,
                "role_ids": [str(r.recipient_role_id) for r in rows],
                "results": data,
            }
        )

    def put(self, request):
        from authapp.models import Role
        from authapp.site_access import assert_caller_can_access_site, get_site_or_error
        from django.db import transaction

        site_id = request.data.get("site_id")
        if not site_id:
            return Response(
                {"error": "site_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            site = get_site_or_error(site_id)
            assert_caller_can_access_site(request.user, site)
        except Exception as exc:
            return Response({"error": str(exc)}, status=status.HTTP_400_BAD_REQUEST)

        location_id, err = resolve_location_for_request(request, str(site.location_id))
        if err:
            return Response({"error": err}, status=status.HTTP_400_BAD_REQUEST)
        if str(site.location_id) != str(location_id):
            return Response({"error": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        raw_ids = request.data.get("role_ids")
        if raw_ids is None:
            return Response(
                {"error": "role_ids is required (array)"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not isinstance(raw_ids, list):
            return Response(
                {"error": "role_ids must be an array"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        role_ids = []
        seen = set()
        for rid in raw_ids:
            key = str(rid).strip()
            if not key or key in seen:
                continue
            seen.add(key)
            role_ids.append(key)

        roles = list(
            Role.objects.filter(id__in=role_ids).filter(
                Q(location_id=site.location_id) | Q(location__isnull=True)
            )
        )
        found = {str(r.id) for r in roles}
        missing = [rid for rid in role_ids if rid not in found]
        if missing:
            return Response(
                {"error": f"Unknown role(s) for this organisation: {', '.join(missing)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        with transaction.atomic():
            SiteVehicleOverstayRecipient.objects.filter(site=site).exclude(
                recipient_role_id__in=[r.id for r in roles]
            ).delete()
            existing = set(
                SiteVehicleOverstayRecipient.objects.filter(site=site).values_list(
                    "recipient_role_id", flat=True
                )
            )
            to_create = [
                SiteVehicleOverstayRecipient(site=site, recipient_role=r)
                for r in roles
                if r.id not in existing
            ]
            if to_create:
                SiteVehicleOverstayRecipient.objects.bulk_create(to_create)

        rows = (
            SiteVehicleOverstayRecipient.objects.filter(site=site)
            .select_related("site", "recipient_role")
            .order_by("recipient_role__name")
        )
        data = SiteVehicleOverstayRecipientSerializer(rows, many=True).data
        return Response(
            {
                "site_id": str(site.id),
                "site_name": site.name,
                "role_ids": [str(r.recipient_role_id) for r in rows],
                "results": data,
            }
        )
