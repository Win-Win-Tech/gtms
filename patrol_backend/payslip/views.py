from django.db import transaction
from django.db.models import Exists, OuterRef, Q, Subquery
from django.http import FileResponse
from django.utils import timezone
from django.core.files.base import ContentFile
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from authapp.models import User
from patrol_backend.utils.timezone_utils import get_user_timezone_from_request
from .models import (
    EmployeePayrollProfile,
    PayslipTemplate,
    PayslipFieldConfig,
    PayslipField,
    PayslipRecord,
)
from .serializers import (
    EmployeePayrollProfileSerializer,
    PayslipTemplateSerializer,
    PayslipFieldConfigSerializer,
    PayslipFieldSerializer,
    PayslipRecordSerializer,
    PayslipGenerateSerializer,
    PayslipBulkGenerateSerializer,
    PayslipMarkPaidSerializer,
    PayslipApproveSerializer,
    PayslipReopenSerializer,
    PayslipRecordAttendanceEditSerializer,
)
from .services import calculate_attendance_from_master, calculate_salary_fields
from .pdf_utils import build_simple_payslip_pdf_bytes
from decimal import Decimal


def _is_admin_like(user):
    allowed_roles = {"admin", "so", "fo"}
    return bool(getattr(user, "is_superuser", False) or getattr(user, "role", None) in allowed_roles)


class AdminOnlyMixin:
    permission_classes = [IsAuthenticated]

    def initial(self, request, *args, **kwargs):
        super().initial(request, *args, **kwargs)
        if not _is_admin_like(request.user):
            self.permission_denied(request, message="Only admin/SO/FO can access payslip APIs")


