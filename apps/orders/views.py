from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import OrderAcknowledgement, OALineItem, Order
from .serializers import OrderAcknowledgementSerializer, OrderSerializer


class OrderAcknowledgementViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = OrderAcknowledgement.objects.all()
    serializer_class = OrderAcknowledgementSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            queryset = queryset.filter(
                quotation__enquiry__assigned_to=self.request.user
            )

        oa_number = self.request.query_params.get("oa_number")
        if oa_number:
            queryset = queryset.filter(oa_number=oa_number)

        status = self.request.query_params.get("status")
        if status:
            queryset = queryset.filter(status__iexact=status)

        quotation_id = self.request.query_params.get("quotation")
        if quotation_id:
            queryset = queryset.filter(quotation__id=quotation_id)

        return queryset

    def perform_update(self, serializer):
        instance = self.get_object()
        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            if instance.quotation.enquiry.assigned_to != self.request.user:
                raise PermissionDenied("You can only update OAs assigned to you.")

        serializer.save()

    @action(detail=False, methods=['post'], url_path='initialize')
    @transaction.atomic
    def initialize(self, request):
        """
        Called when user clicks Generate OA on a quotation.
        - If OA already exists for this quotation, return it.
        - Otherwise create a new PENDING OA pre-filled from quotation data.
        Returns: { id, status, oa }
        """
        quotation_id = request.data.get("quotation")
        if not quotation_id:
            raise ValidationError({"quotation": "This field is required."})

        # Return existing OA if already created for this quotation
        try:
            existing = OrderAcknowledgement.objects.get(
                quotation__id=quotation_id,
                tenant=request.tenant
            )
            serializer = self.get_serializer(existing)
            return Response({
                "id":     str(existing.id),
                "status": existing.status,
                "oa":     serializer.data,
            })
        except OrderAcknowledgement.DoesNotExist:
            pass

        # Load quotation
        from apps.quotations.models import Quotation
        try:
            quotation = Quotation.objects.get(id=quotation_id, tenant=request.tenant)
        except Quotation.DoesNotExist:
            raise ValidationError({"quotation": "Quotation not found."})

        if quotation.review_status != "APPROVED":
            raise ValidationError({"quotation": "Quotation must be approved before creating OA."})

        # ── Build line items ──────────────────────────────────────────────────
        # QuotationLineItem snapshot fields:
        #   product_name_snapshot, description_snapshot, hsn_snapshot, unit_snapshot
        # Other fields: job_code, customer_part_no, part_no, quantity,
        #               unit_price, tax_percent, tax_group_code, line_total, tax_amount
        line_items_to_create = []
        sub_total = 0.0
        total_tax = 0.0

        for li in quotation.line_items.all():
            qty       = float(li.quantity)
            price     = float(li.unit_price)
            tax_pct   = float(li.tax_percent) if li.tax_percent else 0.0
            line_excl = qty * price
            line_tax  = round(line_excl * (tax_pct / 100), 2)
            line_tot  = round(line_excl + line_tax, 2)

            sub_total += line_excl
            total_tax += line_tax

            line_items_to_create.append({
                "job_code":         li.job_code         or "",
                "customer_part_no": li.customer_part_no or "",
                "part_no":          li.part_no          or "",
                "description":      li.product_name_snapshot or li.description_snapshot or "",
                "hsn_code":         li.hsn_snapshot      or "",
                "quantity":         li.quantity,
                "unit":             li.unit_snapshot      or "NOS",
                "unit_price":       li.unit_price,
                "tax_group_code":   li.tax_group_code    or "GST 18%",
                "tax_percent":      tax_pct,
                "tax_amount":       line_tax,
                "total":            line_tot,
            })

        grand_total = round(sub_total + total_tax, 2)

        # ── Billing / shipping snapshots ──────────────────────────────────────
        customer      = quotation.enquiry.customer
        billing_addr  = customer.addresses.filter(address_type='BILLING').first()
        shipping_addr = customer.addresses.filter(address_type='SHIPPING').first()

        billing_snapshot = {
            "entity_name":    billing_addr.entity_name    if billing_addr else customer.company_name,
            "address_line":   billing_addr.address_line   if billing_addr else "",
            "contact_person": billing_addr.contact_person if billing_addr else "",
            "contact_email":  billing_addr.contact_email  if billing_addr else (customer.email or ""),
            "contact_number": billing_addr.contact_number if billing_addr else (customer.telephone_primary or ""),
        }

        shipping_snapshot = {
            "entity_name":    shipping_addr.entity_name    if shipping_addr else customer.company_name,
            "address_line":   shipping_addr.address_line   if shipping_addr else "",
            "contact_person": shipping_addr.contact_person if shipping_addr else "",
            "contact_email":  shipping_addr.contact_email  if shipping_addr else (customer.email or ""),
            "contact_number": shipping_addr.contact_number if shipping_addr else (customer.telephone_primary or ""),
        }

        # ── Transport details ─────────────────────────────────────────────────
        order_num = f"OD{quotation.quotation_number.replace('QT', '')}"

        transport_details = {
            "order_number":          order_num,
            "order_book_number":     order_num,
            "order_type":            "std.mfg.comp",
            "order_date":            "",
            "quote_date":            str(quotation.created_at.date()) if quotation.created_at else "",
            "customer_po_number":    quotation.po_number or "NA",
            "po_date":               "",
            "delivery_date":         "",
            "division":              "LQP",
            "project_type":          "",
            "mode_of_transport":     "By Road",
            "preferred_transporter": "Will be intimated later",
            "packing_type":          "Card Board",
            "ecc_exemption":         "Not Applicable",
            "road_permit":           "Not Required",
            "shipping_gst":          "Not Required",
            "loi_number":            "NA",
            "project_name":          "NA",
        }

        # ── Create OA and line items ──────────────────────────────────────────
        oa = OrderAcknowledgement.objects.create(
            tenant=request.tenant,
            quotation=quotation,
            status='PENDING',
            billing_snapshot=billing_snapshot,
            shipping_snapshot=shipping_snapshot,
            transport_details=transport_details,
            currency=quotation.currency or "INR",
            exchange_rate=quotation.exchange_rate or 1,
            total_value=grand_total,
        )

        for item in line_items_to_create:
            OALineItem.objects.create(oa=oa, **item)

        serializer = self.get_serializer(oa)
        return Response({
            "id":     str(oa.id),
            "status": oa.status,
            "oa":     serializer.data,
        })

    @action(detail=True, methods=['post'])
    @transaction.atomic
    def share(self, request, pk=None):
        """
        Converts OA to CONVERTED and creates an Order record.
        Accepts PENDING or DRAFT status.
        """
        oa = self.get_object()

        if oa.status not in ('PENDING', 'DRAFT'):
            raise PermissionDenied(
                f"Cannot share OA with status '{oa.status}'. "
                "Only PENDING or DRAFT OAs can be shared."
            )

        if hasattr(oa, 'order'):
            raise PermissionDenied("Order already exists for this OA.")

        order = Order.objects.create(
            tenant=request.tenant,
            order_number=f"ORD-{oa.oa_number}",
            oa=oa,
            currency=oa.currency,
            exchange_rate=oa.exchange_rate,
            total_value=oa.total_value,
        )

        oa.status = 'CONVERTED'
        oa.last_activity_at = timezone.now()
        oa.save(update_fields=["status", "last_activity_at"])

        return Response({
            "message":      "OA shared and Order created successfully.",
            "order_id":     str(order.id),
            "order_number": order.order_number,
        })


class OrderViewSet(ModelPermissionMixin, TenantModelViewSet):

    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        queryset = super().get_queryset()

        tenant_user = TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

        if tenant_user and tenant_user.role == 'employee':
            return queryset.filter(
                oa__quotation__enquiry__assigned_to=self.request.user
            )

        return queryset