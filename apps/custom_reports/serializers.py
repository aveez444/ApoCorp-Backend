# apps/reports/serializers.py

from rest_framework import serializers
from .models import SavedReport
from .field_registry import FIELD_REGISTRY, MODULE_REGISTRY


# ── Allowed keys for quick lookup ─────────────────────────────────────────────
_VALID_MODULES   = {m["key"] for m in MODULE_REGISTRY}
_VALID_OPERATORS = {"eq", "neq", "in", "gte", "lte", "contains", "isnull"}


class ReportConfigSerializer(serializers.Serializer):
    """
    Validates a report config dict.  Used both when saving and when running
    an ad-hoc report without saving.
    """

    modules  = serializers.ListField(child=serializers.CharField(), required=False, default=list)
    columns  = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    filters  = serializers.ListField(child=serializers.DictField(), required=False, default=list)
    order_by = serializers.CharField(required=False, allow_blank=True, default="-created_at")

    def validate_modules(self, value):
        for mod in value:
            if mod not in _VALID_MODULES:
                raise serializers.ValidationError(f"Unknown module: '{mod}'")
        return value

    def validate_columns(self, value):
        for col in value:
            mod   = col.get("module")
            field = col.get("field")
            if mod not in FIELD_REGISTRY:
                raise serializers.ValidationError(
                    f"Column references unknown module: '{mod}'"
                )
            if field not in FIELD_REGISTRY.get(mod, {}):
                raise serializers.ValidationError(
                    f"Unknown field '{field}' in module '{mod}'"
                )
        return value

    def validate_filters(self, value):
        for f in value:
            mod      = f.get("module")
            field    = f.get("field")
            operator = f.get("operator")
            if mod not in FIELD_REGISTRY:
                raise serializers.ValidationError(
                    f"Filter references unknown module: '{mod}'"
                )
            if field not in FIELD_REGISTRY.get(mod, {}):
                raise serializers.ValidationError(
                    f"Unknown filter field '{field}' in module '{mod}'"
                )
            if operator and operator not in _VALID_OPERATORS:
                raise serializers.ValidationError(
                    f"Unknown filter operator: '{operator}'"
                )
            # Check field is filterable
            defn = FIELD_REGISTRY[mod][field]
            if not defn.get("filterable", True):
                raise serializers.ValidationError(
                    f"Field '{field}' in module '{mod}' is not filterable"
                )
        return value


class SavedReportSerializer(serializers.ModelSerializer):
    """Full serializer for listing and retrieving saved reports."""

    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = SavedReport
        fields = "__all__"
        read_only_fields = (
            "tenant",
            "created_by",
            "created_at",
            "updated_at",
        )

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def validate_config(self, value):
        """Run config through ReportConfigSerializer for deep validation."""
        s = ReportConfigSerializer(data=value)
        if not s.is_valid():
            raise serializers.ValidationError(s.errors)
        return s.validated_data


class SavedReportListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for the sidebar list."""

    created_by_name = serializers.SerializerMethodField()
    column_count    = serializers.SerializerMethodField()

    class Meta:
        model  = SavedReport
        fields = (
            "id", "name", "description",
            "is_shared",
            "created_by_name",
            "column_count",
            "created_at",
            "updated_at",
        )

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def get_column_count(self, obj):
        return len(obj.config.get("columns", []))


class RunReportSerializer(serializers.Serializer):
    """
    Request body for POST /reports/run/.
    Accepts a full config + optional pagination params.
    """
    config    = serializers.DictField()
    page      = serializers.IntegerField(min_value=1, default=1, required=False)
    page_size = serializers.IntegerField(min_value=1, max_value=500, default=50, required=False)

    def validate_config(self, value):
        s = ReportConfigSerializer(data=value)
        if not s.is_valid():
            raise serializers.ValidationError(s.errors)
        return s.validated_data