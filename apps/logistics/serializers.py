# apps/logistics/serializers.py

from rest_framework import serializers
from django.db import transaction
from django.utils import timezone
from decimal import Decimal
from .models import (
    SalesInvoice,
    SalesInvoiceLineItem,
    PackagingSlip,
    PackagingItem,
    DeliveryChallan,
    BackOrder,
    BackOrderLineItem,
)
from apps.orders.models import OALineItem


# ─────────────────────────────────────────────────────────────────────────────
# SalesInvoiceLineItem
# ─────────────────────────────────────────────────────────────────────────────

class SalesInvoiceLineItemSerializer(serializers.ModelSerializer):
    # Add read-only display fields
    product_description = serializers.CharField(source='oa_line_item.description', read_only=True)
    part_number = serializers.CharField(source='oa_line_item.part_no', read_only=True)
    
    class Meta:
        model = SalesInvoiceLineItem
        exclude = ('invoice',)
        read_only_fields = ('oa_line_item',)  # Set by system, not user

# ─────────────────────────────────────────────────────────────────────────────
# BackOrderLineItem Serializer
# ─────────────────────────────────────────────────────────────────────────────

class BackOrderLineItemSerializer(serializers.ModelSerializer):
    """Serializer for items in a dispatch request"""
    
    # Read-only display fields
    line_item_description = serializers.CharField(source='oa_line_item.description', read_only=True)
    product_name = serializers.CharField(source='oa_line_item.product_name_snapshot', read_only=True)
    available_quantity = serializers.SerializerMethodField()
    
    class Meta:
        model = BackOrderLineItem
        fields = [
            'id', 'oa_line_item', 'line_item_description', 'product_name',
            'quantity_dispatching', 'available_quantity',
            'job_code', 'customer_part_no', 'part_no',
            'description', 'hsn_code', 'unit', 'unit_price',
            'tax_group_code', 'tax_percent'
        ]
    
    def get_available_quantity(self, obj):
        """Calculate remaining quantity for this OA line item"""
        if not obj.oa_line_item_id:
            return 0
        
        oa_line = obj.oa_line_item
        total_qty = oa_line.quantity  # Keep as Decimal
        
        # Sum already dispatched from confirmed backorders (EXCLUDING current)
        from django.db import models
        from decimal import Decimal
        
        dispatched_result = BackOrderLineItem.objects.filter(
            oa_line_item=oa_line,
            back_order__status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED'],
            back_order__isnull=False
        ).exclude(back_order=obj.back_order).aggregate(
            total=models.Sum('quantity_dispatching')
        )['total']
        
        dispatched = dispatched_result or Decimal('0')
        
        remaining = total_qty - dispatched
        # Convert to float for JSON serialization if needed, but return as Decimal
        return float(remaining) if remaining else 0
        
        
    # In apps/logistics/serializers.py - BackOrderLineItemSerializer.validate()

    def validate(self, data):
        """Prevent overshipping AND auto-populate missing tax fields"""
        oa_line_item = data.get('oa_line_item')
        quantity = data.get('quantity_dispatching')
        
        if not oa_line_item or not quantity:
            return data
        
        # FIX: Keep as Decimal, don't convert to float
        total_qty = oa_line_item.quantity  # This should already be Decimal
        
        # Get already dispatched (also Decimal)
        from django.db import models
        dispatched_result = BackOrderLineItem.objects.filter(
            oa_line_item=oa_line_item,
            back_order__status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED']
        ).aggregate(total=models.Sum('quantity_dispatching'))['total']
        
        dispatched = dispatched_result or Decimal('0')
        
        # FIX: Ensure quantity is Decimal
        if isinstance(quantity, (int, float)):
            quantity = Decimal(str(quantity))
        
        remaining = total_qty - dispatched
        
        if quantity > remaining:
            raise serializers.ValidationError(
                f"Cannot dispatch {quantity}. Only {remaining} remaining for {oa_line_item.description or oa_line_item.product_name_snapshot}"
            )
        
        # NEW: Auto-populate tax fields from OALineItem if not provided
        if 'tax_percent' not in data or data.get('tax_percent') == 0:
            data['tax_percent'] = oa_line_item.tax_percent
        
        if 'tax_group_code' not in data or not data.get('tax_group_code'):
            data['tax_group_code'] = oa_line_item.tax_group_code
        
        if 'unit_price' not in data or not data.get('unit_price'):
            data['unit_price'] = oa_line_item.unit_price
        
        if 'unit' not in data or not data.get('unit'):
            data['unit'] = oa_line_item.unit
        
        return data

