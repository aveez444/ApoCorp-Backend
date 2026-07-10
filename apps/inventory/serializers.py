# apps/inventory/serializers.py

from decimal import Decimal
from rest_framework import serializers
from django.db.models import Sum, F

from .models import (
    ItemMaster, Warehouse, StorageLocation,
    StockBatch, StockLedger,
    MaterialIssueSlip, MaterialIssueSlipItem,
    BarcodeLabel, StockReservation,
)


# ─────────────────────────────────────────────────────────────────────────────
# Item Master
# ─────────────────────────────────────────────────────────────────────────────

class ItemMasterListSerializer(serializers.ModelSerializer):
    """Flat lightweight serializer for dropdowns and list views."""
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = ItemMaster
        fields = [
            'id', 'item_code', 'name', 'item_type', 'category',
            'sub_category', 'uom', 'hsn_code', 'tax_percent',
            'reorder_level', 'standard_cost', 'valuation_method',
            'is_batch_tracked', 'is_serial_tracked', 'is_active',
            'drawing_number', 'revision_number',
            'created_by', 'created_by_name', 'created_at',
        ]
        read_only_fields = ('item_code', 'tenant', 'created_by', 'created_at')

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None


class ItemMasterDetailSerializer(serializers.ModelSerializer):
    """Full serializer with stock summary appended."""
    created_by_name = serializers.SerializerMethodField()
    product_name    = serializers.SerializerMethodField()
    stock_summary   = serializers.SerializerMethodField()

    class Meta:
        model  = ItemMaster
        fields = '__all__'
        read_only_fields = ('item_code', 'tenant', 'created_by', 'created_at', 'updated_at')

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None

    def get_product_name(self, obj):
        return obj.product.name if obj.product else None

    def get_stock_summary(self, obj):
        """
        Aggregated on_hand / reserved / available across all warehouses.
        Added to retrieve view so the frontend can show stock without a
        separate stock query.
        """
        result = (
            StockBatch.objects
            .filter(item=obj, qc_status='PASSED')
            .aggregate(
                on_hand=Sum('quantity_on_hand'),
                reserved=Sum('quantity_reserved'),
            )
        )
        on_hand  = result['on_hand']  or Decimal('0')
        reserved = result['reserved'] or Decimal('0')
        return {
            'on_hand':   on_hand,
            'reserved':  reserved,
            'available': on_hand - reserved,
        }

    def create(self, validated_data):
        request = self.context.get('request')
        if request:
            validated_data['created_by'] = request.user
        return super().create(validated_data)


# ─────────────────────────────────────────────────────────────────────────────
# Warehouse + Storage Location
# ─────────────────────────────────────────────────────────────────────────────

class StorageLocationSerializer(serializers.ModelSerializer):
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)

    class Meta:
        model  = StorageLocation
        fields = '__all__'
        read_only_fields = ('bin_code',)  # auto-computed in model.save()


class WarehouseSerializer(serializers.ModelSerializer):
    locations = StorageLocationSerializer(many=True, read_only=True)

    class Meta:
        model  = Warehouse
        fields = '__all__'
        read_only_fields = ('tenant',)


# ─────────────────────────────────────────────────────────────────────────────
# Stock Batch
# ─────────────────────────────────────────────────────────────────────────────

class StockBatchSerializer(serializers.ModelSerializer):
    item_code        = serializers.CharField(source='item.item_code',   read_only=True)
    item_name        = serializers.CharField(source='item.name',        read_only=True)
    warehouse_code   = serializers.CharField(source='warehouse.code',   read_only=True)
    bin_code         = serializers.SerializerMethodField()
    quantity_available = serializers.SerializerMethodField()

    class Meta:
        model  = StockBatch
        fields = '__all__'
        read_only_fields = ('tenant',)

    def get_bin_code(self, obj):
        return obj.storage_location.bin_code if obj.storage_location else None

    def get_quantity_available(self, obj):
        return obj.quantity_available


# ─────────────────────────────────────────────────────────────────────────────
# Stock summary (used by GET /inventory/stock/)
# ─────────────────────────────────────────────────────────────────────────────

class StockSummarySerializer(serializers.Serializer):
    """
    Read-only. One row per item per warehouse showing aggregated quantities.
    Returned by the StockViewSet.list action.
    """
    item_id       = serializers.UUIDField()
    item_code     = serializers.CharField()
    item_name     = serializers.CharField()
    uom           = serializers.CharField()
    warehouse_id  = serializers.UUIDField()
    warehouse_code = serializers.CharField()
    on_hand       = serializers.DecimalField(max_digits=15, decimal_places=3)
    reserved      = serializers.DecimalField(max_digits=15, decimal_places=3)
    available     = serializers.DecimalField(max_digits=15, decimal_places=3)
    reorder_level = serializers.DecimalField(max_digits=15, decimal_places=3)
    below_reorder = serializers.BooleanField()
    batches       = StockBatchSerializer(many=True)


# ─────────────────────────────────────────────────────────────────────────────
# Stock Ledger  (read-only)
# ─────────────────────────────────────────────────────────────────────────────

