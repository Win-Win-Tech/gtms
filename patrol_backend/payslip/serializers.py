from rest_framework import serializers
import ast
from decimal import Decimal, InvalidOperation

from .models import (
    EmployeePayrollProfile,
    PayslipTemplate,
    PayslipFieldConfig,
    PayslipField,
    PayslipRecord,
)


class EmployeePayrollProfileSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    employee_code = serializers.CharField(source="user.employee_code", read_only=True)

    class Meta:
        model = EmployeePayrollProfile
        fields = "__all__"
        read_only_fields = ("created_on", "modified_on", "created_by", "modified_by")

    def validate(self, attrs):
        location = attrs.get("location") or getattr(self.instance, "location", None)
        default_field_config = attrs.get("default_field_config", getattr(self.instance, "default_field_config", None))
        default_template = attrs.get("default_template", getattr(self.instance, "default_template", None))

        if default_field_config and location and default_field_config.location_id != location.id:
            raise serializers.ValidationError({"default_field_config": "Selected field config must belong to profile location"})

        if default_template and location and default_template.location_id != location.id:
            raise serializers.ValidationError({"default_template": "Selected template must belong to profile location"})

        return attrs


class PayslipTemplateSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = PayslipTemplate
        fields = "__all__"
        read_only_fields = ("created_on", "modified_on", "created_by", "modified_by", "is_deleted")


class PayslipFieldConfigSerializer(serializers.ModelSerializer):
    location_name = serializers.CharField(source="location.name", read_only=True)

    class Meta:
        model = PayslipFieldConfig
        fields = "__all__"
        read_only_fields = ("created_on", "modified_on", "created_by", "modified_by", "is_deleted")


class PayslipFieldSerializer(serializers.ModelSerializer):
    SYSTEM_VARS = {
        "gross_salary",
        "month_days",
        "working_days",
        "present_days",
        "half_days",
        "half_days_count",
        "half_days_paid",
        "absent_days",
        "paid_days",
        "Decimal",
    }

    class Meta:
        model = PayslipField
        fields = "__all__"
        read_only_fields = ("created_on", "modified_on", "is_deleted")

    def _validate_numeric(self, value, field_name):
        try:
            Decimal(str(value))
        except (InvalidOperation, ValueError, TypeError):
            raise serializers.ValidationError({field_name: f"{field_name} must be numeric"})

    def _extract_name_tokens(self, expr: str):
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise serializers.ValidationError({"value": f"Invalid formula syntax: {exc.msg}"})

        names = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                names.add(node.id)
        return names

    def _validate_formula_ast_safety(self, expr: str):
        try:
            tree = ast.parse(expr, mode="eval")
        except SyntaxError as exc:
            raise serializers.ValidationError({"value": f"Invalid formula syntax: {exc.msg}"})

        allowed_nodes = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.Name,
            ast.Load,
            ast.Constant,
            ast.Call,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.Mod,
            ast.Pow,
            ast.FloorDiv,
            ast.UAdd,
            ast.USub,
        )

        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                raise serializers.ValidationError(
                    {"value": f"Unsupported expression type: {node.__class__.__name__}"}
                )
            if isinstance(node, ast.Call):
                if not isinstance(node.func, ast.Name) or node.func.id != "Decimal":
                    raise serializers.ValidationError({"value": "Only Decimal(...) function call is allowed"})
                if len(node.args) != 1 or node.keywords:
                    raise serializers.ValidationError({"value": "Decimal(...) accepts exactly one argument"})

    def _validate_formula_dependency(self, *, field_config, current_field_code, current_formula):
        # Build effective formulas after this create/update (excluding deleted).
        all_fields = list(PayslipField.objects.filter(field_config=field_config, is_deleted=False))
        effective = {}
        for f in all_fields:
            if self.instance and f.id == self.instance.id:
                continue
            effective[f.field_code] = (f.value_type, f.value)
        effective[current_field_code] = ("FORMULA", current_formula)

        field_codes = set(effective.keys())

        # 1) Unknown dependency check
        for code, (vtype, expr) in effective.items():
            if vtype != "FORMULA":
                continue
            self._validate_formula_ast_safety(str(expr or ""))
            names = self._extract_name_tokens(str(expr or ""))
            unknown = sorted(n for n in names if n not in self.SYSTEM_VARS and n not in field_codes)
            if unknown:
                raise serializers.ValidationError(
                    {"value": f"Unknown formula reference(s) in {code}: {', '.join(unknown)}"}
                )

        # 2) Cycle detection only among formula->field references
        graph = {}
        for code, (vtype, expr) in effective.items():
            deps = set()
            if vtype == "FORMULA":
                names = self._extract_name_tokens(str(expr or ""))
                deps = {n for n in names if n in field_codes}
            graph[code] = deps

        visiting, visited = set(), set()

        def dfs(node):
            if node in visiting:
                raise serializers.ValidationError({"value": f"Circular dependency detected at field '{node}'"})
            if node in visited:
                return
            visiting.add(node)
            for dep in graph.get(node, set()):
                dfs(dep)
            visiting.remove(node)
            visited.add(node)

        for n in graph.keys():
            dfs(n)

    def validate(self, attrs):
        value_type = attrs.get("value_type", getattr(self.instance, "value_type", None))
        value = attrs.get("value", getattr(self.instance, "value", None))
        field_code = attrs.get("field_code", getattr(self.instance, "field_code", None))
        field_config = attrs.get("field_config", getattr(self.instance, "field_config", None))

        if value_type in ("FIXED", "PERCENTAGE"):
            self._validate_numeric(value, "value")

        if value_type == "FORMULA":
            if not value or not str(value).strip():
                raise serializers.ValidationError({"value": "Formula cannot be empty"})
            if not field_config:
                raise serializers.ValidationError({"field_config": "field_config is required for formula validation"})
            if not field_code:
                raise serializers.ValidationError({"field_code": "field_code is required for formula validation"})
            self._validate_formula_dependency(
                field_config=field_config,
                current_field_code=field_code,
                current_formula=str(value).strip(),
            )

        return attrs