class EmployeePayrollProfileViewSet(AdminOnlyMixin, viewsets.ModelViewSet):
    queryset = EmployeePayrollProfile.objects.select_related("user", "location").all()
    serializer_class = EmployeePayrollProfileSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    @action(detail=False, methods=["get"], url_path="employees")
    def employees(self, request):
        """
        Return location-scoped employee list with payroll-profile presence.
        Roles included by default: All non-admin roles (guard, so, fo, etc.)
        """
        requested_location_id = request.query_params.get("location_id")
        search = (request.query_params.get("search") or "").strip()
        roles_param = request.query_params.get("roles")
        if getattr(request.user, "is_superuser", False):
            effective_location_id = requested_location_id
        else:
            effective_location_id = str(getattr(request.user, "location_id", "") or "")
            if not effective_location_id:
                return Response({"error": "User has no assigned location"}, status=status.HTTP_400_BAD_REQUEST)

        roles_param = request.query_params.get("roles")
        role_param = request.query_params.get("role")
        search = (request.query_params.get("search") or "").strip()

        # Start with base queryset
        users_qs = User.objects.filter(is_active=True, is_deleted=False).select_related("location")

        # Admin restriction: Only superusers see Admins in employee lists
        if not request.user.is_superuser:
            users_qs = users_qs.exclude(role__iexact='admin')

        if roles_param:
            requested_roles = [r.strip().lower() for r in roles_param.split(",") if r.strip()]
            users_qs = users_qs.filter(role__in=requested_roles)
        elif role_param and role_param.lower() != 'all':
            users_qs = users_qs.filter(role__iexact=role_param)
        else:
            # Re-confirm: Default hide admin if not already excluded
            if not request.user.is_superuser:
                users_qs = users_qs.exclude(role__iexact='admin')

        if effective_location_id:
            users_qs = users_qs.filter(location_id=effective_location_id)

        if search:
            users_qs = users_qs.filter(
                Q(name__icontains=search) |
                Q(email__icontains=search) |
                Q(phone_no__icontains=search) |
                Q(employee_code__icontains=search)
            )

        active_profile_subquery = EmployeePayrollProfile.objects.filter(
            user_id=OuterRef("id"),
            is_active=True,
        )
        users_qs = users_qs.annotate(
            has_payroll_profile=Exists(active_profile_subquery),
            payroll_profile_id=Subquery(active_profile_subquery.values("id")[:1]),
        ).order_by("name", "email")

        profile_map = {
            str(p.user_id): p
            for p in EmployeePayrollProfile.objects.filter(
                user_id__in=[u.id for u in users_qs],
                is_active=True,
            ).select_related("default_field_config", "default_template")
        }

        result = [
            {
                "id": str(u.id),
                "name": u.name,
                "email": u.email,
                "phone_no": u.phone_no,
                "employee_code": u.employee_code,
                "role": u.role,
                "location_id": str(u.location_id) if u.location_id else None,
                "location_name": getattr(u.location, "name", None),
                "has_payroll_profile": bool(getattr(u, "has_payroll_profile", False)),
                "payroll_profile_id": str(u.payroll_profile_id) if getattr(u, "payroll_profile_id", None) else None,
                "default_field_config_id": (
                    str(profile_map[str(u.id)].default_field_config_id)
                    if profile_map.get(str(u.id)) and profile_map[str(u.id)].default_field_config_id
                    else None
                ),
                "default_template_id": (
                    str(profile_map[str(u.id)].default_template_id)
                    if profile_map.get(str(u.id)) and profile_map[str(u.id)].default_template_id
                    else None
                ),
            }
            for u in users_qs
        ]
        return Response(result, status=status.HTTP_200_OK)

    @action(detail=False, methods=["get"], url_path="bulk-ready")
    def bulk_ready(self, request):
        """
        Check whether selected location has required payroll setup for bulk generation:
        - template exists
        - active field config exists
        - at least one visible non-deleted field in active config
        """
        requested_location_id = request.query_params.get("location_id")
        if getattr(request.user, "is_superuser", False):
            effective_location_id = requested_location_id
        else:
            effective_location_id = str(getattr(request.user, "location_id", "") or "")

        if not effective_location_id:
            return Response(
                {
                    "ready": False,
                    "location_id": None,
                    "has_template": False,
                    "template_id": None,
                    "has_active_field_config": False,
                    "active_field_config_id": None,
                    "has_visible_fields": False,
                    "message": "Location is required",
                },
                status=status.HTTP_200_OK,
            )

        template_exists = PayslipTemplate.objects.filter(
            location_id=effective_location_id,
            is_deleted=False,
        ).exists()
        active_config = PayslipFieldConfig.objects.filter(
            location_id=effective_location_id,
            is_deleted=False,
            is_active=True,
        ).order_by("config_name").first()
        visible_fields_exists = False
        if active_config:
            visible_fields_exists = PayslipField.objects.filter(
                field_config=active_config,
                is_deleted=False,
                is_visible=True,
            ).exists()

        ready = bool(template_exists and active_config and visible_fields_exists)
        msg = (
            "Ready for bulk payslip generation"
            if ready
            else "Template/active field config/visible fields missing for this location"
        )
        return Response(
            {
                "ready": ready,
                "location_id": effective_location_id,
                "has_template": template_exists,
                "template_id": str(
                    PayslipTemplate.objects.filter(
                        location_id=effective_location_id,
                        is_deleted=False,
                    ).values_list("id", flat=True).first()
                ) if template_exists else None,
                "has_active_field_config": bool(active_config),
                "active_field_config_id": str(active_config.id) if active_config else None,
                "has_visible_fields": bool(visible_fields_exists),
                "message": msg,
            },
            status=status.HTTP_200_OK,
        )


class PayslipTemplateViewSet(AdminOnlyMixin, viewsets.ModelViewSet):
    queryset = PayslipTemplate.objects.select_related("location").filter(is_deleted=False)
    serializer_class = PayslipTemplateSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        location_id = self.request.query_params.get("location_id")
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.modified_by = self.request.user
        instance.save(update_fields=["is_deleted", "modified_by", "modified_on"])


