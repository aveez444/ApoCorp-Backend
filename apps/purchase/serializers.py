# apps/purchase/serializers.py

from decimal import Decimal
from django.db import transaction
from django.utils import timezone
from rest_framework import serializers

from apps.vendors.serializers import VendorListSerializer
from apps.inventory.serializers import ItemMasterListSerializer

from .models import (
    PurchaseIndent, PurchaseIndentItem,
    RFQ, RFQItem, RFQVendor,
    VendorQuotation, VendorQuotationItem,
    PurchaseOrder, PurchaseOrderItem,
    GRN, GRNItem,
    VendorInvoice,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _user_name(user):
    if user:
        return user.get_full_name() or user.username
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Indent
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseIndentItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name',      read_only=True)

    class Meta:
        model  = PurchaseIndentItem
        fields = '__all__'
        read_only_fields = ('indent', 'fulfilled_qty', 'status')


class PurchaseIndentSerializer(serializers.ModelSerializer):
    items          = PurchaseIndentItemSerializer(many=True)
    raised_by_name = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PurchaseIndent
        fields = '__all__'
        read_only_fields = (
            'indent_number', 'tenant', 'raised_by',
            'approved_by', 'approved_at', 'created_at', 'updated_at',
        )

    def get_raised_by_name(self, obj):
        return _user_name(obj.raised_by)

    def get_approved_by_name(self, obj):
        return _user_name(obj.approved_by)

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        request    = self.context.get('request')
        if request:
            validated_data['raised_by'] = request.user

        indent = PurchaseIndent.objects.create(**validated_data)
        for item_data in items_data:
            # Snapshot available qty at indent time
            from apps.inventory.services import check_stock_availability
            avail = check_stock_availability(item_data['item'], item_data['required_qty'])
            item_data['available_qty_at_time'] = avail['available_qty']
            PurchaseIndentItem.objects.create(indent=indent, **item_data)
        return indent

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        if instance.status not in ('DRAFT', 'SUBMITTED'):
            raise serializers.ValidationError(
                "Indent can only be edited in DRAFT or SUBMITTED status."
            )
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                PurchaseIndentItem.objects.create(indent=instance, **item_data)
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# RFQ
# ─────────────────────────────────────────────────────────────────────────────

class RFQItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name',      read_only=True)

    class Meta:
        model  = RFQItem
        fields = '__all__'
        read_only_fields = ('rfq',)


class RFQVendorSerializer(serializers.ModelSerializer):
    vendor_name = serializers.CharField(source='vendor.name',        read_only=True)
    vendor_code = serializers.CharField(source='vendor.vendor_code', read_only=True)

    class Meta:
        model  = RFQVendor
        fields = '__all__'
        read_only_fields = ('rfq', 'sent_at')


class RFQSerializer(serializers.ModelSerializer):
    items       = RFQItemSerializer(many=True)
    rfq_vendors = RFQVendorSerializer(many=True, required=False)
    created_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = RFQ
        fields = '__all__'
        read_only_fields = ('rfq_number', 'tenant', 'created_by', 'created_at', 'updated_at')

    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)

    @transaction.atomic
    def create(self, validated_data):
        items_data   = validated_data.pop('items', [])
        vendors_data = validated_data.pop('rfq_vendors', [])
        request      = self.context.get('request')
        if request:
            validated_data['created_by'] = request.user

        rfq = RFQ.objects.create(**validated_data)
        for item_data in items_data:
            RFQItem.objects.create(rfq=rfq, **item_data)
        for vendor_data in vendors_data:
            RFQVendor.objects.create(rfq=rfq, **vendor_data)
        return rfq

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data   = validated_data.pop('items', None)
        vendors_data = validated_data.pop('rfq_vendors', None)
        if instance.status not in ('DRAFT',):
            raise serializers.ValidationError("RFQ can only be edited in DRAFT status.")

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                RFQItem.objects.create(rfq=instance, **item_data)
        if vendors_data is not None:
            instance.rfq_vendors.all().delete()
            for vendor_data in vendors_data:
                RFQVendor.objects.create(rfq=instance, **vendor_data)
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# Vendor Quotation
# ─────────────────────────────────────────────────────────────────────────────

class VendorQuotationItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='rfq_item.item.item_code', read_only=True)
    item_name = serializers.CharField(source='rfq_item.item.name',      read_only=True)

    class Meta:
        model  = VendorQuotationItem
        fields = '__all__'
        read_only_fields = ('quotation', 'tax_amount', 'total_price')