class StockLedgerSerializer(serializers.ModelSerializer):
    item_code      = serializers.CharField(source='item.item_code', read_only=True)
    item_name      = serializers.CharField(source='item.name',      read_only=True)
    warehouse_code = serializers.CharField(source='warehouse.code', read_only=True)
    batch_number   = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = StockLedger
        fields = '__all__'

    def get_batch_number(self, obj):
        return obj.batch.batch_number if obj.batch else None

    def get_created_by_name(self, obj):
        if obj.created_by:
            return obj.created_by.get_full_name() or obj.created_by.username
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Material Issue Slip
# ─────────────────────────────────────────────────────────────────────────────

class MaterialIssueSlipItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name',      read_only=True)
    bin_code  = serializers.SerializerMethodField()

    class Meta:
        model  = MaterialIssueSlipItem
        fields = '__all__'
        read_only_fields = ('slip', 'issued_qty', 'batch')

    def get_bin_code(self, obj):
        return obj.storage_location.bin_code if obj.storage_location else None


class MaterialIssueSlipSerializer(serializers.ModelSerializer):
    items           = MaterialIssueSlipItemSerializer(many=True)
    issued_by_name  = serializers.SerializerMethodField()

    class Meta:
        model  = MaterialIssueSlip
        fields = '__all__'
        read_only_fields = ('slip_number', 'tenant', 'issued_by', 'issued_at')

    def get_issued_by_name(self, obj):
        if obj.issued_by:
            return obj.issued_by.get_full_name() or obj.issued_by.username
        return None

    def create(self, validated_data):
        from django.db import transaction
        items_data = validated_data.pop('items', [])
        request    = self.context.get('request')
        if request:
            validated_data['issued_by'] = request.user

        with transaction.atomic():
            slip = MaterialIssueSlip.objects.create(**validated_data)
            for item_data in items_data:
                MaterialIssueSlipItem.objects.create(slip=slip, **item_data)
        return slip

    def update(self, instance, validated_data):
        from django.db import transaction
        items_data = validated_data.pop('items', None)

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None and instance.status == 'DRAFT':
            # Only allow item edits in DRAFT status
            instance.items.all().delete()
            for item_data in items_data:
                MaterialIssueSlipItem.objects.create(slip=instance, **item_data)

        return instance


# ─────────────────────────────────────────────────────────────────────────────
# Barcode Label
# ─────────────────────────────────────────────────────────────────────────────

class BarcodeLabelSerializer(serializers.ModelSerializer):
    item_code    = serializers.CharField(source='item.item_code', read_only=True)
    batch_number = serializers.SerializerMethodField()

    class Meta:
        model  = BarcodeLabel
        fields = '__all__'

    def get_batch_number(self, obj):
        return obj.batch.batch_number if obj.batch else None


class BarcodeGenerateSerializer(serializers.Serializer):
    """
    Input serializer for POST /inventory/labels/generate/
    """
    grn_id    = serializers.UUIDField(required=False, help_text="Generate labels for all items in a GRN")
    item_id   = serializers.UUIDField(required=False, help_text="Generate labels for a specific item+batch")
    batch_id  = serializers.UUIDField(required=False)
    label_type = serializers.ChoiceField(choices=['GRN', 'ISSUE', 'TRANSFER'], default='GRN')
    count     = serializers.IntegerField(min_value=1, max_value=100, default=1,
                                         help_text="Number of labels per line item")

    def validate(self, attrs):
        if not attrs.get('grn_id') and not attrs.get('item_id'):
            raise serializers.ValidationError("Either grn_id or item_id must be provided.")
        return attrs
    
# ─────────────────────────────────────────────────────────────────────────────
# Stock Reservation
# ─────────────────────────────────────────────────────────────────────────────

class StockReservationSerializer(serializers.ModelSerializer):
    item_code        = serializers.CharField(source='item.item_code',        read_only=True)
    item_name        = serializers.CharField(source='item.name',             read_only=True)
    uom              = serializers.CharField(source='item.uom',              read_only=True)
    warehouse_code   = serializers.CharField(source='warehouse.code',        read_only=True)
    project_number   = serializers.CharField(source='project.project_number', read_only=True)
    project_name     = serializers.CharField(source='project.name',          read_only=True)
    mrp_run_number   = serializers.SerializerMethodField()
    requested_by_name = serializers.SerializerMethodField()
    approved_by_name  = serializers.SerializerMethodField()
    remaining_qty    = serializers.SerializerMethodField()

    class Meta:
        model  = StockReservation
        fields = [
            'id', 'project', 'project_number', 'project_name',
            'mrp_run', 'mrp_run_number',
            'item', 'item_code', 'item_name', 'uom',
            'warehouse', 'warehouse_code',
            'requested_qty', 'approved_qty', 'issued_qty', 'remaining_qty',
            'required_by_date', 'status',
            'requested_by', 'requested_by_name',
            'approved_by', 'approved_by_name',
            'rejection_reason', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = (
            'tenant', 'status', 'approved_qty', 'issued_qty',
            'requested_by', 'approved_by', 'created_at', 'updated_at',
        )

    def get_mrp_run_number(self, obj):
        return obj.mrp_run.run_number if obj.mrp_run else None

    def get_requested_by_name(self, obj):
        if obj.requested_by:
            return obj.requested_by.get_full_name() or obj.requested_by.username
        return None

    def get_approved_by_name(self, obj):
        if obj.approved_by:
            return obj.approved_by.get_full_name() or obj.approved_by.username
        return None

    def get_remaining_qty(self, obj):
        return obj.remaining_qty