class PayslipFieldConfigViewSet(AdminOnlyMixin, viewsets.ModelViewSet):
    queryset = PayslipFieldConfig.objects.select_related("location").filter(is_deleted=False)
    serializer_class = PayslipFieldConfigSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        location_id = self.request.query_params.get("location_id")
        if location_id:
            qs = qs.filter(location_id=location_id)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user, modified_by=self.request.user)

    def perform_update(self, serializer):
        serializer.save(modified_by=self.request.user)

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.modified_by = self.request.user
        instance.save(update_fields=["is_deleted", "modified_by", "modified_on"])

    @action(detail=False, methods=["post"], url_path="create-default")
    def create_default(self, request):
        """
        Create a basic default config for a location if none exists.
        """
        location_id = request.data.get("location_id")
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        existing = PayslipFieldConfig.objects.filter(
            location_id=location_id,
            is_deleted=False,
        ).first()
        if existing:
            return Response({"error": "Field config already exists for this location"}, status=status.HTTP_400_BAD_REQUEST)

        with transaction.atomic():
            PayslipTemplate.objects.get_or_create(
                location_id=location_id,
                defaults={
                    "company_name": "GTMS",
                    "header_text": "Payslip",
                    "created_by": request.user,
                    "modified_by": request.user,
                    "is_deleted": False,
                },
            )
            config = PayslipFieldConfig.objects.create(
                location_id=location_id,
                config_name="Default Salary Config",
                description="Default salary setup",
                is_active=True,
                created_by=request.user,
            )
            defaults = [
                ("Basic Salary", "BASIC", "EARNING", "PERCENTAGE", "60", 1),
                ("House Rent Allowance", "HRA", "EARNING", "PERCENTAGE", "20", 2),
                ("Conveyance", "CONVEYANCE", "EARNING", "PERCENTAGE", "10", 3),
                ("Medical", "MEDICAL", "EARNING", "PERCENTAGE", "10", 4),
                ("Provident Fund", "PF", "DEDUCTION", "FORMULA", "BASIC * Decimal('0.12')", 5),
                ("Professional Tax", "PT", "DEDUCTION", "FIXED", "200", 6),
                ("Absent Deduction", "ABSENT_DEDUCTION", "DEDUCTION", "FORMULA", "(gross_salary / month_days) * absent_days", 7),
                ("Month Days", "MONTH_DAYS", "INFO", "FORMULA", "month_days", 8),
                ("Working Days", "WORKING_DAYS", "INFO", "FORMULA", "working_days", 9),
                ("Present Days", "PRESENT_DAYS", "INFO", "FORMULA", "present_days", 10),
                ("Half Days", "HALF_DAYS", "INFO", "FORMULA", "half_days", 11),
                ("Absent Days", "ABSENT_DAYS", "INFO", "FORMULA", "absent_days", 12),
                ("Paid Days", "PAID_DAYS", "INFO", "FORMULA", "paid_days", 13),
            ]
            for field_name, field_code, field_type, value_type, value, display_order in defaults:
                PayslipField.objects.create(
                    field_config=config,
                    field_name=field_name,
                    field_code=field_code,
                    field_type=field_type,
                    value_type=value_type,
                    value=value,
                    display_order=display_order,
                    is_visible=True,
                )

        return Response(PayslipFieldConfigSerializer(config).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="bootstrap-default")
    def bootstrap_default(self, request):
        """
        Ensure payslip setup for a location:
        1) default template
        2) default field-config
        3) default fields if missing
        """
        location_id = request.data.get("location_id")
        if not location_id:
            return Response({"error": "location_id is required"}, status=status.HTTP_400_BAD_REQUEST)

        template, template_created = PayslipTemplate.objects.get_or_create(
            location_id=location_id,
            defaults={
                "company_name": "GTMS",
                "header_text": "Payslip",
                "created_by": request.user,
                "modified_by": request.user,
                "is_deleted": False,
            },
        )
        if template.is_deleted:
            template.is_deleted = False
            template.modified_by = request.user
            template.save(update_fields=["is_deleted", "modified_by", "modified_on"])
            template_created = True

        config = PayslipFieldConfig.objects.filter(
            location_id=location_id,
            is_deleted=False,
            config_name="Default Salary Config",
        ).first()
        config_created = False
        if not config:
            config = PayslipFieldConfig.objects.create(
                location_id=location_id,
                config_name="Default Salary Config",
                description="Default salary setup",
                is_active=True,
                created_by=request.user,
                modified_by=request.user,
            )
            config_created = True

        default_fields = [
            ("Basic Salary", "BASIC", "EARNING", "PERCENTAGE", "60", 1),
            ("House Rent Allowance", "HRA", "EARNING", "PERCENTAGE", "20", 2),
            ("Conveyance", "CONVEYANCE", "EARNING", "PERCENTAGE", "10", 3),
            ("Medical", "MEDICAL", "EARNING", "PERCENTAGE", "10", 4),
            ("Provident Fund", "PF", "DEDUCTION", "FORMULA", "BASIC * Decimal('0.12')", 5),
            ("Professional Tax", "PT", "DEDUCTION", "FIXED", "200", 6),
            ("Absent Deduction", "ABSENT_DEDUCTION", "DEDUCTION", "FORMULA", "(gross_salary / month_days) * absent_days", 7),
            ("Month Days", "MONTH_DAYS", "INFO", "FORMULA", "month_days", 8),
            ("Working Days", "WORKING_DAYS", "INFO", "FORMULA", "working_days", 9),
            ("Present Days", "PRESENT_DAYS", "INFO", "FORMULA", "present_days", 10),
            ("Half Days", "HALF_DAYS", "INFO", "FORMULA", "half_days", 11),
            ("Absent Days", "ABSENT_DAYS", "INFO", "FORMULA", "absent_days", 12),
            ("Paid Days", "PAID_DAYS", "INFO", "FORMULA", "paid_days", 13),
        ]
        created_field_codes = []
        for field_name, field_code, field_type, value_type, value, display_order in default_fields:
            field_obj, was_created = PayslipField.objects.get_or_create(
                field_config=config,
                field_code=field_code,
                defaults={
                    "field_name": field_name,
                    "field_type": field_type,
                    "value_type": value_type,
                    "value": value,
                    "display_order": display_order,
                    "is_visible": True,
                    "is_deleted": False,
                },
            )
            # If default row exists but was soft-deleted, restore it.
            if not was_created and field_obj.is_deleted:
                field_obj.is_deleted = False
                field_obj.field_name = field_name
                field_obj.field_type = field_type
                field_obj.value_type = value_type
                field_obj.value = value
                field_obj.display_order = display_order
                field_obj.is_visible = True
                field_obj.save(
                    update_fields=[
                        "is_deleted",
                        "field_name",
                        "field_type",
                        "value_type",
                        "value",
                        "display_order",
                        "is_visible",
                        "modified_on",
                    ]
                )
                created_field_codes.append(field_code)
            if was_created:
                created_field_codes.append(field_code)

        visible_fields_count = PayslipField.objects.filter(
            field_config=config,
            is_deleted=False,
            is_visible=True,
        ).count()

        return Response(
            {
                "message": "Bootstrap completed",
                "location_id": location_id,
                "template_created": template_created,
                "field_config_created": config_created,
                "created_fields": created_field_codes,
                "template_id": str(template.id),
                "field_config_id": str(config.id),
                "visible_fields_count": visible_fields_count,
            },
            status=status.HTTP_200_OK,
        )


