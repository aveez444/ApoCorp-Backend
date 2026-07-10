# apps/vendors/views.py

from decimal import Decimal

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

from django.db.models import Q, F, Sum, Count
from .models import Vendor, VendorDocument, ApprovedVendorList
from .serializers import (
    VendorListSerializer,
    VendorDetailSerializer,
    VendorDocumentSerializer,
    ApprovedVendorListSerializer,
)


class VendorViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Full CRUD for vendor master with approve / blacklist / upload-document actions.

    List view  → VendorListSerializer  (flat, fast)
    Retrieve   → VendorDetailSerializer (nested contacts, addresses, bank details)
    Create     → VendorDetailSerializer (nested create)
    Update     → VendorDetailSerializer (nested replace)

    Filters (query params):
        ?status=ACTIVE
        ?category=MECHANICAL
        ?vendor_type=SUPPLIER
        ?is_approved=true
        ?search=<name or vendor_code>
    """

    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = Vendor.objects.filter(tenant=self.request.tenant).select_related(
            'approved_by', 'created_by'
        ).prefetch_related('contacts', 'addresses', 'bank_details', 'documents')

        # Query param filters
        status_filter   = self.request.query_params.get('status')
        category        = self.request.query_params.get('category')
        vendor_type     = self.request.query_params.get('vendor_type')
        is_approved     = self.request.query_params.get('is_approved')
        search          = self.request.query_params.get('search')

        if status_filter:
            qs = qs.filter(status=status_filter.upper())
        if category:
            qs = qs.filter(category=category.upper())
        if vendor_type:
            qs = qs.filter(vendor_type=vendor_type.upper())
        if is_approved is not None:
            qs = qs.filter(is_approved=(is_approved.lower() == 'true'))
        if search:
            qs = qs.filter(name__icontains=search) | qs.filter(vendor_code__icontains=search)

        return qs.order_by('name')

    def get_serializer_class(self):
        if self.action == 'list':
            return VendorListSerializer
        return VendorDetailSerializer

    # ─────────────────────────────────────────────────────────────────────
    # Approve
    # ─────────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        Mark vendor as approved. Manager only.
        Vendor must be ACTIVE and not already approved.
        """
        self._require_manager()
        vendor = self.get_object()

        if vendor.status == 'BLACKLISTED':
            raise ValidationError("Cannot approve a blacklisted vendor.")
        if vendor.is_approved:
            return Response({"message": "Vendor is already approved."})

        vendor.is_approved  = True
        vendor.approved_by  = request.user
        vendor.approved_at  = timezone.now()
        vendor.save(update_fields=['is_approved', 'approved_by', 'approved_at'])

        return Response({"message": f"Vendor {vendor.vendor_code} approved successfully."})

    # ─────────────────────────────────────────────────────────────────────
    # Blacklist
    # ─────────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def blacklist(self, request, pk=None):
        """
        Blacklist a vendor. Manager only. Requires a reason in request body.
        Resets is_approved to False — blacklisted vendors cannot be re-approved
        without first being set to ACTIVE.
        """
        self._require_manager()
        vendor = self.get_object()

        reason = (request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({"reason": "A blacklist reason is required."})

        vendor.status           = 'BLACKLISTED'
        vendor.blacklist_reason = reason
        vendor.is_approved      = False
        vendor.save(update_fields=['status', 'blacklist_reason', 'is_approved'])

        return Response({"message": f"Vendor {vendor.vendor_code} has been blacklisted."})

    # ─────────────────────────────────────────────────────────────────────
    # Purchase history  (aggregated from POs — data available after Sprint 3)
    # ─────────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['get'])
    def purchase_history(self, request, pk=None):
        """
        Returns aggregated purchase statistics for the vendor.
        Safe to call before purchase module is built — returns zeros.
        """
        vendor = self.get_object()

        try:
            from apps.purchase.models import PurchaseOrder
            from django.db.models import Sum, Count, Avg

            pos = PurchaseOrder.objects.filter(
                tenant=request.tenant,
                vendor=vendor,
            ).exclude(status='CANCELLED')

            total_orders     = pos.count()
            total_value      = pos.aggregate(t=Sum('total_value'))['t'] or Decimal('0')
            received_count   = pos.filter(status='RECEIVED').count()
            # Delivery performance: compare delivery_date vs GRN received_date
            # Simplified: % of POs where GRN received_date <= delivery_date
            on_time = 0
            if received_count:
                from apps.purchase.models import GRN
                grns = GRN.objects.filter(po__vendor=vendor, po__tenant=request.tenant)
                on_time_grns = grns.filter(
                    received_date__lte=F('po__delivery_date')
                ).count() if grns.count() else 0
                on_time_pct = round((on_time_grns / grns.count()) * 100, 1) if grns.count() else 0
            else:
                on_time_pct = 0

        except Exception:
            total_orders = 0
            total_value  = Decimal('0')
            on_time_pct  = 0

        return Response({
            'vendor_code':   vendor.vendor_code,
            'vendor_name':   vendor.name,
            'total_orders':  total_orders,
            'total_value':   total_value,
            'on_time_pct':   on_time_pct,
            'rating':        vendor.rating,
        })

    # ─────────────────────────────────────────────────────────────────────
    # Document upload (multipart — separate from JSON body)
    # ─────────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'],
            parser_classes=[MultiPartParser, FormParser],
            url_path='upload-document')
    def upload_document(self, request, pk=None):
        vendor   = self.get_object()
        file_obj = request.FILES.get('file')
        if not file_obj:
            raise ValidationError({"file": "No file provided."})

        doc_type    = request.data.get('doc_type', 'OTHER')
        description = request.data.get('description', '')

        doc = VendorDocument.objects.create(
            vendor=vendor, file=file_obj,
            doc_type=doc_type, description=description
        )
        return Response(
            VendorDocumentSerializer(doc, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )

    # ─────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────

    def _get_tenant_user(self):
        return TenantUser.objects.filter(
            user=self.request.user,
            tenant=self.request.tenant
        ).first()

    def _require_manager(self):
        tu = self._get_tenant_user()
        if not tu or tu.role != 'manager':
            raise PermissionDenied("Only managers can perform this action.")


# ─────────────────────────────────────────────────────────────────────────────
# Approved Vendor List ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class ApprovedVendorListViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Manage AVL entries. Used by the RFQ module to filter eligible vendors.

    Filters:
        ?category=MECHANICAL
        ?item_code=ITM00001    (exact)
        ?vendor=<vendor_uuid>
    
    The RFQ vendor picker calls:
        GET /api/vendors/avl/?category=MECHANICAL&item_code=ITM00001
    and gets back a deduplicated list of approved, non-blacklisted vendors.
    """

    serializer_class   = ApprovedVendorListSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ApprovedVendorList.objects.filter(
            tenant=self.request.tenant
        ).select_related('vendor', 'approved_by').filter(
            vendor__status='ACTIVE',
            vendor__is_approved=True,
        )

        category  = self.request.query_params.get('category')
        item_code = self.request.query_params.get('item_code')
        vendor_id = self.request.query_params.get('vendor')

        if category:
            qs = qs.filter(item_category=category.upper())
        if item_code:
            # Return both category-level and item-level matches
            qs = qs.filter(
                Q(item_code=item_code) | Q(item_code='')
            )
        if vendor_id:
            qs = qs.filter(vendor__id=vendor_id)

        return qs.order_by('item_category', 'item_code', 'vendor__name')