# ─────────────────────────────────────────────────────────────────────────────
# BackOrder Serializer
# ─────────────────────────────────────────────────────────────────────────────

# Replace the existing BackOrderSerializer with this enhanced version

class BackOrderSerializer(serializers.ModelSerializer):
    """Main BackOrder serializer with nested line items and full context"""
    
    line_items = BackOrderLineItemSerializer(many=True, required=False)
    
    # Read-only display fields from Order and OA
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    invoice_number = serializers.CharField(source='invoice.invoice_number', read_only=True, allow_null=True)
    total_quantity = serializers.SerializerMethodField()
    customer_name = serializers.SerializerMethodField()
    
    # ADD THESE MISSING FIELDS
    entity_name = serializers.SerializerMethodField()
    po_number = serializers.SerializerMethodField()
    oa_number = serializers.SerializerMethodField()
    order_category = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    exchange_rate = serializers.SerializerMethodField()
    order_stage = serializers.SerializerMethodField()
    location = serializers.SerializerMethodField()
    shipping_city = serializers.SerializerMethodField()
    
    # Dispatch progress fields
    order_total_quantity = serializers.SerializerMethodField()
    total_shipped_before = serializers.SerializerMethodField()
    remaining_after = serializers.SerializerMethodField()
    total_dispatching_quantity = serializers.SerializerMethodField()
    
    # Invoice status from order
    invoice_status = serializers.SerializerMethodField()
    
    # Created by info
    created_by_name = serializers.SerializerMethodField()
    
    # Invoices linked through back_order
    invoices = serializers.SerializerMethodField()
    
    class Meta:
        model = BackOrder
        fields = '__all__'
        read_only_fields = (
            'tenant', 'back_order_number', 'created_at', 'updated_at'
        )
    
    def get_total_quantity(self, obj):
        """Sum of all line item quantities in this backorder"""
        total = sum(float(item.quantity_dispatching) for item in obj.line_items.all())
        return total
    
    def get_customer_name(self, obj):
        try:
            return obj.order.oa.quotation.enquiry.customer.company_name
        except Exception:
            return ''
    
    def get_entity_name(self, obj):
        """Get customer/entity name"""
        try:
            return obj.order.oa.quotation.enquiry.customer.company_name
        except Exception:
            return ''
    
    def get_po_number(self, obj):
        """Get PO number from quotation"""
        try:
            return obj.order.oa.quotation.po_number or ''
        except Exception:
            return ''
    
    def get_oa_number(self, obj):
        """Get OA number"""
        try:
            return obj.order.oa.oa_number
        except Exception:
            return ''
    
    def get_order_category(self, obj):
        """Get order category (DOMESTIC/INTERNATIONAL)"""
        try:
            return obj.order.order_category
        except Exception:
            return ''
    
    def get_currency(self, obj):
        """Get currency"""
        try:
            return obj.order.currency
        except Exception:
            return 'INR'
    
    def get_exchange_rate(self, obj):
        """Get exchange rate"""
        try:
            return float(obj.order.exchange_rate) if obj.order.exchange_rate else 1.0
        except Exception:
            return 1.0
    
    def get_order_stage(self, obj):
        """Get order stage (PLANNING, ENGINEERING, etc.)"""
        try:
            return obj.order.stage
        except Exception:
            return ''
    
    def get_location(self, obj):
        """Get location from shipping address"""
        try:
            shipping = obj.order.oa.shipping_snapshot
            if shipping and shipping.get('address_line'):
                return shipping.get('address_line', '')
        except Exception:
            pass
        return ''
    
    def get_shipping_city(self, obj):
        """Get shipping city"""
        # You may need to extract city from address or add a city field
        return self.get_location(obj)
    
    def get_order_total_quantity(self, obj):
        """Get total quantity from OA line items"""
        try:
            oa = obj.order.oa
            total = sum(float(item.quantity) for item in oa.line_items.all())
            return total
        except Exception:
            return 0
    
    def get_total_shipped_before(self, obj):
        """Get total quantity shipped before this dispatch"""
        try:
            order = obj.order
            total_shipped = 0
            # Sum from all backorders except current one
            for bo in order.back_orders.filter(
                status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED']
            ).exclude(id=obj.id):
                for item in bo.line_items.all():
                    total_shipped += float(item.quantity_dispatching)
            return total_shipped
        except Exception:
            return 0
    
    def get_remaining_after(self, obj):
        """Get remaining quantity after this dispatch"""
        try:
            total = self.get_order_total_quantity(obj)
            shipped_before = self.get_total_shipped_before(obj)
            this_dispatch = self.get_total_quantity(obj)
            remaining = total - (shipped_before + this_dispatch)
            return max(0, remaining)
        except Exception:
            return 0
    
    def get_total_dispatching_quantity(self, obj):
        """Alias for total_quantity"""
        return self.get_total_quantity(obj)
    
    def get_invoice_status(self, obj):
        """Get invoice status from order"""
        try:
            return obj.order.invoice_status
        except Exception:
            return 'NOT_INVOICED'
    
    def get_created_by_name(self, obj):
        """Get created by user name"""
        # BackOrder model doesn't have created_by field
        # You may need to add this field to the model
        return ''
    
    def get_invoices(self, obj):
        """Get invoices linked to this backorder"""
        try:
            if hasattr(obj, 'invoice') and obj.invoice:
                from .serializers import SalesInvoiceSerializer
                # Always return a list, even for single invoice
                return [SalesInvoiceSerializer(obj.invoice).data]
        except Exception as e:
            # Log error if needed
            pass
        return []
    
    # In apps/logistics/serializers.py

    def create(self, validated_data):
        """Create BackOrder with line items with validation"""
        line_items_data = validated_data.pop('line_items', [])
        
        if not line_items_data:
            raise serializers.ValidationError({"line_items": "At least one line item is required"})
        
        # CRITICAL: Get tenant from context
        request = self.context.get('request')
        if request and hasattr(request, 'tenant'):
            validated_data['tenant'] = request.tenant
        
        # CRITICAL: Ensure order is a UUID string or instance
        from apps.orders.models import Order
        
        order_id = validated_data.get('order')
        if order_id and isinstance(order_id, str):
            try:
                order = Order.objects.get(id=order_id)
                validated_data['order'] = order
            except Order.DoesNotExist:
                raise serializers.ValidationError({"order": f"Order with id {order_id} not found"})
        
        # Validate each line item quantity doesn't exceed available
        from decimal import Decimal
        
        # NEW: First, enrich line items with OALineItem data
        enriched_line_items = []
        for item_data in line_items_data:
            oa_line = item_data.get('oa_line_item')
            qty = item_data.get('quantity_dispatching')
            
            if not oa_line or not qty:
                continue
                
            # Convert to Decimal if needed
            if isinstance(qty, (int, float)):
                qty = Decimal(str(qty))
            
            # CRITICAL: Auto-populate fields from OALineItem if not provided
            # This ensures tax_percent, tax_group_code, unit_price, etc. are set
            
            if 'unit_price' not in item_data or not item_data['unit_price']:
                item_data['unit_price'] = oa_line.unit_price
            
            if 'unit' not in item_data or not item_data['unit']:
                item_data['unit'] = oa_line.unit or 'NOS'
            
            if 'description' not in item_data or not item_data['description']:
                item_data['description'] = oa_line.description or ''
            
            if 'part_no' not in item_data or not item_data['part_no']:
                item_data['part_no'] = oa_line.part_no or ''
            
            if 'hsn_code' not in item_data or not item_data['hsn_code']:
                item_data['hsn_code'] = oa_line.hsn_code or ''
            
            if 'tax_percent' not in item_data or item_data['tax_percent'] == 0:
                item_data['tax_percent'] = oa_line.tax_percent or Decimal('0')
            
            if 'tax_group_code' not in item_data or not item_data['tax_group_code']:
                item_data['tax_group_code'] = oa_line.tax_group_code or ''
            
            if 'job_code' not in item_data or not item_data['job_code']:
                item_data['job_code'] = oa_line.job_code or ''
            
            if 'customer_part_no' not in item_data or not item_data['customer_part_no']:
                item_data['customer_part_no'] = oa_line.customer_part_no or ''
            
            # Calculate available quantity
            total_qty = oa_line.quantity
            
            already_dispatched_result = BackOrderLineItem.objects.filter(
                oa_line_item=oa_line,
                back_order__status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED']
            ).aggregate(total=models.Sum('quantity_dispatching'))['total']
            
            already_dispatched = already_dispatched_result or Decimal('0')
            available = total_qty - already_dispatched
            
            if qty > available:
                raise serializers.ValidationError(
                    f"Cannot dispatch {qty} of '{oa_line.description}'. Only {available} remaining."
                )
            
            enriched_line_items.append(item_data)
        
        # Create the backorder
        back_order = BackOrder.objects.create(**validated_data)
        
        # Create line items with enriched data
        for item_data in enriched_line_items:
            # Ensure quantity is properly set as Decimal
            if 'quantity_dispatching' in item_data:
                qty = item_data['quantity_dispatching']
                if isinstance(qty, (int, float)):
                    item_data['quantity_dispatching'] = Decimal(str(qty))
            
            BackOrderLineItem.objects.create(back_order=back_order, **item_data)
        
        return back_order
    
    def update(self, instance, validated_data):
        """Only allow updates when PENDING"""
        if instance.status != 'PENDING':
            raise serializers.ValidationError(
                f"Cannot update BackOrder in '{instance.status}' status. Only PENDING can be edited."
            )
        
        line_items_data = validated_data.pop('line_items', None)
        
        # Update scalar fields
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        # Replace line items if provided
        if line_items_data is not None:
            instance.line_items.all().delete()
            for item_data in line_items_data:
                BackOrderLineItem.objects.create(back_order=instance, **item_data)
        
        return instance