class PayslipFieldViewSet(AdminOnlyMixin, viewsets.ModelViewSet):
    queryset = PayslipField.objects.select_related("field_config").filter(is_deleted=False)
    serializer_class = PayslipFieldSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        field_config_id = self.request.query_params.get("field_config_id")
        if field_config_id:
            qs = qs.filter(field_config_id=field_config_id)
        return qs.order_by("display_order", "field_name")

    def perform_destroy(self, instance):
        instance.is_deleted = True
        instance.save(update_fields=["is_deleted", "modified_on"])

    @action(detail=False, methods=["post"], url_path="bulk-upsert")
    def bulk_upsert(self, request):
        """
        Bulk create/update fields for one field config.
        Accepts: { field_config_id, fields: [{id?, field_name, field_code, field_type, value_type, value, display_order, is_visible}] }
        """
        field_config_id = request.data.get("field_config_id")
        fields = request.data.get("fields")
        if not field_config_id:
            return Response({"error": "field_config_id is required"}, status=status.HTTP_400_BAD_REQUEST)
        if not isinstance(fields, list) or len(fields) == 0:
            return Response({"error": "fields must be a non-empty list"}, status=status.HTTP_400_BAD_REQUEST)

        try:
            field_config = PayslipFieldConfig.objects.get(id=field_config_id, is_deleted=False)
        except PayslipFieldConfig.DoesNotExist:
            return Response({"error": "Field config not found"}, status=status.HTTP_404_NOT_FOUND)

        upserted = []
        failed = []
        with transaction.atomic():
            for idx, raw in enumerate(fields):
                row = dict(raw or {})
                row["field_config"] = field_config.id
                field_id = row.get("id")
                try:
                    if field_id:
                        instance = PayslipField.objects.filter(id=field_id, field_config=field_config, is_deleted=False).first()
                        if not instance:
                            failed.append({"index": idx, "field_name": row.get("field_name"), "reason": "Field not found"})
                            continue
                        serializer = PayslipFieldSerializer(instance, data=row, partial=True)
                    else:
                        serializer = PayslipFieldSerializer(data=row)

                    if serializer.is_valid():
                        obj = serializer.save()
                        upserted.append(PayslipFieldSerializer(obj).data)
                    else:
                        failed.append(
                            {
                                "index": idx,
                                "field_name": row.get("field_name"),
                                "reason": "; ".join(
                                    [f"{k}: {', '.join([str(vv) for vv in v]) if isinstance(v, list) else v}" for k, v in serializer.errors.items()]
                                ),
                            }
                        )
                except Exception as exc:
                    failed.append({"index": idx, "field_name": row.get("field_name"), "reason": str(exc)})

        return Response(
            {
                "message": f"{len(upserted)} upserted, {len(failed)} failed",
                "upserted_count": len(upserted),
                "failed_count": len(failed),
                "upserted": upserted,
                "failed": failed,
            },
            status=status.HTTP_200_OK,
        )