class VendorQuotationSerializer(serializers.ModelSerializer):
    items       = VendorQuotationItemSerializer(many=True)
    vendor_name = serializers.CharField(source='vendor.name',        read_only=True)
    vendor_code = serializers.CharField(source='vendor.vendor_code', read_only=True)

    class Meta:
        model  = VendorQuotation
        fields = '__all__'
        read_only_fields = (
            'tenant', 'is_selected', 'total_value', 'created_at',
        )

    def validate(self, attrs):
        # Enforce justification when manually marking as SELECTED
        if attrs.get('status') == 'SELECTED' and not attrs.get('selection_justification'):
            # Check if this is L1 — we validate this in the select action instead
            pass
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        quotation  = VendorQuotation.objects.create(**validated_data)

        total = Decimal('0')
        for item_data in items_data:
            qi = VendorQuotationItem.objects.create(quotation=quotation, **item_data)
            total += qi.total_price

        quotation.total_value = total
        quotation.save(update_fields=['total_value'])
        return quotation

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            total = Decimal('0')
            for item_data in items_data:
                qi = VendorQuotationItem.objects.create(quotation=instance, **item_data)
                total += qi.total_price
            instance.total_value = total
            instance.save(update_fields=['total_value'])
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Order
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    item_code    = serializers.CharField(source='item.item_code', read_only=True)
    item_name    = serializers.CharField(source='item.name',      read_only=True)
    pending_qty  = serializers.SerializerMethodField()

    class Meta:
        model  = PurchaseOrderItem
        fields = '__all__'
        read_only_fields = ('po', 'received_qty', 'tax_amount', 'total_price')

    def get_pending_qty(self, obj):
        return obj.pending_qty


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items            = PurchaseOrderItemSerializer(many=True)
    vendor_name      = serializers.CharField(source='vendor.name',        read_only=True)
    vendor_code      = serializers.CharField(source='vendor.vendor_code', read_only=True)
    created_by_name  = serializers.SerializerMethodField()
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model  = PurchaseOrder
        fields = '__all__'
        read_only_fields = (
            'po_number', 'tenant', 'po_date',
            'sub_total', 'tax_amount', 'total_value',
            'approved_by', 'approved_at',
            'created_by', 'created_at', 'updated_at',
        )

    def get_created_by_name(self, obj):
        return _user_name(obj.created_by)

    def get_approved_by_name(self, obj):
        return _user_name(obj.approved_by)

    def _recalculate_totals(self, items):
        sub_total = Decimal('0')
        tax_total = Decimal('0')
        for i in items:
            disc   = i.unit_price * i.quantity * (1 - Decimal(str(i.discount_pct)) / 100)
            tax    = round(disc * Decimal(str(i.tax_pct)) / 100, 2)
            sub_total += disc
            tax_total += tax
        return round(sub_total, 2), round(tax_total, 2), round(sub_total + tax_total, 2)

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        request    = self.context.get('request')
        if request:
            validated_data['created_by'] = request.user

        po = PurchaseOrder.objects.create(**validated_data)
        po_items = []
        for item_data in items_data:
            pi = PurchaseOrderItem.objects.create(po=po, **item_data)
            po_items.append(pi)

        sub, tax, total = self._recalculate_totals(po_items)
        po.sub_total   = sub
        po.tax_amount  = tax
        po.total_value = total
        po.save(update_fields=['sub_total', 'tax_amount', 'total_value'])
        return po

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        if instance.status not in ('DRAFT', 'PENDING_APPROVAL'):
            raise serializers.ValidationError(
                "PO can only be edited in DRAFT or PENDING_APPROVAL status."
            )
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            po_items = []
            for item_data in items_data:
                pi = PurchaseOrderItem.objects.create(po=instance, **item_data)
                po_items.append(pi)
            sub, tax, total = self._recalculate_totals(po_items)
            instance.sub_total   = sub
            instance.tax_amount  = tax
            instance.total_value = total
            instance.save(update_fields=['sub_total', 'tax_amount', 'total_value'])
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# GRN
# ─────────────────────────────────────────────────────────────────────────────

class GRNItemSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source='item.item_code', read_only=True)
    item_name = serializers.CharField(source='item.name',      read_only=True)

    class Meta:
        model  = GRNItem
        fields = '__all__'
        read_only_fields = ('grn', 'accepted_qty', 'rejected_qty')


class GRNSerializer(serializers.ModelSerializer):
    items            = GRNItemSerializer(many=True)
    vendor_name      = serializers.CharField(source='vendor.name',        read_only=True)
    vendor_code      = serializers.CharField(source='vendor.vendor_code', read_only=True)
    warehouse_code   = serializers.CharField(source='warehouse.code',     read_only=True)
    received_by_name = serializers.SerializerMethodField()
    dc_attachment_url = serializers.SerializerMethodField()

    class Meta:
        model  = GRN
        fields = '__all__'
        read_only_fields = ('grn_number', 'tenant', 'received_by', 'created_at')

    def get_received_by_name(self, obj):
        return _user_name(obj.received_by)

    def get_dc_attachment_url(self, obj):
        request = self.context.get('request')
        if obj.dc_attachment and request:
            return request.build_absolute_uri(obj.dc_attachment.url)
        return None

    @transaction.atomic
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        request    = self.context.get('request')
        if request:
            validated_data['received_by'] = request.user

        grn = GRN.objects.create(**validated_data)
        for item_data in items_data:
            GRNItem.objects.create(grn=grn, **item_data)
        return grn

    @transaction.atomic
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        if instance.status not in ('DRAFT',):
            raise serializers.ValidationError("GRN items can only be edited in DRAFT status.")

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                GRNItem.objects.create(grn=instance, **item_data)
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# Vendor Invoice
# ─────────────────────────────────────────────────────────────────────────────

class VendorInvoiceSerializer(serializers.ModelSerializer):
    vendor_name      = serializers.CharField(source='vendor.name',        read_only=True)
    vendor_code      = serializers.CharField(source='vendor.vendor_code', read_only=True)
    po_number        = serializers.CharField(source='po.po_number',       read_only=True)
    grn_number       = serializers.CharField(source='grn.grn_number',     read_only=True)
    approved_by_name = serializers.SerializerMethodField()
    attachment_url   = serializers.SerializerMethodField()

    class Meta:
        model  = VendorInvoice
        fields = '__all__'
        read_only_fields = (
            'tenant', 'match_status', 'mismatch_notes',
            'approved_by', 'due_date', 'created_at',
        )

    def get_approved_by_name(self, obj):
        return _user_name(obj.approved_by)

    def get_attachment_url(self, obj):
        request = self.context.get('request')
        if obj.attachment and request:
            return request.build_absolute_uri(obj.attachment.url)
        return None