# apps/logistics/views.py

from __future__ import annotations
from rest_framework import status
from django.http import HttpResponse, Http404

from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError, PermissionDenied
from django.db import transaction
from django.utils import timezone
from decimal import Decimal

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser
from apps.orders.models import Order

from .models import (
    SalesInvoice,
    SalesInvoiceLineItem,
    PackagingSlip,
    DeliveryChallan,
    BackOrder,
    BackOrderLineItem,
)
from .serializers import (
    SalesInvoiceSerializer,
    PackagingSlipSerializer,
    DeliveryChallanSerializer,
    PendingInvoiceListSerializer,
    BackOrderSerializer,
    DispatchSummarySerializer,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_tenant_user(request):
    return TenantUser.objects.filter(
        user=request.user,
        tenant=request.tenant
    ).first()


def _update_order_invoice_status(order):
    """Calculate invoice status based on shipped quantities"""
    if not order or not order.oa:
        return
    
    total_qty = Decimal('0')
    shipped_qty = Decimal('0')
    
    # Get totals from OA line items
    for oa_line in order.oa.line_items.all():
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
# SalesInvoice ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class SalesInvoiceViewSet(ModelPermissionMixin, TenantModelViewSet):
    queryset = SalesInvoice.objects.all()
    serializer_class = SalesInvoiceSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'order__oa__quotation__enquiry__customer',
            'back_order'
        )

        tenant_user = _get_tenant_user(self.request)
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                order__oa__quotation__enquiry__assigned_to=self.request.user
            )

        order_id = self.request.query_params.get('order')
        if order_id:
            queryset = queryset.filter(order__id=order_id)

        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status__iexact=status_param)

        backorder_id = self.request.query_params.get('backorder')
        if backorder_id:
            queryset = queryset.filter(back_order__id=backorder_id)

        return queryset

    @action(detail=False, methods=['get'])
    def pending(self, request):
        """Returns Orders that are ready to be invoiced."""
        queryset = Order.objects.filter(
            tenant=request.tenant
        ).select_related(
            'oa__quotation__enquiry__customer'
        ).exclude(
            invoice_status='FULLY_INVOICED'
        ).filter(
            stage='DISPATCH'
        )

        tenant_user = _get_tenant_user(request)
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                oa__quotation__enquiry__assigned_to=request.user
            )

        category = request.query_params.get('category')
        if category:
            queryset = queryset.filter(order_category__iexact=category)

        po_number = request.query_params.get('po_number')
        if po_number:
            queryset = queryset.filter(
                oa__quotation__po_number__icontains=po_number
            )

        serializer = PendingInvoiceListSerializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def create_from_backorder(self, request):
        """
        Create an invoice for a specific BackOrder.
        THIS IS THE ONLY WAY TO CREATE INVOICES.
        """
        backorder_id = request.data.get('backorder_id')
        
        if not backorder_id:
            raise ValidationError({"backorder_id": "This field is required."})
        
        try:
            backorder = BackOrder.objects.select_related(
                'order__oa__quotation__enquiry__customer'
            ).get(id=backorder_id, tenant=request.tenant)
        except BackOrder.DoesNotExist:
            raise ValidationError({"backorder_id": "BackOrder not found."})
        
        # Validate status
        if backorder.status != 'PENDING':
            raise ValidationError(
                f"Cannot create invoice. BackOrder status is '{backorder.status}'. Only PENDING BackOrders can be invoiced."
            )
        
        # Check if invoice already exists
        if hasattr(backorder, 'invoice') and backorder.invoice:
            raise ValidationError("Invoice already exists for this BackOrder.")
        
        order = backorder.order
        oa = order.oa
        quotation = oa.quotation
        customer = quotation.enquiry.customer
        
        # Get contact person
        primary_poc = customer.pocs.filter(is_primary=True).first()
        if not primary_poc:
            primary_poc = customer.pocs.first()
        
        # Get additional fields from request data (these were missing!)
        po_number = request.data.get('po_number', quotation.po_number or '')
        po_date = request.data.get('po_date')
        amd_number = request.data.get('amd_number', '')
        amd_date = request.data.get('amd_date')
        location = request.data.get('location', '')
        invoice_type = request.data.get('invoice_type', '')
        
        # Get address data (allow editing)
        bill_to = request.data.get('bill_to', oa.billing_snapshot)
        ship_to = request.data.get('ship_to', oa.shipping_snapshot)
        
        # Contact details
        contact_name = request.data.get('contact_name', primary_poc.name if primary_poc else '')
        contact_number = request.data.get('contact_number', primary_poc.phone if primary_poc else '')
        contact_email = request.data.get('contact_email', primary_poc.email if primary_poc else '')
        
        # GST details
        consignee_gst = request.data.get('consignee_gst', customer.gst_number or '')
        consignor_gst = request.data.get('consignor_gst', customer.gst_number or '')
        state_code = request.data.get('state_code', customer.state or '')
        
        # Logistics details (THESE WERE MISSING!)
        date_of_removal = request.data.get('date_of_removal')
        time_of_removal = request.data.get('time_of_removal')
        mode_of_transport = request.data.get('mode_of_transport', '')
        transporter = request.data.get('transporter', '')
        vehicle_number = request.data.get('vehicle_number', '')
        lr_number = request.data.get('lr_number', '')
        payment_due_date = request.data.get('payment_due_date')
        
        with transaction.atomic():
            # Create invoice with ALL fields
            invoice = SalesInvoice.objects.create(
                tenant=request.tenant,
                order=order,
                back_order=backorder,
                po_number=po_number,
                po_date=po_date,
                amd_number=amd_number,
                amd_date=amd_date,
                location=location,
                invoice_type=invoice_type,
                bill_to=bill_to,
                ship_to=ship_to,
                contact_name=contact_name,
                contact_number=contact_number,
                contact_email=contact_email,
                consignee_gst=consignee_gst,
                consignor_gst=consignor_gst,
                state_code=state_code,
                date_of_removal=date_of_removal,
                time_of_removal=time_of_removal,
                mode_of_transport=mode_of_transport,
                transporter=transporter,
                vehicle_number=vehicle_number,
                lr_number=lr_number,
                payment_due_date=payment_due_date,
                status='DRAFT'
            )
            
            # Create invoice line items from backorder line items
            net_total = Decimal('0')
            tax_total = Decimal('0')
            
            for bo_item in backorder.line_items.select_related('oa_line_item'):
                oa_line = bo_item.oa_line_item
                qty = bo_item.quantity_dispatching
                price = bo_item.unit_price or oa_line.unit_price
                tax_pct = bo_item.tax_percent or oa_line.tax_percent or Decimal('0')
                
                line_excl = qty * price
                line_tax = round(line_excl * (tax_pct / 100), 2)
                line_total = round(line_excl + line_tax, 2)
                
                net_total += line_excl
                tax_total += line_tax
                
                # CRITICAL: Link to oa_line_item
                SalesInvoiceLineItem.objects.create(
                    invoice=invoice,
                    oa_line_item=oa_line,  # ← ADD THIS LINK
                    job_code=bo_item.job_code or oa_line.job_code,
                    customer_part_no=bo_item.customer_part_no or oa_line.customer_part_no,
                    part_no=bo_item.part_no or oa_line.part_no,
                    description=bo_item.description or oa_line.description or "",
                    hsn_code=bo_item.hsn_code or oa_line.hsn_code,
                    quantity=qty,
                    unit=bo_item.unit or oa_line.unit or 'NOS',
                    unit_price=price,
                    tax_group_code=bo_item.tax_group_code or oa_line.tax_group_code,
                    tax_percent=tax_pct,
                    tax_amount=line_tax,
                    total=line_total
                )
            
            # Update invoice totals
            invoice.net_amount = float(net_total)
            invoice.tax_amount = float(tax_total)
            invoice.grand_total = float(net_total + tax_total)
            invoice.save(update_fields=['net_amount', 'tax_amount', 'grand_total'])
            
            # Update backorder status
            backorder.status = 'INVOICED'
            backorder.save(update_fields=['status'])
        
        serializer = self.get_serializer(invoice)
        return Response({
            'message': 'Invoice created from BackOrder successfully.',
            'invoice': serializer.data,
            'backorder_number': backorder.back_order_number
        }, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='prefill-from-backorder/(?P<backorder_id>[^/.]+)')
    def prefill_from_backorder(self, request, backorder_id=None):
        """Get pre-filled data for creating invoice from BackOrder"""
        try:
            backorder = BackOrder.objects.select_related(
                'order__oa__quotation__enquiry__customer'
            ).get(id=backorder_id, tenant=request.tenant)
        except BackOrder.DoesNotExist:
            return Response({'error': 'BackOrder not found'}, status=404)
        
        if backorder.status != 'PENDING':
            return Response({'error': f'BackOrder status is {backorder.status}. Only PENDING can be invoiced.'}, status=400)
        
        if hasattr(backorder, 'invoice') and backorder.invoice:
            return Response({'error': 'Invoice already exists', 'invoice_id': str(backorder.invoice.id)}, status=400)
        
        order = backorder.order
        oa = order.oa
        quotation = oa.quotation
        customer = quotation.enquiry.customer
        
        # Get primary contact
        primary_poc = customer.pocs.filter(is_primary=True).first()
        if not primary_poc:
            primary_poc = customer.pocs.first()
        
        # Build line items from backorder
        line_items = []
        for bo_item in backorder.line_items.all():
            line_items.append({
                'oa_line_item_id': str(bo_item.oa_line_item.id),
                'quantity': float(bo_item.quantity_dispatching),
                'unit_price': float(bo_item.unit_price),
                'description': bo_item.description,
                'part_no': bo_item.part_no,
                'hsn_code': bo_item.hsn_code,
                'unit': bo_item.unit,
                'tax_percent': float(bo_item.tax_percent),
                'tax_group_code': bo_item.tax_group_code,
            })
        
        return Response({
            'backorder_id': str(backorder.id),
            'backorder_number': backorder.back_order_number,
            'order_id': str(order.id),
            'order_number': order.order_number,
            'po_number': quotation.po_number or '',
            'bill_to': oa.billing_snapshot,
            'ship_to': oa.shipping_snapshot,
            'contact': {
                'name': primary_poc.name if primary_poc else '',
                'number': primary_poc.phone if primary_poc else '',
                'email': primary_poc.email if primary_poc else '',
            },
            'gst': {
                'consignee': customer.gst_number or '',
                'consignor': customer.gst_number or '',
                'state_code': customer.state or '',
            },
            'transport_details': oa.transport_details or {},
            'line_items': line_items,
        })

    @action(detail=False, methods=['post'])
    @transaction.atomic
    def initialize(self, request):
        """
        DEPRECATED: Do not use this endpoint.
        Use create_from_backorder instead.
        
        This method remains for backward compatibility but will raise an error.
        """
        raise ValidationError({
            "error": "Direct invoice creation is disabled. Please create invoices from BackOrders using /invoices/create_from_backorder/"
        })

    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Mark invoice as CONFIRMED and update backorder status."""
        invoice = self.get_object()

        if invoice.status == 'CANCELLED':
            return Response(
                {'error': 'Cannot confirm a cancelled invoice.'},
                status=status.HTTP_400_BAD_REQUEST
            )

        invoice.status = 'CONFIRMED'
        invoice.save(update_fields=['status'])

        # Update linked backorder if exists
        if invoice.back_order:
            invoice.back_order.status = 'IN_TRANSIT'
            invoice.back_order.save(update_fields=['status'])

        # Update order invoice status
        _update_order_invoice_status(invoice.order)

        return Response({'message': 'Invoice confirmed successfully.'})

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel an invoice."""
        invoice = self.get_object()

        if invoice.status == 'CONFIRMED':
            tenant_user = _get_tenant_user(request)
            if not tenant_user or tenant_user.role != 'manager':
                raise PermissionDenied(
                    'Only managers can cancel a confirmed invoice.'
                )

        invoice.status = 'CANCELLED'
        invoice.save(update_fields=['status'])

        # Update linked backorder
        if invoice.back_order and invoice.back_order.status == 'INVOICED':
            invoice.back_order.status = 'PENDING'
            invoice.back_order.save(update_fields=['status'])

        # Re-sync order invoice status
        _update_order_invoice_status(invoice.order)

        return Response({'message': 'Invoice cancelled.'})