# ─────────────────────────────────────────────────────────────────────────────
# PackagingItem
# ─────────────────────────────────────────────────────────────────────────────

class PackagingItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = PackagingItem
        exclude = ('packaging_slip',)


# ─────────────────────────────────────────────────────────────────────────────
# PackagingSlip
# ─────────────────────────────────────────────────────────────────────────────

class PackagingSlipSerializer(serializers.ModelSerializer):
    items = PackagingItemSerializer(many=True, required=False)
    
    # Read-only display fields
    invoice_number = serializers.CharField(source='invoice.invoice_number', read_only=True)
    invoice_date = serializers.DateField(source='invoice.invoice_date', read_only=True)
    po_number = serializers.CharField(source='invoice.po_number', read_only=True)
    po_date = serializers.DateField(source='invoice.po_date', read_only=True)
    
    class Meta:
        model = PackagingSlip
        fields = '__all__'
        read_only_fields = (
            'tenant', 'packing_list_number', 'created_at',
        )
    
    def create(self, validated_data):
        items_data = validated_data.pop('items', [])
        
        slip = PackagingSlip.objects.create(**validated_data)
        
        for idx, item in enumerate(items_data, start=1):
            item.setdefault('serial_number', idx)
            item['packing_list_number'] = slip.packing_list_number
            PackagingItem.objects.create(packaging_slip=slip, **item)
        
        return slip
    
    def update(self, instance, validated_data):
        items_data = validated_data.pop('items', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        
        if items_data is not None:
            instance.items.all().delete()
            for idx, item in enumerate(items_data, start=1):
                item.setdefault('serial_number', idx)
                item['packing_list_number'] = instance.packing_list_number
                PackagingItem.objects.create(packaging_slip=instance, **item)
        
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# DeliveryChallan
# ─────────────────────────────────────────────────────────────────────────────

class DeliveryChallanSerializer(serializers.ModelSerializer):
    # Read-only display fields
    invoice_number = serializers.CharField(source='invoice.invoice_number', read_only=True)
    
    class Meta:
        model = DeliveryChallan
        fields = '__all__'
        read_only_fields = (
            'tenant', 'challan_number', 'created_at',
        )
    
    def create(self, validated_data):
        invoice = validated_data.get('invoice')
        
        # Auto-populate bill_to and ship_to from invoice if not provided
        if not validated_data.get('bill_to') and invoice:
            validated_data['bill_to'] = invoice.bill_to
        if not validated_data.get('ship_to') and invoice:
            validated_data['ship_to'] = invoice.ship_to
        
        return DeliveryChallan.objects.create(**validated_data)
    
    def update(self, instance, validated_data):
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance


# ─────────────────────────────────────────────────────────────────────────────
# SalesInvoice (with BackOrder support)
# ─────────────────────────────────────────────────────────────────────────────

class SalesInvoiceSerializer(serializers.ModelSerializer):
    line_items = SalesInvoiceLineItemSerializer(many=True, required=False)
    
        # ADD THESE TWO LINES - to include packaging slip and delivery challan
    packaging_slip = PackagingSlipSerializer(read_only=True)
    delivery_challan = DeliveryChallanSerializer(read_only=True)

    # Read-only context fields
    order_number = serializers.CharField(source='order.order_number', read_only=True)
    oa_number = serializers.CharField(source='order.oa.oa_number', read_only=True)
    quotation_number = serializers.CharField(source='order.oa.quotation.quotation_number', read_only=True)
    customer_name = serializers.CharField(source='order.oa.quotation.enquiry.customer.company_name', read_only=True)
    customer_gst = serializers.CharField(source='order.oa.quotation.enquiry.customer.gst_number', read_only=True)
    order_category = serializers.CharField(source='order.order_category', read_only=True)
    
    # BackOrder info
    back_order_number = serializers.CharField(source='back_order.back_order_number', read_only=True)
    
    class Meta:
        model = SalesInvoice
        fields = '__all__'
        read_only_fields = (
            'tenant', 'invoice_number', 'created_at', 'updated_at',
        )
    
    def _recalculate_totals(self, line_items_data):
        net = Decimal('0')
        tax = Decimal('0')
        for item in line_items_data:
            qty = Decimal(str(item.get('quantity') or 0))
            price = Decimal(str(item.get('unit_price') or 0))
            tax_pct = Decimal(str(item.get('tax_percent') or 0))
            line_excl = qty * price
            line_tax = round(line_excl * (tax_pct / 100), 2)
            item['tax_amount'] = float(line_tax)
            item['total'] = float(line_excl + line_tax)
            net += line_excl
            tax += line_tax
        return float(net), float(tax), float(net + tax)
    
    def create(self, validated_data):
        line_items_data = validated_data.pop('line_items', [])
        
        net, tax, grand = self._recalculate_totals(line_items_data)
        validated_data['net_amount'] = net
        validated_data['tax_amount'] = tax
        validated_data['grand_total'] = grand
        
        invoice = SalesInvoice.objects.create(**validated_data)
        
        for item in line_items_data:
            SalesInvoiceLineItem.objects.create(invoice=invoice, **item)
        
        # Update order invoice status
        self._update_order_invoice_status(invoice.order)
        
        return invoice
    
    def update(self, instance, validated_data):
        line_items_data = validated_data.pop('line_items', None)
        
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        
        if line_items_data is not None:
            net, tax, grand = self._recalculate_totals(line_items_data)
            instance.net_amount = net
            instance.tax_amount = tax
            instance.grand_total = grand
        
        instance.save()
        
        if line_items_data is not None:
            instance.line_items.all().delete()
            for item in line_items_data:
                SalesInvoiceLineItem.objects.create(invoice=instance, **item)
        
        self._update_order_invoice_status(instance.order)
        
        return instance
    
    def _update_order_invoice_status(self, order):
        """
        Keeps Order.invoice_status in sync after every invoice save.
        Now checks actual shipped quantities via BackOrders.
        """
        if not order:
            return
        
        oa = order.oa
        if not oa:
            order.invoice_status = 'NOT_INVOICED'
            order.save(update_fields=['invoice_status'])
            return
        
        total_qty = Decimal('0')
        shipped_qty = Decimal('0')
        
        # Get totals from OA line items
        for oa_line in oa.line_items.all():
            total_qty += oa_line.quantity
        
        # Get shipped from confirmed backorders
        for backorder in order.back_orders.filter(status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED']):
            for item in backorder.line_items.all():
                shipped_qty += item.quantity_dispatching
        
        if shipped_qty == 0:
            order.invoice_status = 'NOT_INVOICED'
        elif shipped_qty >= total_qty:
            order.invoice_status = 'FULLY_INVOICED'
        else:
            order.invoice_status = 'PARTIALLY_INVOICED'
        
        order.save(update_fields=['invoice_status'])


# ─────────────────────────────────────────────────────────────────────────────
# Pending Invoice List Serializer
# ─────────────────────────────────────────────────────────────────────────────

class PendingInvoiceListSerializer(serializers.Serializer):
    """Read-only serializer. Source is Order queryset."""
    order_id = serializers.UUIDField(source='id')
    order_number = serializers.CharField()
    order_category = serializers.CharField()
    invoice_status = serializers.CharField()
    stage = serializers.CharField()
    status = serializers.CharField()
    
    po_number = serializers.SerializerMethodField()
    entity_name = serializers.SerializerMethodField()
    net_amount = serializers.SerializerMethodField()
    currency = serializers.SerializerMethodField()
    
    def get_po_number(self, obj):
        try:
            return obj.oa.quotation.po_number or ''
        except Exception:
            return ''
    
    def get_entity_name(self, obj):
        try:
            return obj.oa.quotation.enquiry.customer.company_name
        except Exception:
            return ''
    
    def get_net_amount(self, obj):
        try:
            return str(obj.oa.total_value)
        except Exception:
            return '0'
    
    def get_currency(self, obj):
        try:
            return obj.oa.currency
        except Exception:
            return 'INR'


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch Summary Serializer (for Order)
# ─────────────────────────────────────────────────────────────────────────────

class DispatchSummaryLineSerializer(serializers.Serializer):
    oa_line_item_id = serializers.CharField()
    description = serializers.CharField()
    part_no = serializers.CharField()
    total_quantity = serializers.FloatField()
    shipped_quantity = serializers.FloatField()
    remaining_quantity = serializers.FloatField()
    unit = serializers.CharField()
    unit_price = serializers.FloatField()
    dispatches = serializers.ListField(child=serializers.DictField())


class DispatchSummarySerializer(serializers.Serializer):
    order_id = serializers.UUIDField()
    order_number = serializers.CharField()
    order_category = serializers.CharField()
    invoice_status = serializers.CharField()
    total_quantity = serializers.FloatField()
    shipped_quantity = serializers.FloatField()
    completion_percentage = serializers.FloatField()
    line_items = DispatchSummaryLineSerializer(many=True)


# Import for model aggregation
from django.db import models