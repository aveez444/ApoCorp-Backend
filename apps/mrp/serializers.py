# apps/mrp/serializers.py
from rest_framework import serializers
from django.db import transaction
from decimal import Decimal
from .models import MRPRun, MRPLine, MRPRunStatus, MRPLineStatus


def _user_name(user):
    if user:
        return user.get_full_name() or user.username
    return None


# ─────────────────────────────────────────────────────────────────────────────
# MRP Line Serializers
# ─────────────────────────────────────────────────────────────────────────────

class MRPLineSerializer(serializers.ModelSerializer):
    """Full MRP line serializer"""
    engineering_item_code = serializers.CharField(
        source='engineering_item.item_code', 
        read_only=True
    )
    engineering_item_name = serializers.CharField(
        source='engineering_item.name', 
        read_only=True
    )
    inventory_item_code = serializers.CharField(
        source='inventory_item.item_code', 
        read_only=True
    )
    item_class_display = serializers.CharField(
        source='get_item_class_display', 
        read_only=True
    )
    status_display = serializers.CharField(
        source='get_status_display', 
        read_only=True
    )
    
    class Meta:
        model = MRPLine
        fields = '__all__'
        read_only_fields = (
            'mrp_run', 'shortage_qty', 'has_shortage', 
            'status', 'created_at', 'updated_at'
        )


class MRPLineShortageSerializer(serializers.ModelSerializer):
    """Serializer for shortage lines only"""
    engineering_item_code = serializers.CharField(
        source='engineering_item.item_code', 
        read_only=True
    )
    engineering_item_name = serializers.CharField(
        source='engineering_item.name', 
        read_only=True
    )
    inventory_item_code = serializers.CharField(
        source='inventory_item.item_code', 
        read_only=True
    )
    item_class_display = serializers.CharField(
        source='get_item_class_display', 
        read_only=True
    )
    
    class Meta:
        model = MRPLine
        fields = [
            'id', 'engineering_item', 'engineering_item_code', 'engineering_item_name',
            'inventory_item', 'inventory_item_code', 'item_class', 'item_class_display',
            'required_qty', 'uom', 'available_qty', 'on_order_qty', 'shortage_qty',
            'recommendation', 'bom_path', 'depth', 'indent_raised', 'status'
        ]


class MRPLineUpdateSerializer(serializers.Serializer):
    """Serializer for updating MRP line quantities"""
    override_required_qty = serializers.DecimalField(
        max_digits=15, 
        decimal_places=3,
        required=False,
        min_value=Decimal('0')
    )
    note = serializers.CharField(required=False, allow_blank=True)


# ─────────────────────────────────────────────────────────────────────────────
# MRP Run Serializers
# ─────────────────────────────────────────────────────────────────────────────

class MRPRunListSerializer(serializers.ModelSerializer):
    """Lightweight MRP run serializer for list views"""
    project_name = serializers.CharField(source='project.name', read_only=True)
    project_number = serializers.CharField(source='project.project_number', read_only=True)
    bom_name = serializers.CharField(source='bom.name', read_only=True)
    run_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    class Meta:
        model = MRPRun
        fields = [
            'id', 'run_number', 'project', 'project_name', 'project_number',
            'bom', 'bom_name', 'status', 'status_display', 'run_by', 'run_by_name',
            'run_at', 'completed_at', 'total_items', 'items_with_shortage',
            'total_shortage_value', 'notes'
        ]
    
    def get_run_by_name(self, obj):
        return _user_name(obj.run_by)


class MRPRunDetailSerializer(serializers.ModelSerializer):
    """Full MRP run serializer with all lines"""
    project_name = serializers.CharField(source='project.name', read_only=True)
    project_number = serializers.CharField(source='project.project_number', read_only=True)
    bom_name = serializers.CharField(source='bom.name', read_only=True)
    bom_version = serializers.CharField(source='bom.version', read_only=True)
    run_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    
    # All lines (filtered by shortage status can be done via query param)
    lines = MRPLineSerializer(many=True, read_only=True)
    
    class Meta:
        model = MRPRun
        fields = '__all__'
        read_only_fields = (
            'tenant', 'run_number', 'run_at', 'completed_at',
            'total_items', 'items_with_shortage', 'total_shortage_value'
        )
    
    def get_run_by_name(self, obj):
        return _user_name(obj.run_by)


class MRPRunSummarySerializer(serializers.Serializer):
    """Summary stats for an MRP run"""
    total_items = serializers.IntegerField()
    items_with_shortage = serializers.IntegerField()
    total_shortage_value = serializers.DecimalField(max_digits=15, decimal_places=2)
    
    shortage_by_class = serializers.DictField()
    class_a_shortages = serializers.IntegerField()
    class_b_shortages = serializers.IntegerField()
    class_c_shortages = serializers.IntegerField()


class RunMRPSerializer(serializers.Serializer):
    """Input serializer for triggering an MRP run"""
    project_id = serializers.UUIDField(required=True)
    
    def validate_project_id(self, value):
        from apps.projects.models import Project
        try:
            project = Project.objects.get(id=value)
            if not project.bom:
                raise serializers.ValidationError(
                    "Project does not have a BOM assigned."
                )
            if project.bom.status != 'APPROVED':
                raise serializers.ValidationError(
                    f"Project BOM is not approved (status: {project.bom.status})."
                )
            return project
        except Project.DoesNotExist:
            raise serializers.ValidationError("Project not found.")


# ─────────────────────────────────────────────────────────────────────────────
# Convert to Indent Serializer
# ─────────────────────────────────────────────────────────────────────────────

class ConvertToIndentSerializer(serializers.Serializer):
    """Input serializer for converting MRP shortages to Purchase Indent"""
    line_ids = serializers.ListField(
        child=serializers.UUIDField(),
        required=False,
        help_text="List of MRP line IDs to convert. If empty, converts all shortages."
    )
    indent_type = serializers.CharField(
        required=False,
        default='PRODUCTION',
        help_text="Indent type: PRODUCTION, MAINTENANCE, GENERAL, PROJECT"
    )
    priority = serializers.CharField(
        required=False,
        default='NORMAL',
        help_text="Priority for the indent"
    )
    notes = serializers.CharField(
        required=False,
        allow_blank=True,
        help_text="Notes to add to the indent"
    )
    
    def validate_line_ids(self, value):
        if value:
            # Verify all lines belong to the same MRP run
            from .models import MRPLine
            lines = MRPLine.objects.filter(id__in=value)
            if lines.count() != len(value):
                raise serializers.ValidationError("One or more line IDs are invalid.")
            
            # Check if any line is already converted
            if lines.filter(indent_raised=True).exists():
                raise serializers.ValidationError(
                    "Some lines have already been converted to indents."
                )
        return value