class PayslipRecordSerializer(serializers.ModelSerializer):
    user_name = serializers.CharField(source="user.name", read_only=True)
    location_name = serializers.CharField(source="location.name", read_only=True)
    employee_code = serializers.CharField(source="user.employee_code", read_only=True)

    class Meta:
        model = PayslipRecord
        fields = "__all__"
        read_only_fields = (
            "location",
            "payroll_profile",
            "field_config",
            "template",
            "month_days",
            "working_days",
            "present_days",
            "half_days",
            "absent_days",
            "paid_days",
            "gross_salary",
            "total_earnings",
            "total_deductions",
            "net_pay",
            "field_values",
            "attendance_snapshot",
            "status",
            "generated_on",
            "modified_on",
            "generated_by",
            "approved_on",
            "approved_by",
            "paid_marked_by",
            "pdf_file",
        )


class PayslipGenerateSerializer(serializers.Serializer):
    user_id = serializers.UUIDField()
    month = serializers.CharField(max_length=7)
    field_config_id = serializers.UUIDField(required=False, allow_null=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)


class PayslipBulkGenerateSerializer(serializers.Serializer):
    user_ids = serializers.ListField(child=serializers.UUIDField(), allow_empty=False)
    month = serializers.CharField(max_length=7)
    field_config_id = serializers.UUIDField(required=False, allow_null=True)
    template_id = serializers.UUIDField(required=False, allow_null=True)


class PayslipMarkPaidSerializer(serializers.Serializer):
    paid_amount = serializers.DecimalField(max_digits=12, decimal_places=2)
    paid_on = serializers.DateTimeField(required=False)
    payment_mode = serializers.ChoiceField(
        choices=[c[0] for c in PayslipRecord.PAYMENT_MODE_CHOICES],
        required=False,
        allow_null=True,
    )
    payment_ref_no = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    payment_notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PayslipApproveSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PayslipReopenSerializer(serializers.Serializer):
    notes = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class PayslipRecordAttendanceEditSerializer(serializers.Serializer):
    present_days = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)
    half_days = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)
    absent_days = serializers.DecimalField(max_digits=6, decimal_places=2, min_value=0)

    def validate(self, attrs):
        # For manual corrections:
        # - present/absent can be in 0.5 steps (e.g., 15.5)
        # - half_days is a count of half-day occurrences (whole number)
        for key in ("present_days", "absent_days"):
            value = attrs.get(key)
            if value is None:
                continue
            doubled = value * Decimal("2")
            if doubled != doubled.to_integral_value():
                raise serializers.ValidationError(
                    {key: f"{key} must be in 0.5 steps (example: 15, 15.5, 16)."}
                )

        half_days = attrs.get("half_days")
        if half_days is not None and half_days != half_days.to_integral_value():
            raise serializers.ValidationError(
                {"half_days": "half_days must be a whole number (0, 1, 2, ...)."}
            )
        return attrs