class PayslipRecordViewSet(AdminOnlyMixin, viewsets.ModelViewSet):
    queryset = PayslipRecord.objects.select_related("user", "location", "payroll_profile", "template", "field_config").all()
    serializer_class = PayslipRecordSerializer

    def get_queryset(self):
        qs = super().get_queryset()
        month = self.request.query_params.get("month")
        location_id = self.request.query_params.get("location_id")
        user_id = self.request.query_params.get("user_id")
        status_q = self.request.query_params.get("status")
        if month:
            qs = qs.filter(month=month)
        if location_id:
            qs = qs.filter(location_id=location_id)
        if user_id:
            qs = qs.filter(user_id=user_id)
        if status_q:
            qs = qs.filter(status=status_q)
        
        # New advanced filters
        role = self.request.query_params.get("role")
        search = self.request.query_params.get("search")
        
        if role and role.lower() != 'all':
            qs = qs.filter(user__role__iexact=role)
        
        if search:
            qs = qs.filter(
                Q(user__name__icontains=search) | 
                Q(user__employee_code__icontains=search)
            )
            
        return qs

    def _resolve_template(self, location_id, template_id):
        if template_id:
            return PayslipTemplate.objects.filter(
                id=template_id,
                location_id=location_id,
                is_deleted=False,
            ).first()
        return PayslipTemplate.objects.filter(location_id=location_id, is_deleted=False).first()

    def _resolve_field_config(self, location_id, field_config_id):
        if field_config_id:
            return PayslipFieldConfig.objects.filter(
                id=field_config_id,
                location_id=location_id,
                is_deleted=False,
            ).first()
        return PayslipFieldConfig.objects.filter(location_id=location_id, is_deleted=False, is_active=True).order_by("config_name").first()

    # Enforce workflow APIs instead of generic CRUD mutations for records.
    def create(self, request, *args, **kwargs):
        return Response({"error": "Use /records/generate/ to create payslip records"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def update(self, request, *args, **kwargs):
        return Response({"error": "Direct update is not allowed; use workflow actions"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        return Response({"error": "Direct patch is not allowed; use workflow actions"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def destroy(self, request, *args, **kwargs):
        return Response({"error": "Direct delete is not allowed for payslip records"}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def _can_edit_record(self, record):
        return record.status in ("DRAFT", "APPROVED")

    def _save_pdf_snapshot(self, record):
        """
        Save/replace record PDF in media storage and persist path in record.pdf_file.
        """
        pdf_bytes = build_simple_payslip_pdf_bytes(record)
        filename = f"payslip_{record.user_id}_{record.month}.pdf"

        # Delete old file when regenerating
        if record.pdf_file and getattr(record.pdf_file, "name", None):
            record.pdf_file.delete(save=False)

        record.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)

    def _generate_for_user(self, *, employee, month, field_config_id, template_id, request):
        profile = EmployeePayrollProfile.objects.filter(user=employee, is_active=True).first()
        if not profile:
            return None, {"error": "Active payroll profile not found for employee"}, status.HTTP_400_BAD_REQUEST

        preferred_template_id = template_id or profile.default_template_id
        template = self._resolve_template(profile.location_id, preferred_template_id)
        if not template:
            return None, {"error": "Payslip template not found for location"}, status.HTTP_400_BAD_REQUEST

        preferred_field_config_id = field_config_id or profile.default_field_config_id
        field_config = self._resolve_field_config(profile.location_id, preferred_field_config_id)
        if not field_config:
            return None, {"error": "Payslip field config not found for location"}, status.HTTP_400_BAD_REQUEST

        user_tz = get_user_timezone_from_request(request, location_id=profile.location_id)
        attendance_snapshot = calculate_attendance_from_master(employee, month, user_tz)

        fields = PayslipField.objects.filter(field_config=field_config, is_deleted=False, is_visible=True).order_by("display_order")
        calc = calculate_salary_fields(profile.gross_salary, fields, attendance_snapshot)

        with transaction.atomic():
            existing = PayslipRecord.objects.filter(user=employee, month=month).first()
            if existing and existing.status == "PAID":
                return None, {"error": "This month is already marked as PAID. You cannot regenerate this payslip."}, status.HTTP_400_BAD_REQUEST

            payload = {
                "location": profile.location,
                "payroll_profile": profile,
                "field_config": field_config,
                "template": template,
                "month_days": attendance_snapshot["month_days"],
                "working_days": attendance_snapshot["working_days"],
                "present_days": attendance_snapshot["present_days"],
                "half_days": attendance_snapshot["half_days"],
                "absent_days": attendance_snapshot["absent_days"],
                "paid_days": attendance_snapshot["paid_days"],
                "gross_salary": profile.gross_salary,
                "total_earnings": calc["total_earnings"],
                "total_deductions": calc["total_deductions"],
                "net_pay": calc["net_pay"],
                "field_values": calc["field_values"],
                "attendance_snapshot": {
                    k: str(v) if hasattr(v, "quantize") else v for k, v in attendance_snapshot.items()
                },
                "generated_by": request.user,
                "status": existing.status if existing else "DRAFT",
            }

            if existing:
                if not self._can_edit_record(existing):
                    return None, {"error": "This payslip cannot be edited in current state."}, status.HTTP_400_BAD_REQUEST
                for key, value in payload.items():
                    setattr(existing, key, value)
                self._save_pdf_snapshot(existing)
                existing.save()
                record = existing
            else:
                record = PayslipRecord.objects.create(user=employee, month=month, **payload)
                self._save_pdf_snapshot(record)
                record.save(update_fields=["pdf_file"])
        return record, None, None

    @action(detail=False, methods=["post"], url_path="generate")
    def generate(self, request):
        serializer = PayslipGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            employee = User.objects.get(id=data["user_id"], is_deleted=False)
        except User.DoesNotExist:
            return Response({"error": "Employee not found"}, status=status.HTTP_404_NOT_FOUND)

        record, err, err_status = self._generate_for_user(
            employee=employee,
            month=data["month"],
            field_config_id=data.get("field_config_id"),
            template_id=data.get("template_id"),
            request=request,
        )
        if err:
            return Response(err, status=err_status)

        return Response(PayslipRecordSerializer(record).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=["post"], url_path="generate-bulk")
    def generate_bulk(self, request):
        serializer = PayslipBulkGenerateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        generated = []
        skipped = []

        def _stringify_reason(err_payload):
            if isinstance(err_payload, dict):
                if "error" in err_payload and isinstance(err_payload["error"], str):
                    return err_payload["error"]
                flat_bits = []
                for key, value in err_payload.items():
                    if isinstance(value, (list, tuple)):
                        flat_bits.append(f"{key}: {', '.join([str(v) for v in value])}")
                    else:
                        flat_bits.append(f"{key}: {value}")
                return "; ".join(flat_bits) if flat_bits else "Validation failed"
            return str(err_payload or "Validation failed")

        for user_id in data["user_ids"]:
            try:
                employee = User.objects.get(id=user_id, is_deleted=False)
            except User.DoesNotExist:
                skipped.append({"user_id": str(user_id), "employee_name": None, "reason": "Employee not found"})
                continue

            record, err, _err_status = self._generate_for_user(
                employee=employee,
                month=data["month"],
                field_config_id=data.get("field_config_id"),
                template_id=data.get("template_id"),
                request=request,
            )
            if err:
                skipped.append(
                    {
                        "user_id": str(user_id),
                        "employee_name": employee.name,
                        "reason": _stringify_reason(err),
                    }
                )
            else:
                generated.append(PayslipRecordSerializer(record).data)

        return Response(
            {
                "generated_count": len(generated),
                "skipped_count": len(skipped),
                "generated": generated,
                "skipped": skipped,
                "message": f"{len(generated)} generated, {len(skipped)} skipped",
            },
            status=status.HTTP_200_OK,
        )

    @action(detail=True, methods=["post"], url_path="mark-paid")
    def mark_paid(self, request, pk=None):
        try:
            record = PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response({"error": "Payslip record not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = PayslipMarkPaidSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if record.status == "PAID":
            return Response({"error": "Payslip is already marked as PAID"}, status=status.HTTP_400_BAD_REQUEST)
        if record.status != "APPROVED":
            return Response({"error": "Only APPROVED payslip can be marked as PAID"}, status=status.HTTP_400_BAD_REQUEST)

        record.paid_amount = data["paid_amount"]
        record.paid_on = data.get("paid_on") or timezone.now()
        record.payment_mode = data.get("payment_mode")
        record.payment_ref_no = data.get("payment_ref_no")
        record.payment_notes = data.get("payment_notes")
        record.status = "PAID"
        record.paid_marked_by = request.user
        self._save_pdf_snapshot(record)
        record.save()

        return Response(PayslipRecordSerializer(record).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="approve")
    def approve(self, request, pk=None):
        try:
            record = PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response({"error": "Payslip record not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = PayslipApproveSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if record.status == "PAID":
            return Response({"error": "PAID payslip cannot be approved again"}, status=status.HTTP_400_BAD_REQUEST)
        if record.status == "APPROVED":
            return Response({"error": "Payslip is already APPROVED"}, status=status.HTTP_400_BAD_REQUEST)

        record.status = "APPROVED"
        record.approved_on = timezone.now()
        record.approved_by = request.user
        if data.get("notes"):
            record.notes = data.get("notes")
        self._save_pdf_snapshot(record)
        record.save()
        return Response(PayslipRecordSerializer(record).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["post"], url_path="reopen")
    def reopen(self, request, pk=None):
        """
        Move APPROVED back to DRAFT.
        PAID records remain locked.
        """
        try:
            record = PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response({"error": "Payslip record not found"}, status=status.HTTP_404_NOT_FOUND)

        serializer = PayslipReopenSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        if record.status == "PAID":
            return Response({"error": "PAID payslip cannot be reopened"}, status=status.HTTP_400_BAD_REQUEST)
        if record.status == "DRAFT":
            return Response({"error": "Payslip is already in DRAFT"}, status=status.HTTP_400_BAD_REQUEST)

        record.status = "DRAFT"
        record.approved_on = None
        record.approved_by = None
        if data.get("notes"):
            record.notes = data.get("notes")
        self._save_pdf_snapshot(record)
        record.save()
        return Response(PayslipRecordSerializer(record).data, status=status.HTTP_200_OK)

    @action(detail=True, methods=["get"], url_path="download")
    def download(self, request, pk=None):
        try:
            record = PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response({"error": "Payslip record not found"}, status=status.HTTP_404_NOT_FOUND)

        # Always regenerate from current template/profile data so latest
        # logo/header/footer changes are reflected in View/Download.
        self._save_pdf_snapshot(record)
        record.save(update_fields=["pdf_file", "modified_on"])

        try:
            file_handle = record.pdf_file.open("rb")
        except Exception:
            return Response({"error": "Unable to open payslip PDF"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        download_name = f"payslip_{record.month}_{record.user_id}.pdf"
        return FileResponse(file_handle, as_attachment=True, filename=download_name, content_type="application/pdf")

    @action(detail=True, methods=["post"], url_path="edit-attendance")
    def edit_attendance(self, request, pk=None):
        try:
            record = PayslipRecord.objects.get(pk=pk)
        except PayslipRecord.DoesNotExist:
            return Response({"error": "Payslip record not found"}, status=status.HTTP_404_NOT_FOUND)

        if not self._can_edit_record(record):
            return Response({"error": "This payslip cannot be edited in current state."}, status=status.HTTP_400_BAD_REQUEST)

        serializer = PayslipRecordAttendanceEditSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        present_days = Decimal(data["present_days"])
        half_days = Decimal(data["half_days"])
        absent_days = Decimal(data["absent_days"])
        working_days = present_days + absent_days + (half_days * Decimal("0.5"))
        paid_days = present_days + (half_days * Decimal("0.5"))

        month_days = Decimal(str(record.month_days or 0))
        if working_days > month_days:
            return Response(
                {
                    "error": (
                        f"Invalid days: present + absent + (half x 0.5) ({working_days}) "
                        f"cannot exceed month days ({month_days})."
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        attendance_snapshot = {
            "month_days": record.month_days,
            "working_days": working_days,
            "present_days": present_days,
            "half_days": half_days,
            "absent_days": absent_days,
            "paid_days": paid_days,
        }

        fields = PayslipField.objects.filter(
            field_config=record.field_config,
            is_deleted=False,
            is_visible=True,
        ).order_by("display_order")
        calc = calculate_salary_fields(record.gross_salary, fields, attendance_snapshot)

        with transaction.atomic():
            record.working_days = working_days
            record.present_days = present_days
            record.half_days = half_days
            record.absent_days = absent_days
            record.paid_days = paid_days
            record.total_earnings = calc["total_earnings"]
            record.total_deductions = calc["total_deductions"]
            record.net_pay = calc["net_pay"]
            record.field_values = calc["field_values"]
            record.attendance_snapshot = {
                k: str(v) if hasattr(v, "quantize") else v for k, v in attendance_snapshot.items()
            }
            self._save_pdf_snapshot(record)
            record.save()

        return Response(PayslipRecordSerializer(record).data, status=status.HTTP_200_OK)

