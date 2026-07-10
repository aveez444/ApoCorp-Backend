# apps/purchase/views.py

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import (
    PurchaseIndent, RFQ, RFQVendor,
    VendorQuotation, PurchaseOrder, GRN, VendorInvoice,
)
from .serializers import (
    PurchaseIndentSerializer, RFQSerializer,
    VendorQuotationSerializer, PurchaseOrderSerializer,
    GRNSerializer, VendorInvoiceSerializer,
)
from . import services


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_tenant_user(request):
    return TenantUser.objects.filter(
        user=request.user, tenant=request.tenant
    ).first()


def _require_manager(request):
    tu = _get_tenant_user(request)
    if not tu or tu.role != 'manager':
        raise PermissionDenied("Only managers can perform this action.")


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Indent (MRN)
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseIndentViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Filters: ?status= ?indent_type= ?department= ?raised_by=<user_id>
    """
    serializer_class   = PurchaseIndentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = PurchaseIndent.objects.filter(
            tenant=self.request.tenant
        ).select_related('raised_by', 'approved_by').prefetch_related('items__item')

        params = self.request.query_params
        tu = _get_tenant_user(self.request)

        # Employees only see their own indents
        if tu and tu.role == 'employee':
            qs = qs.filter(raised_by=self.request.user)

        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('indent_type'):
            qs = qs.filter(indent_type=params['indent_type'].upper())
        if params.get('department'):
            qs = qs.filter(department__icontains=params['department'])

        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """DRAFT → SUBMITTED"""
        indent = self.get_object()
        if indent.status != 'DRAFT':
            raise ValidationError({"detail": "Only DRAFT indents can be submitted."})
        indent.status = 'SUBMITTED'
        indent.save(update_fields=['status'])
        return Response(PurchaseIndentSerializer(indent, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """SUBMITTED → APPROVED. Manager only."""
        _require_manager(request)
        indent = self.get_object()
        if indent.status != 'SUBMITTED':
            raise ValidationError({"detail": "Only SUBMITTED indents can be approved."})
        indent.status      = 'APPROVED'
        indent.approved_by = request.user
        indent.approved_at = timezone.now()
        indent.save(update_fields=['status', 'approved_by', 'approved_at'])
        return Response(PurchaseIndentSerializer(indent, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        indent = self.get_object()
        if indent.status in ('FULFILLED', 'CANCELLED'):
            raise ValidationError({"detail": f"Cannot cancel an indent in {indent.status} status."})
        indent.status = 'CANCELLED'
        indent.save(update_fields=['status'])
        return Response({"message": f"Indent {indent.indent_number} cancelled."})


# ─────────────────────────────────────────────────────────────────────────────
# RFQ
# ─────────────────────────────────────────────────────────────────────────────

class RFQViewSet(ModelPermissionMixin, TenantModelViewSet):
    serializer_class   = RFQSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = RFQ.objects.filter(
            tenant=self.request.tenant
        ).select_related('created_by', 'indent').prefetch_related(
            'items__item', 'rfq_vendors__vendor'
        )
        if self.request.query_params.get('status'):
            qs = qs.filter(status=self.request.query_params['status'].upper())
        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'])
    def send(self, request, pk=None):
        """
        DRAFT → SENT.
        Validates minimum vendor count (PurchaseSettings.rfq_min_vendors).
        Marks all RFQVendor records with sent_at.
        """
        rfq = self.get_object()
        if rfq.status != 'DRAFT':
            raise ValidationError({"detail": "Only DRAFT RFQs can be sent."})

        # Check minimum vendors
        from apps.vendors.models import PurchaseSettings
        settings = PurchaseSettings.objects.filter(tenant=request.tenant).first()
        min_vendors = settings.rfq_min_vendors if settings else 3

        vendor_count = rfq.rfq_vendors.count()
        if vendor_count < min_vendors:
            raise ValidationError({
                "detail": f"At least {min_vendors} vendors required before sending. "
                          f"Currently {vendor_count} added."
            })

        now = timezone.now()
        rfq.rfq_vendors.all().update(sent_at=now, status='SENT')
        rfq.status = 'SENT'
        rfq.save(update_fields=['status'])

        # TODO: trigger email to each vendor with RFQ PDF attachment
        # from apps.documents.tasks import send_rfq_email
        # send_rfq_email.delay(rfq.id)

        return Response(RFQSerializer(rfq, context={'request': request}).data)

    @action(detail=True, methods=['get'], url_path='comparative-statement')
    def comparative_statement(self, request, pk=None):
        """
        GET /purchase/rfqs/{id}/comparative-statement/
        Returns vendor quotations side-by-side with L1/L2/L3 ranking per line item.
        """
        rfq = self.get_object()
        data = services.get_comparative_statement(rfq)
        return Response(data)


# ─────────────────────────────────────────────────────────────────────────────
# Vendor Quotation
# ─────────────────────────────────────────────────────────────────────────────

class VendorQuotationViewSet(ModelPermissionMixin, TenantModelViewSet):
    serializer_class   = VendorQuotationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = VendorQuotation.objects.filter(
            tenant=self.request.tenant
        ).select_related('vendor', 'rfq').prefetch_related('items__rfq_item__item')

        if self.request.query_params.get('rfq'):
            qs = qs.filter(rfq__id=self.request.query_params['rfq'])
        if self.request.query_params.get('status'):
            qs = qs.filter(status=self.request.query_params['status'].upper())

        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'])
    def select(self, request, pk=None):
        """
        Select this quotation. Manager only.
        If not L1 (lowest price), selection_justification is mandatory.
        Marks all other quotations for the same RFQ as REJECTED.
        """
        _require_manager(request)
        quotation = self.get_object()
        rfq       = quotation.rfq

        # Check if L1
        cheapest = (
            VendorQuotation.objects
            .filter(rfq=rfq, status__in=['RECEIVED', 'SHORTLISTED'])
            .order_by('total_value')
            .first()
        )
        is_l1 = (cheapest and cheapest.id == quotation.id)

        if not is_l1:
            justification = (request.data.get('selection_justification') or '').strip()
            if not justification:
                raise ValidationError({
                    "selection_justification":
                        "Justification is required when selecting a non-L1 vendor."
                })
            quotation.selection_justification = justification

        # Reject others
        VendorQuotation.objects.filter(rfq=rfq).exclude(id=quotation.id).update(
            status='REJECTED', is_selected=False
        )

        quotation.status      = 'SELECTED'
        quotation.is_selected = True
        quotation.save(update_fields=['status', 'is_selected', 'selection_justification'])

        rfq.status = 'CLOSED'
        rfq.save(update_fields=['status'])

        return Response(VendorQuotationSerializer(quotation, context={'request': request}).data)


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Order
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseOrderViewSet(ModelPermissionMixin, TenantModelViewSet):
    serializer_class   = PurchaseOrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = PurchaseOrder.objects.filter(
            tenant=self.request.tenant
        ).select_related('vendor', 'rfq', 'quotation', 'created_by', 'approved_by'
                         ).prefetch_related('items__item')

        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('vendor'):
            qs = qs.filter(vendor__id=params['vendor'])

        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='submit-for-approval')
    def submit_for_approval(self, request, pk=None):
        """DRAFT → PENDING_APPROVAL"""
        po = self.get_object()
        if po.status != 'DRAFT':
            raise ValidationError({"detail": "Only DRAFT POs can be submitted for approval."})
        if not po.items.exists():
            raise ValidationError({"detail": "Cannot submit a PO with no line items."})
        po.status = 'PENDING_APPROVAL'
        po.save(update_fields=['status'])
        return Response(PurchaseOrderSerializer(po, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """PENDING_APPROVAL → APPROVED. Manager only (acting as GM for now)."""
        _require_manager(request)
        po = self.get_object()
        if po.status != 'PENDING_APPROVAL':
            raise ValidationError({"detail": "Only PENDING_APPROVAL POs can be approved."})
        po.status      = 'APPROVED'
        po.approved_by = request.user
        po.approved_at = timezone.now()
        po.save(update_fields=['status', 'approved_by', 'approved_at'])
        return Response(PurchaseOrderSerializer(po, context={'request': request}).data)

    @action(detail=True, methods=['post'], url_path='send-to-vendor')
    def send_to_vendor(self, request, pk=None):
        """APPROVED → SENT. Triggers email with PO PDF."""
        _require_manager(request)
        po = self.get_object()
        if po.status != 'APPROVED':
            raise ValidationError({"detail": "Only APPROVED POs can be sent."})
        po.status = 'SENT'
        po.save(update_fields=['status'])

        # TODO: trigger email with PDF
        # from apps.documents.tasks import send_po_email
        # send_po_email.delay(po.id)

        return Response(PurchaseOrderSerializer(po, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        _require_manager(request)
        po     = self.get_object()
        reason = (request.data.get('reason') or '').strip()
        if po.status in ('RECEIVED', 'CANCELLED'):
            raise ValidationError({"detail": f"Cannot cancel a PO in {po.status} status."})
        if not reason:
            raise ValidationError({"reason": "Cancellation reason is required."})
        po.status           = 'CANCELLED'
        po.cancelled_reason = reason
        po.save(update_fields=['status', 'cancelled_reason'])
        return Response({"message": f"PO {po.po_number} cancelled."})

    @action(detail=True, methods=['get'])
    def pdf(self, request, pk=None):
        """
        GET /purchase/purchase-orders/{id}/pdf/
        Generates PO PDF via pdf_engine and returns as download.
        """
        po = self.get_object()
        try:
            from django.template.loader import render_to_string
            from apps.documents.pdf_engine import generate_quotation_pdf
            from django.http import HttpResponse

            context = {
                'po': po,
                'items': po.items.select_related('item').all(),
                'vendor': po.vendor,
                'tenant': request.tenant,
            }
            html         = render_to_string('documents/purchase_order.html', context)
            base_url     = request.build_absolute_uri('/')
            letterhead   = getattr(request.tenant, 'letterhead_pdf', None)
            pdf_bytes    = generate_quotation_pdf(html, base_url, letterhead)

            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = f'attachment; filename="{po.po_number}.pdf"'
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# GRN
# ─────────────────────────────────────────────────────────────────────────────

class GRNViewSet(ModelPermissionMixin, TenantModelViewSet):
    serializer_class   = GRNSerializer
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def get_queryset(self):
        qs = GRN.objects.filter(
            tenant=self.request.tenant
        ).select_related('po', 'vendor', 'warehouse', 'received_by'
                         ).prefetch_related('items__item', 'items__po_item')

        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('vendor'):
            qs = qs.filter(vendor__id=params['vendor'])
        if params.get('po'):
            qs = qs.filter(po__id=params['po'])

        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """DRAFT → QC_PENDING (triggers auto QC creation)."""
        grn = self.get_object()
        if grn.status != 'DRAFT':
            raise ValidationError({"detail": "Only DRAFT GRNs can be submitted."})
        if not grn.items.exists():
            raise ValidationError({"detail": "GRN must have at least one line item."})
        grn.status = 'QC_PENDING'
        grn.save(update_fields=['status'])  # model.save() triggers _trigger_qc_inspection
        return Response(GRNSerializer(grn, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        _require_manager(request)
        grn = self.get_object()
        if grn.status in ('STOCK_UPDATED', 'CANCELLED'):
            raise ValidationError({"detail": f"Cannot cancel GRN in {grn.status} status."})
        grn.status = 'CANCELLED'
        grn.save(update_fields=['status'])
        return Response({"message": f"GRN {grn.grn_number} cancelled."})


# ─────────────────────────────────────────────────────────────────────────────
# Vendor Invoice
# ─────────────────────────────────────────────────────────────────────────────

class VendorInvoiceViewSet(ModelPermissionMixin, TenantModelViewSet):
    serializer_class   = VendorInvoiceSerializer
    permission_classes = [IsAuthenticated]
    parser_classes     = [MultiPartParser, FormParser]

    def get_queryset(self):
        qs = VendorInvoice.objects.filter(
            tenant=self.request.tenant
        ).select_related('vendor', 'po', 'grn', 'approved_by')

        params = self.request.query_params
        if params.get('match_status'):
            qs = qs.filter(match_status=params['match_status'].upper())
        if params.get('payment_status'):
            qs = qs.filter(payment_status=params['payment_status'].upper())
        if params.get('vendor'):
            qs = qs.filter(vendor__id=params['vendor'])

        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='three-way-match')
    def three_way_match(self, request, pk=None):
        """
        POST /purchase/vendor-invoices/{id}/three-way-match/
        Compares PO value, GRN accepted value, and invoice amount.
        """
        invoice = self.get_object()
        result  = services.three_way_match(invoice)
        return Response(result)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a MATCHED invoice for payment. Manager only."""
        _require_manager(request)
        invoice = self.get_object()
        if invoice.match_status not in ('MATCHED',):
            raise ValidationError({
                "detail": "Only MATCHED invoices can be approved. Run three-way match first."
            })
        invoice.match_status = 'APPROVED'
        invoice.approved_by  = request.user
        invoice.save(update_fields=['match_status', 'approved_by'])
        return Response(VendorInvoiceSerializer(invoice, context={'request': request}).data)
    