# ─────────────────────────────────────────────────────────────────────────────
# BackOrder ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class BackOrderViewSet(ModelPermissionMixin, TenantModelViewSet):
    queryset = BackOrder.objects.all()
    serializer_class = BackOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'order__oa__quotation__enquiry__customer',
            'invoice__delivery_challan',
        ).prefetch_related('line_items__oa_line_item')

        tenant_user = _get_tenant_user(self.request)
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                order__oa__quotation__enquiry__assigned_to=self.request.user
            )

        category = self.request.query_params.get('category')
        if category:
            queryset = queryset.filter(order__order_category__iexact=category)

        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status__iexact=status_param)

        po_number = self.request.query_params.get('po_number')
        if po_number:
            queryset = queryset.filter(
                order__oa__quotation__po_number__icontains=po_number
            )

        order_id = self.request.query_params.get('order')
        if order_id:
            queryset = queryset.filter(order__id=order_id)

        return queryset

    @action(detail=True, methods=['post'])
    def mark_in_transit(self, request, pk=None):
        """Mark backorder as in transit (dispatched)."""
        backorder = self.get_object()
        
        if backorder.status != 'INVOICED':
            raise ValidationError(f"Cannot mark as in transit. Current status: {backorder.status}")
        
        backorder.status = 'IN_TRANSIT'
        backorder.save(update_fields=['status'])
        
        return Response({
            'message': 'BackOrder marked as In Transit.',
            'status': backorder.status
        })

    @action(detail=True, methods=['post'])
    def mark_delivered(self, request, pk=None):
        """Mark backorder as delivered."""
        backorder = self.get_object()
        
        if backorder.status not in ['IN_TRANSIT', 'OUT_FOR_DELIVERY']:
            raise ValidationError(f"Cannot mark as delivered. Current status: {backorder.status}")
        
        backorder.status = 'DELIVERED'
        backorder.save(update_fields=['status'])
        
        # Update order invoice status
        _update_order_invoice_status(backorder.order)
        
        return Response({
            'message': 'BackOrder marked as Delivered.',
            'status': backorder.status
        })

    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark backorder as completed (final status)."""
        backorder = self.get_object()
        
        backorder.status = 'COMPLETED'
        backorder.save(update_fields=['status'])
        
        # Update order invoice status
        _update_order_invoice_status(backorder.order)
        
        return Response({
            'message': 'BackOrder marked as Completed.',
            'status': backorder.status
        })

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        Cancel a PENDING BackOrder.
        Only allowed if status is PENDING and no invoice exists.
        """
        backorder = self.get_object()
        
        # Check if can be cancelled
        if backorder.status != 'PENDING':
            raise ValidationError(
                f"Cannot cancel BackOrder in '{backorder.status}' status. Only PENDING can be cancelled."
            )
        
        # Check if invoice exists
        if hasattr(backorder, 'invoice') and backorder.invoice:
            raise ValidationError(
                "Cannot cancel BackOrder with linked invoice. Cancel the invoice first."
            )
        
        # Cancel the backorder
        backorder.status = 'CANCELLED'
        backorder.save(update_fields=['status'])
        
        # Update order invoice status (may change from PARTIAL to NOT_INVOICED if this was the only dispatch)
        _update_order_invoice_status(backorder.order)
        
        return Response({
            'message': 'BackOrder cancelled successfully.',
            'status': backorder.status
        })        

    @action(detail=True, methods=['post'])
    def update_tracking(self, request, pk=None):
        """Update tracking information."""
        backorder = self.get_object()
        
        tracking_status = request.data.get('tracking_status')
        current_location = request.data.get('current_location', '')
        etd = request.data.get('etd')
        tracking_remark = request.data.get('tracking_remark', '')
        
        valid_statuses = ['IN_TRANSIT', 'OUT_FOR_DELIVERY', 'DELIVERED', 'DELAYED', 'RETURNED']
        
        if tracking_status and tracking_status not in valid_statuses:
            return Response(
                {'error': f'Invalid status. Choose from {valid_statuses}'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if tracking_status:
            backorder.tracking_status = tracking_status
        if current_location:
            backorder.current_location = current_location
        if etd:
            backorder.etd = etd
        if tracking_remark:
            backorder.tracking_remark = tracking_remark
        
        # Auto-update main status if delivered
        if tracking_status == 'DELIVERED' and backorder.status != 'DELIVERED':
            backorder.status = 'DELIVERED'
        
        backorder.save()
        
        # Update order status if needed
        if tracking_status == 'DELIVERED':
            _update_order_invoice_status(backorder.order)
        
        return Response({
            'message': 'Tracking updated.',
            'tracking_status': backorder.tracking_status,
            'status': backorder.status
        })
    
    @action(detail=False, methods=['get'])
    def dispatch_summary(self, request):
        """Get dispatch summary for an order."""
        order_id = request.query_params.get('order_id')
        
        if not order_id:
            raise ValidationError({"order_id": "This field is required."})
        
        try:
            order = Order.objects.select_related('oa').get(
                id=order_id, tenant=request.tenant
            )
        except Order.DoesNotExist:
            raise ValidationError({"order_id": "Order not found."})
        
        oa = order.oa
        if not oa:
            return Response({'error': 'No OA linked to this order'}, status=400)
        
        # Calculate shipped quantities
        shipped_by_line = {}
        
        for backorder in order.back_orders.filter(status__in=['INVOICED', 'IN_TRANSIT', 'DELIVERED', 'COMPLETED']):
            for item in backorder.line_items.all():
                line_id = str(item.oa_line_item.id)
                shipped_by_line[line_id] = shipped_by_line.get(line_id, 0) + float(item.quantity_dispatching)
        
        # Build response
        line_items_summary = []
        total_shipped = 0
        total_quantity = 0
        
        for oa_line in oa.line_items.all():
            line_id = str(oa_line.id)
            total_qty = float(oa_line.quantity)
            shipped_qty = shipped_by_line.get(line_id, 0)
            remaining_qty = max(0, total_qty - shipped_qty)
            
            total_quantity += total_qty
            total_shipped += shipped_qty
            
            # Get dispatch history
            dispatches = []
            for backorder in order.back_orders.filter(
                line_items__oa_line_item=oa_line
            ).distinct():
                bo_item = backorder.line_items.get(oa_line_item=oa_line)
                
                # FIXED: Safely check for invoice
                invoice_number = None
                if hasattr(backorder, 'invoice') and backorder.invoice:
                    invoice_number = backorder.invoice.invoice_number
                
                dispatches.append({
                    'back_order_number': backorder.back_order_number,
                    'quantity': float(bo_item.quantity_dispatching),
                    'status': backorder.status,
                    'invoice_number': invoice_number,
                    'created_at': backorder.created_at.isoformat() if backorder.created_at else None
                })
            
            line_items_summary.append({
                'oa_line_item_id': line_id,
                'description': oa_line.description or '',
                'part_no': oa_line.part_no or '',
                'total_quantity': total_qty,
                'shipped_quantity': shipped_qty,
                'remaining_quantity': remaining_qty,
                'unit': oa_line.unit or 'NOS',
                'unit_price': float(oa_line.unit_price),
                'dispatches': dispatches
            })
        
        # Calculate invoice status dynamically
        if total_shipped == 0:
            invoice_status = 'NOT_INVOICED'
        elif total_shipped >= total_quantity:
            invoice_status = 'FULLY_INVOICED'
        else:
            invoice_status = 'PARTIALLY_INVOICED'
        
        return Response({
            'order_id': str(order.id),
            'order_number': order.order_number,
            'order_category': order.order_category,
            'invoice_status': invoice_status,
            'total_quantity': total_quantity,
            'shipped_quantity': total_shipped,
            'completion_percentage': round((total_shipped / total_quantity * 100), 2) if total_quantity > 0 else 0,
            'line_items': line_items_summary
        })

    @action(detail=False, methods=['get'])
    def tracking_list(self, request):
        """Order Tracking page list."""
        queryset = self.get_queryset().filter(
            status__in=['PENDING', 'INVOICED', 'IN_TRANSIT', 'OUT_FOR_DELIVERY', 'DELIVERED', 'DELAYED', 'COMPLETED']
        )
        
        transporter = request.query_params.get('transporter')
        if transporter:
            queryset = queryset.filter(
                invoice__transporter__icontains=transporter
            )
        
        tracking_status = request.query_params.get('tracking_status')
        if tracking_status:
            queryset = queryset.filter(tracking_status__iexact=tracking_status)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Dashboard stats."""
        from apps.orders.models import Order as OrderModel
        
        orders_qs = OrderModel.objects.filter(tenant=request.tenant)
        
        return Response({
            'orders_delivered': orders_qs.filter(invoice_status='FULLY_INVOICED').count(),
            'orders_in_progress': orders_qs.filter(invoice_status='PARTIALLY_INVOICED').count(),
            'export_orders': orders_qs.filter(order_category='INTERNATIONAL').count(),
            'domestic_orders': orders_qs.filter(order_category='DOMESTIC').count(),
            'in_transit': self.get_queryset().filter(status='IN_TRANSIT').count(),
            'pending_dispatch': self.get_queryset().filter(status='PENDING').count(),
        })
    
    # apps/logistics/views.py - Add to BackOrderViewSet

    @action(detail=False, methods=['get'], url_path='order-logistics/(?P<order_id>[^/.]+)')
    def order_logistics(self, request, order_id=None):
        """
        Get complete logistics view for an order including:
        - Order summary
        - All BackOrders with their status
        - All invoices with their details
        - Dispatch progress per line item
        - Complete tracking history
        """
        from apps.orders.models import Order
        from django.db.models import Sum, Q
        
        try:
            order = Order.objects.select_related(
                'oa__quotation__enquiry__customer'
            ).get(id=order_id, tenant=request.tenant)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=404)
        
        oa = order.oa
        if not oa:
            return Response({'error': 'No OA linked to this order'}, status=400)
        
        # Get all backorders for this order with their invoices
        backorders = order.back_orders.all().prefetch_related(
            'line_items__oa_line_item',
            'invoice__line_items'
        ).order_by('created_at')
        
        # Calculate dispatch summary per OA line item
        line_items_summary = []
        total_order_qty = 0
        total_dispatched_qty = 0
        
        for oa_line in oa.line_items.all():
            total_qty = float(oa_line.quantity)
            total_order_qty += total_qty
            
            # Calculate dispatched quantity from confirmed backorders
            dispatched_qty = 0
            dispatches = []
            
            for backorder in backorders:
                if backorder.status != 'CANCELLED':
                    try:
                        bo_item = backorder.line_items.get(oa_line_item=oa_line)
                        qty = float(bo_item.quantity_dispatching)
                        dispatched_qty += qty
                        total_dispatched_qty += qty
                        
                        dispatches.append({
                            'back_order_id': str(backorder.id),
                            'back_order_number': backorder.back_order_number,
                            'quantity': qty,
                            'status': backorder.status,
                            'created_at': backorder.created_at.isoformat(),
                            'invoice': {
                                'id': str(backorder.invoice.id) if hasattr(backorder, 'invoice') and backorder.invoice else None,
                                'number': backorder.invoice.invoice_number if hasattr(backorder, 'invoice') and backorder.invoice else None,
                                'status': backorder.invoice.status if hasattr(backorder, 'invoice') and backorder.invoice else None,
                            } if hasattr(backorder, 'invoice') and backorder.invoice else None
                        })
                    except BackOrderLineItem.DoesNotExist:
                        pass
            
            remaining_qty = max(0, total_qty - dispatched_qty)
            
            line_items_summary.append({
                'id': str(oa_line.id),
                'description': oa_line.description or '',
                'part_no': oa_line.part_no or '',
                'hsn_code': oa_line.hsn_code or '',
                'unit': oa_line.unit or 'NOS',
                'unit_price': float(oa_line.unit_price),
                'total_quantity': total_qty,
                'dispatched_quantity': dispatched_qty,
                'remaining_quantity': remaining_qty,
                'dispatches': dispatches
            })
        
        # Build backorders timeline
        backorders_timeline = []
        for backorder in backorders:
            # Calculate line items count and total quantity
            total_bo_qty = sum(float(item.quantity_dispatching) for item in backorder.line_items.all())
            
            backorders_timeline.append({
                'id': str(backorder.id),
                'number': backorder.back_order_number,
                'status': backorder.status,
                'reason': backorder.reason,
                'total_quantity': total_bo_qty,
                'created_at': backorder.created_at.isoformat(),
                'updated_at': backorder.updated_at.isoformat(),
                'expected_dispatch_date': backorder.expected_dispatch_date,
                'tracking': {
                    'status': backorder.tracking_status,
                    'current_location': backorder.current_location,
                    'etd': backorder.etd,
                    'remark': backorder.tracking_remark,
                },
                'invoice': {
                    'id': str(backorder.invoice.id) if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'number': backorder.invoice.invoice_number if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'date': backorder.invoice.invoice_date if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'status': backorder.invoice.status if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'amount': float(backorder.invoice.grand_total) if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'transporter': backorder.invoice.transporter if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'vehicle_number': backorder.invoice.vehicle_number if hasattr(backorder, 'invoice') and backorder.invoice else None,
                    'lr_number': backorder.invoice.lr_number if hasattr(backorder, 'invoice') and backorder.invoice else None,
                } if hasattr(backorder, 'invoice') and backorder.invoice else None
            })
        
        # Calculate overall progress
        completion_percentage = (total_dispatched_qty / total_order_qty * 100) if total_order_qty > 0 else 0
        
        # Get customer details
        customer = oa.quotation.enquiry.customer
        
        return Response({
            'order': {
                'id': str(order.id),
                'number': order.order_number,
                'category': order.order_category,
                'stage': order.stage,
                'status': order.status,
                'invoice_status': order.invoice_status,
                'total_value': float(order.total_value),
                'currency': order.currency,
                'exchange_rate': float(order.exchange_rate),
                'created_at': order.created_at.isoformat(),
            },
            'customer': {
                'id': str(customer.id),
                'name': customer.company_name,
                'gst': customer.gst_number,
                'email': customer.email,
                'phone': customer.telephone_primary,
            },
            'oa': {
                'id': str(oa.id),
                'number': oa.oa_number,
                'status': oa.status,
                'total_value': float(oa.total_value),
                'currency': oa.currency,
                'billing_address': oa.billing_snapshot,
                'shipping_address': oa.shipping_snapshot,
            },
            'summary': {
                'total_order_quantity': total_order_qty,
                'total_dispatched_quantity': total_dispatched_qty,
                'remaining_quantity': total_order_qty - total_dispatched_qty,
                'completion_percentage': round(completion_percentage, 2),
                'total_backorders': len(backorders),
                'total_invoices': len([bo for bo in backorders if hasattr(bo, 'invoice') and bo.invoice]),
                'backorders_by_status': {
                    'PENDING': len([bo for bo in backorders if bo.status == 'PENDING']),
                    'INVOICED': len([bo for bo in backorders if bo.status == 'INVOICED']),
                    'IN_TRANSIT': len([bo for bo in backorders if bo.status == 'IN_TRANSIT']),
                    'DELIVERED': len([bo for bo in backorders if bo.status == 'DELIVERED']),
                    'CANCELLED': len([bo for bo in backorders if bo.status == 'CANCELLED']),
                }
            },
            'line_items': line_items_summary,
            'backorders': backorders_timeline,
        })


# ─────────────────────────────────────────────────────────────────────────────
# PackagingSlip ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class PackagingSlipViewSet(ModelPermissionMixin, TenantModelViewSet):
    queryset = PackagingSlip.objects.all()
    serializer_class = PackagingSlipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'invoice__order__oa__quotation__enquiry__customer'
        )

        tenant_user = _get_tenant_user(self.request)
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                invoice__order__oa__quotation__enquiry__assigned_to=self.request.user
            )

        invoice_id = self.request.query_params.get('invoice')
        if invoice_id:
            queryset = queryset.filter(invoice__id=invoice_id)

        return queryset


# ─────────────────────────────────────────────────────────────────────────────
# DeliveryChallan ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class DeliveryChallanViewSet(ModelPermissionMixin, TenantModelViewSet):
    queryset = DeliveryChallan.objects.all()
    serializer_class = DeliveryChallanSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset().select_related(
            'invoice__order__oa__quotation__enquiry__customer'
        )

        tenant_user = _get_tenant_user(self.request)
        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                invoice__order__oa__quotation__enquiry__assigned_to=self.request.user
            )

        invoice_id = self.request.query_params.get('invoice')
        if invoice_id:
            queryset = queryset.filter(invoice__id=invoice_id)

        return queryset
    

# apps/logistics/invoice_pdf_view.py
#
# Generates a GST Tax Invoice PDF using the same two-stage pipeline as
# quotation/proforma PDFs:
#   Stage 1 — Playwright renders invoice.html → content PDF
#   Stage 2 — PyMuPDF overlays content PDF on tenant's letterhead PDF
#
# Wire up in apps/logistics/urls.py:
#   path('invoices/<uuid:pk>/pdf/', InvoicePDFView.as_view(), name='invoice-pdf'),
#
# Or add as a @action in SalesInvoiceViewSet:
#   @action(detail=True, methods=['get'], url_path='pdf')
#   def pdf(self, request, pk=None): ...  (see bottom of file for snippet)





from django.template.loader import render_to_string
from django.utils.dateformat import format as date_format
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView

from apps.documents.pdf_engine import generate_quotation_pdf, split_gst, amount_in_words
from apps.documents.models import TenantLetterhead
from .models import SalesInvoice


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_date(d):
    """Return DD-Mon-YYYY string or empty string."""
    if not d:
        return ""
    try:
        return date_format(d, "d M Y")
    except Exception:
        return str(d)


def _fmt_time(t):
    """Return HH:MM string or empty string."""
    if not t:
        return ""
    try:
        return t.strftime("%H:%M")
    except Exception:
        return str(t)


def _build_gst_rows(line_items, intra_state: bool, cgst_rate, sgst_rate, igst_rate):
    """
    Group line items by HSN code and build the GST breakdown rows
    used in the bottom-left summary table of the invoice.
    Each row: { hsn, taxable, cgst_rate, cgst, sgst_rate, sgst, igst_rate, igst, tax_total }
    """
    from collections import defaultdict
    buckets = defaultdict(lambda: {
        "taxable": Decimal("0"),
        "cgst": Decimal("0"),
        "sgst": Decimal("0"),
        "igst": Decimal("0"),
        "tax_total": Decimal("0"),
    })

    for item in line_items:
        hsn = getattr(item, "hsn_code", "") or "—"
        qty = Decimal(str(getattr(item, "quantity", 0) or 0))
        price = Decimal(str(getattr(item, "unit_price", 0) or 0))
        tax_pct = Decimal(str(getattr(item, "tax_percent", 0) or 0))

        taxable = (qty * price).quantize(Decimal("0.01"))
        tax_amt = (taxable * tax_pct / 100).quantize(Decimal("0.01"))

        buckets[hsn]["taxable"] += taxable
        buckets[hsn]["tax_total"] += tax_amt

        if intra_state:
            half = (tax_amt / 2).quantize(Decimal("0.01"))
            buckets[hsn]["cgst"] += half
            buckets[hsn]["sgst"] += half
        else:
            buckets[hsn]["igst"] += tax_amt

    rows = []
    for hsn, vals in buckets.items():
        rows.append({
            "hsn": hsn,
            "taxable": vals["taxable"],
            "cgst_rate": cgst_rate,
            "cgst": vals["cgst"],
            "sgst_rate": sgst_rate,
            "sgst": vals["sgst"],
            "igst_rate": igst_rate,
            "igst": vals["igst"],
            "tax_total": vals["tax_total"],
        })
    return rows


def _enrich_line_items(items, intra_state: bool, cgst_rate, sgst_rate, igst_rate):
    """
    Add per-line tax split fields to each line item for the template.
    Returns a list of dicts (safe to iterate in template).
    """
    enriched = []
    for item in items:
        qty = Decimal(str(getattr(item, "quantity", 0) or 0))
        price = Decimal(str(getattr(item, "unit_price", 0) or 0))
        tax_pct = Decimal(str(getattr(item, "tax_percent", 0) or 0))

        taxable = (qty * price).quantize(Decimal("0.01"))
        tax_amt = (taxable * tax_pct / 100).quantize(Decimal("0.01"))
        total   = (taxable + tax_amt).quantize(Decimal("0.01"))

        if intra_state:
            half = (tax_amt / 2).quantize(Decimal("0.01"))
            cgst_line = half
            sgst_line = half
            igst_line = Decimal("0")
        else:
            cgst_line = Decimal("0")
            sgst_line = Decimal("0")
            igst_line = tax_amt

        enriched.append({
            "job_code":         getattr(item, "job_code", ""),
            "customer_part_no": getattr(item, "customer_part_no", ""),
            "part_no":          getattr(item, "part_no", ""),
            "description":      getattr(item, "description", ""),
            "hsn_code":         getattr(item, "hsn_code", ""),
            "quantity":         getattr(item, "quantity", 0),
            "unit":             getattr(item, "unit", ""),
            "unit_price":       getattr(item, "unit_price", 0),
            "tax_group_code":   getattr(item, "tax_group_code", ""),
            "tax_percent":      tax_pct,
            "taxable_amount":   taxable,
            "cgst_amount":      cgst_line,
            "sgst_amount":      sgst_line,
            "igst_amount":      igst_line,
            "tax_amount":       tax_amt,
            "total":            total,
        })
    return enriched


# ─────────────────────────────────────────────────────────────────────────────
# Context builder — shared by both the APIView and the @action snippet
# ─────────────────────────────────────────────────────────────────────────────

def build_invoice_context(invoice: SalesInvoice) -> dict:
    """
    Build the full template context dict for invoice.html.
    Call this from the view and pass the result to render_to_string().
    """
    order     = invoice.order
    oa        = order.oa
    quotation = oa.quotation
    customer  = quotation.enquiry.customer

    # ── Letterhead / company info ─────────────────────────────────────────
    try:
        lh = order.tenant.letterhead
    except Exception:
        lh = None

    company_name    = (lh and lh.company_name)    or order.tenant.company_name or ""
    company_address = (lh and lh.company_address) or ""
    company_phone   = (lh and lh.company_phone)   or ""
    company_email   = (lh and lh.company_email)   or ""
    company_gstin   = (lh and lh.company_gstin)   or ""
    company_pan     = (lh and lh.company_pan)      or ""
    company_state   = (lh and lh.company_state)   or ""

    bank_name           = (lh and lh.bank_name)           or ""
    bank_account_name   = (lh and lh.bank_account_name)   or ""
    bank_branch         = (lh and lh.bank_branch)         or ""
    bank_account_number = (lh and lh.bank_account_number) or ""
    bank_ifsc           = (lh and lh.bank_ifsc)           or ""
    bank_micr           = (lh and lh.bank_micr)           or ""

    # ── GST split ─────────────────────────────────────────────────────────
    line_items_qs = invoice.line_items.all()

    customer_state = (invoice.bill_to or {}).get("state", "") or customer.state or ""

    cgst, sgst, igst, cgst_rate, sgst_rate, igst_rate = split_gst(
        line_items_qs, customer_state, company_state
    )
    intra_state = bool(cgst)

    # Net / tax / grand
    net_amount  = sum(
        Decimal(str(item.unit_price)) * Decimal(str(item.quantity))
        for item in line_items_qs
    ).quantize(Decimal("0.01"))
    tax_amount  = (cgst + sgst + igst).quantize(Decimal("0.01"))
    grand_total = (net_amount + tax_amount).quantize(Decimal("0.01"))

    # ── Per-line enrichment ───────────────────────────────────────────────
    enriched_items = _enrich_line_items(
        line_items_qs, intra_state, cgst_rate, sgst_rate, igst_rate
    )

    # ── GST summary rows (bottom-left table) ─────────────────────────────
    gst_rows = _build_gst_rows(
        line_items_qs, intra_state, cgst_rate, sgst_rate, igst_rate
    )

    # ── Back-order reference ──────────────────────────────────────────────
    back_order_number = ""
    if invoice.back_order:
        back_order_number = invoice.back_order.back_order_number

    return {
        # Core objects
        "invoice":          invoice,
        "order":            order,
        "customer":         customer,

        # Formatted dates
        "invoice_date":     _fmt_date(invoice.invoice_date),
        "payment_due_date": _fmt_date(invoice.payment_due_date),
        "po_date":          _fmt_date(invoice.po_date),
        "amd_date":         _fmt_date(invoice.amd_date),
        "date_of_removal":  _fmt_date(invoice.date_of_removal),
        "time_of_removal":  _fmt_time(invoice.time_of_removal),

        # Transport / dispatch (Rule 46 — required whenever goods move,
        # independent of whether an e-way bill is generated)
        "transporter_name":  invoice.transporter or "",
        "vehicle_number":    invoice.vehicle_number or "",
        "lr_number":         invoice.lr_number or "",
        "mode_of_transport": invoice.mode_of_transport or "",

        # Reference numbers
        "oa_number":          oa.oa_number,
        "order_number":       order.order_number,
        "back_order_number":  back_order_number,

        # Company / letterhead
        "company_name":    company_name,
        "company_address": company_address,
        "company_phone":   company_phone,
        "company_email":   company_email,
        "company_gstin":   company_gstin,
        "company_pan":     company_pan,
        "company_state":   company_state,

        # Bank
        "bank_name":           bank_name,
        "bank_account_name":   bank_account_name,
        "bank_branch":         bank_branch,
        "bank_account_number": bank_account_number,
        "bank_ifsc":           bank_ifsc,
        "bank_micr":           bank_micr,

        # Customer / addresses
        "customer_name": customer.company_name,
        "bill_to":        invoice.bill_to or {},
        "ship_to":        invoice.ship_to or {},

        # Line items (enriched dicts — NOT ORM objects)
        "line_items": enriched_items,

        # GST totals
        "net_amount":   net_amount,
        "cgst_amount":  cgst,
        "sgst_amount":  sgst,
        "igst_amount":  igst,
        "cgst_rate":    cgst_rate,
        "sgst_rate":    sgst_rate,
        "igst_rate":    igst_rate,
        "tax_amount":   tax_amount,
        "grand_total":  grand_total,
        "gst_rows":     gst_rows,

        # Amount in words
        "amount_in_words": amount_in_words(grand_total, order.currency or "INR"),
        "invoice_currency": order.currency or "INR",

        # Misc
        "prepared_by": "",  # Override with request.user.get_full_name() in view if desired
    }


# ─────────────────────────────────────────────────────────────────────────────
# APIView  (standalone — add to urls.py if not using ViewSet @action)
# ─────────────────────────────────────────────────────────────────────────────

class InvoicePDFView(APIView):
    """
    GET /api/logistics/invoices/<pk>/pdf/
    Returns the invoice as a PDF download (or inline display).

    Query param:  ?inline=1   → Content-Disposition: inline (browser renders)
                  (default)   → Content-Disposition: attachment (download)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        try:
            invoice = (
                SalesInvoice.objects
                .select_related(
                    "order__oa__quotation__enquiry__customer",
                    "order__tenant__letterhead",
                    "back_order",
                )
                .get(pk=pk, tenant=request.tenant)
            )
        except SalesInvoice.DoesNotExist:
            raise Http404

        # ── Build context & render HTML ────────────────────────────────
        ctx = build_invoice_context(invoice)
        ctx["prepared_by"] = request.user.get_full_name() or request.user.username

        html = render_to_string("documents/invoice.html", ctx, request=request)

        # ── PDF generation (Stage 1 + 2) ──────────────────────────────
        lh_file = None
        try:
            lh_file = invoice.order.tenant.letterhead.letterhead_pdf
        except Exception:
            pass

        base_url = request.build_absolute_uri("/")
        pdf_bytes = generate_quotation_pdf(html, base_url, lh_file)

        # ── HTTP response ──────────────────────────────────────────────
        inline  = request.query_params.get("inline", "0") in ("1", "true", "yes")
        disposition = "inline" if inline else "attachment"
        filename = f"{invoice.invoice_number}.pdf"

        response = HttpResponse(pdf_bytes, content_type="application/pdf")
        response["Content-Disposition"] = f'{disposition}; filename="{filename}"'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# @action snippet — paste into SalesInvoiceViewSet in apps/logistics/views.py
# ─────────────────────────────────────────────────────────────────────────────
"""
from .invoice_pdf_view import build_invoice_context
from apps.documents.pdf_engine import generate_quotation_pdf
from django.template.loader import render_to_string

# Inside SalesInvoiceViewSet:

    @action(detail=True, methods=['get'], url_path='pdf')
    def pdf(self, request, pk=None):
        invoice = self.get_object()

        ctx = build_invoice_context(invoice)
        ctx['prepared_by'] = request.user.get_full_name() or request.user.username

        html = render_to_string('documents/invoice.html', ctx, request=request)

        lh_file = None
        try:
            lh_file = invoice.order.tenant.letterhead.letterhead_pdf
        except Exception:
            pass

        pdf_bytes = generate_quotation_pdf(html, request.build_absolute_uri('/'), lh_file)

        inline = request.query_params.get('inline', '0') in ('1', 'true', 'yes')
        disposition = 'inline' if inline else 'attachment'

        response = HttpResponse(pdf_bytes, content_type='application/pdf')
        response['Content-Disposition'] = f'{disposition}; filename="{invoice.invoice_number}.pdf"'
        return response
"""