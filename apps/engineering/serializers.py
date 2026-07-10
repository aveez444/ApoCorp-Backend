# apps/engineering/serializers.py
from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from .models import (
    EngineeringItemMaster,
    EngineeringItemRevision,
    EngineeringDocument,
    BOMLine,
    EngineeringBOM,
    ItemClass,
    DocumentType,
    EngineeringPackageChangeNotice,
    EngineeringPackage
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _user_name(user):
    if user:
        return user.get_full_name() or user.username
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Engineering Item Master
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringItemMasterListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for dropdowns and list views"""
    item_class_display = serializers.CharField(source='get_item_class_display', read_only=True)
    has_inventory_item = serializers.BooleanField(read_only=True)
    
    class Meta:
        model = EngineeringItemMaster
        fields = [
            'id', 'item_code', 'name', 'item_class', 'item_class_display',
            'category', 'sub_category', 'drawing_number', 'current_revision',
            'uom', 'make', 'model', 'is_active', 'has_inventory_item',
            'created_at'
        ]


class EngineeringItemRevisionSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    document_url = serializers.SerializerMethodField()
    
    class Meta:
        model = EngineeringItemRevision
        fields = '__all__'
        read_only_fields = ('item', 'created_at')
    
    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)
    
    def get_document_url(self, obj):
        request = self.context.get('request')
        if obj.document and request:
            return request.build_absolute_uri(obj.document.url)
        return None


class EngineeringDocumentSerializer(serializers.ModelSerializer):
    uploaded_by_name = serializers.SerializerMethodField()
    file_url = serializers.SerializerMethodField()
    doc_type_display = serializers.CharField(source='get_doc_type_display', read_only=True)
    
    class Meta:
        model = EngineeringDocument
        fields = '__all__'
        read_only_fields = ('item', 'uploaded_by', 'uploaded_at')
    
    def get_uploaded_by_name(self, obj):
        return _user_name(obj.uploaded_by)
    
    def get_file_url(self, obj):
        request = self.context.get('request')
        if obj.file and request:
            return request.build_absolute_uri(obj.file.url)
        return None


class EngineeringItemMasterDetailSerializer(serializers.ModelSerializer):
    """Full serializer with revisions and documents"""
    item_class_display = serializers.CharField(source='get_item_class_display', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    revisions = EngineeringItemRevisionSerializer(many=True, read_only=True)
    documents = EngineeringDocumentSerializer(many=True, read_only=True)
    
    # Inventory link info
    inventory_item_code = serializers.CharField(
        source='inventory_item.item_code', 
        read_only=True
    )
    inventory_item_name = serializers.CharField(
        source='inventory_item.name', 
        read_only=True
    )
    
    class Meta:
        model = EngineeringItemMaster
        fields = '__all__'
        read_only_fields = (
            'tenant', 'item_code', 'created_by', 'created_at', 'updated_at'
        )
    
    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)
    
    @transaction.atomic
    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            validated_data['created_by'] = request.user
        
        # Create the engineering item
        item = EngineeringItemMaster.objects.create(**validated_data)
        
        # If there's a drawing number but no revision, create initial revision
        if item.drawing_number and not item.current_revision:
            revision = EngineeringItemRevision.objects.create(
                item=item,
                revision='Rev A',
                drawing_number=item.drawing_number,
                specification=item.specification,
                effective_date=timezone.now().date(),
                is_current=True,
                created_by=request.user if request else None
            )
            item.current_revision = revision.revision
            item.save(update_fields=['current_revision'])
        
        return item


# ─────────────────────────────────────────────────────────────────────────────
# Create Revision Serializer
# ─────────────────────────────────────────────────────────────────────────────

class CreateRevisionSerializer(serializers.Serializer):
    """Input serializer for creating a new revision"""
    revision = serializers.CharField(max_length=10, required=True)
    drawing_number = serializers.CharField(max_length=100, required=False, allow_blank=True)
    specification = serializers.CharField(required=False, allow_blank=True)
    change_description = serializers.CharField(required=True)
    effective_date = serializers.DateField(required=True)
    document = serializers.FileField(required=False)
    
    def validate_revision(self, value):
        # Ensure revision naming convention (Rev X)
        if not value.startswith('Rev '):
            raise serializers.ValidationError("Revision should start with 'Rev '")
        return value


# ─────────────────────────────────────────────────────────────────────────────
# Link Inventory Item Serializer
# ─────────────────────────────────────────────────────────────────────────────

class LinkInventorySerializer(serializers.Serializer):
    """Input serializer for linking an Engineering Item to Inventory"""
    inventory_item_id = serializers.UUIDField(required=True)
    
    def validate_inventory_item_id(self, value):
        from apps.inventory.models import ItemMaster
        try:
            item = ItemMaster.objects.get(id=value)
            return item
        except ItemMaster.DoesNotExist:
            raise serializers.ValidationError("Inventory item not found")
        
# apps/engineering/serializers.py - Add these to existing serializers

# ─────────────────────────────────────────────────────────────────────────────
# BOM Serializers
# ─────────────────────────────────────────────────────────────────────────────

class BOMLineSerializer(serializers.ModelSerializer):
    """Serializer for BOM lines with nested children support"""
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name', read_only=True)
    item_class_display = serializers.CharField(source='get_item_class_display', read_only=True)
    children = serializers.SerializerMethodField()
    has_children = serializers.SerializerMethodField()
    
    class Meta:
        model = BOMLine
        fields = '__all__'
        read_only_fields = ('bom', 'item_class')
    
    def get_children(self, obj):
        """Recursively serialize child lines"""
        children = obj.children.all().order_by('sort_order')
        if children:
            return BOMLineSerializer(children, many=True, context=self.context).data
        return []
    
    def get_has_children(self, obj):
        return obj.children.exists()


class BOMLineCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for creating/updating BOM lines"""
    
    class Meta:
        model = BOMLine
        fields = '__all__'
        read_only_fields = ('bom', 'item_class')


class EngineeringBOMListSerializer(serializers.ModelSerializer):
    """Lightweight BOM serializer for list views"""
    parent_item_code = serializers.CharField(source='parent_item.item_code', read_only=True)
    parent_item_name = serializers.CharField(source='parent_item.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_count = serializers.SerializerMethodField()
    
    class Meta:
        model = EngineeringBOM
        fields = [
            'id', 'bom_number', 'name', 'parent_item', 'parent_item_code',
            'parent_item_name', 'version', 'status', 'status_display',
            'is_active', 'effective_date', 'line_count', 'created_at'
        ]
    
    def get_line_count(self, obj):
        return obj.lines.filter(parent_line__isnull=True).count()


class EngineeringBOMDetailSerializer(serializers.ModelSerializer):
    """Full BOM serializer with tree structure"""
    parent_item_code = serializers.CharField(source='parent_item.item_code', read_only=True)
    parent_item_name = serializers.CharField(source='parent_item.name', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    lines = serializers.SerializerMethodField()
    
    class Meta:
        model = EngineeringBOM
        fields = '__all__'
        read_only_fields = ('tenant', 'bom_number', 'created_at', 'updated_at')
    
    def get_approved_by_name(self, obj):
        return _user_name(obj.approved_by)
    
    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)
    
    def get_lines(self, obj):
        """Get only top-level lines (where parent_line is NULL)"""
        top_lines = obj.lines.filter(parent_line__isnull=True).order_by('sort_order')
        return BOMLineSerializer(top_lines, many=True, context=self.context).data


class BOMExplosionItemSerializer(serializers.Serializer):
    """Output serializer for BOM explosion results"""
    item_id = serializers.UUIDField()
    item_code = serializers.CharField()
    item_name = serializers.CharField()
    item_class = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=15, decimal_places=3)
    uom = serializers.CharField()
    depth = serializers.IntegerField()
    is_assembly = serializers.BooleanField()
    path = serializers.CharField(help_text="Path string showing parent->child relationship")

class CreateBOMRevisionSerializer(serializers.Serializer):
    """Input serializer for creating a new BOM revision"""
    new_version = serializers.CharField(max_length=10, required=True)
    effective_date = serializers.DateField(required=True)
    description = serializers.CharField(required=False, allow_blank=True)

# apps/engineering/serializers.py - Add these

class EngineeringPackageSerializer(serializers.ModelSerializer):
    """Serializer for Engineering Package. Status/lifecycle fields are read-only —
    they can only change via the transition endpoints (release/accept/reject/obsolete),
    never via a plain PATCH."""

    project_name = serializers.CharField(source='project.name', read_only=True)
    project_number = serializers.CharField(source='project.project_number', read_only=True)
    bom_number = serializers.CharField(source='source_bom.bom_number', read_only=True)
    created_by_name = serializers.SerializerMethodField()
    released_by_name = serializers.SerializerMethodField()
    accepted_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    allowed_transitions = serializers.ListField(source='get_allowed_transitions', read_only=True)

    class Meta:
        model = EngineeringPackage
        fields = '__all__'
        read_only_fields = (
            'package_number', 'status', 'bom_snapshot', 'document_snapshots',
            'released_at', 'released_by', 'accepted_at', 'accepted_by', 'acceptance_notes',
            'rejected_at', 'rejected_by', 'rejection_reason',
            'created_by', 'created_at', 'updated_at',
        )

    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)

    def get_released_by_name(self, obj):
        return _user_name(obj.released_by)

    def get_accepted_by_name(self, obj):
        return _user_name(obj.accepted_by)

class EngineeringPackageChangeNoticeSerializer(serializers.ModelSerializer):
    """Serializer for Engineering Change Notice"""
    
    package_number = serializers.CharField(source='package.package_number', read_only=True)
    new_package_number = serializers.CharField(source='new_package.package_number', read_only=True)
    requested_by_name = serializers.SerializerMethodField()
    reviewed_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    
    class Meta:
        model = EngineeringPackageChangeNotice
        fields = '__all__'
        read_only_fields = ('ecn_number', 'requested_at', 'created_at')
    
    def get_requested_by_name(self, obj):
        return _user_name(obj.requested_by)
    
    def get_reviewed_by_name(self, obj):
        return _user_name(obj.reviewed_by)
    
    def get_approved_by_name(self, obj):
        return _user_name(obj.approved_by)