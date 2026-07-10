# apps/inventory/views.py

from decimal import Decimal

from django.db import transaction
from django.db.models import Sum, F, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import (
    ItemMaster, Warehouse, StorageLocation,
    StockBatch, StockLedger,
    MaterialIssueSlip, MaterialIssueSlipItem,
    BarcodeLabel, StockReservation,
)
from .serializers import (
    ItemMasterListSerializer, ItemMasterDetailSerializer,
    WarehouseSerializer, StorageLocationSerializer,
    StockBatchSerializer, StockLedgerSerializer,
    MaterialIssueSlipSerializer,
    BarcodeLabelSerializer, BarcodeGenerateSerializer,
    StockReservationSerializer,
)
from . import services


def _get_tenant_user(request):
    return TenantUser.objects.filter(
        user=request.user, tenant=request.tenant
    ).first()


def _require_store_manager(request):
    tu = _get_tenant_user(request)
    if not tu or tu.role != 'manager':
        raise PermissionDenied("Only store managers can perform this action.")


# ─────────────────────────────────────────────────────────────────────────────
# Item Master
# ─────────────────────────────────────────────────────────────────────────────

class ItemMasterViewSet(ModelPermissionMixin, TenantModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = ItemMaster.objects.filter(tenant=self.request.tenant).select_related(
            'product', 'created_by'
        )
        params = self.request.query_params
        if params.get('category'):
            qs = qs.filter(category__iexact=params['category'])
        if params.get('item_type'):
            qs = qs.filter(item_type=params['item_type'].upper())
        if params.get('is_active') is not None:
            qs = qs.filter(is_active=(params['is_active'].lower() == 'true'))
        if params.get('search'):
            qs = qs.filter(
                Q(name__icontains=params['search']) |
                Q(item_code__icontains=params['search'])
            )
        return qs.order_by('item_code')

    def get_serializer_class(self):
        if self.action == 'list':
            return ItemMasterListSerializer
        return ItemMasterDetailSerializer


# ─────────────────────────────────────────────────────────────────────────────
# Warehouse + Storage Location
# ─────────────────────────────────────────────────────────────────────────────

class WarehouseViewSet(ModelPermissionMixin, TenantModelViewSet):
    serializer_class   = WarehouseSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Warehouse.objects.filter(
            tenant=self.request.tenant
        ).prefetch_related('locations').order_by('code')


class StorageLocationViewSet(TenantModelViewSet):
    serializer_class = StorageLocationSerializer

    def get_queryset(self):
        return StorageLocation.objects.filter(warehouse__tenant=self.request.tenant)

    def perform_create(self, serializer):
        serializer.save()


# ─────────────────────────────────────────────────────────────────────────────
# Stock
# ─────────────────────────────────────────────────────────────────────────────

class StockViewSet(TenantModelViewSet):
    serializer_class   = StockBatchSerializer
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'head', 'options']

    def get_queryset(self):
        qs = StockBatch.objects.filter(
            tenant=self.request.tenant
        ).select_related('item', 'warehouse', 'storage_location')
        params = self.request.query_params
        if params.get('item_code'):
            qs = qs.filter(item__item_code=params['item_code'])
        if params.get('warehouse'):
            qs = qs.filter(warehouse__id=params['warehouse'])
        if params.get('qc_status'):
            qs = qs.filter(qc_status=params['qc_status'].upper())
        return qs.order_by('item__item_code', 'received_date')

    def list(self, request, *args, **kwargs):
        batches = self.get_queryset()
        grouped: dict = {}
        for b in batches:
            key = (str(b.item.id), str(b.warehouse.id))
            if key not in grouped:
                grouped[key] = {
                    'item_id':        str(b.item.id),
                    'item_code':      b.item.item_code,
                    'item_name':      b.item.name,
                    'uom':            b.item.uom,
                    'warehouse_id':   str(b.warehouse.id),
                    'warehouse_code': b.warehouse.code,
                    'on_hand':        Decimal('0'),
                    'reserved':       Decimal('0'),
                    'available':      Decimal('0'),
                    'reorder_level':  b.item.reorder_level,
                    'below_reorder':  False,
                    'batches':        [],
                }
            row = grouped[key]
            row['on_hand']  += b.quantity_on_hand
            row['reserved'] += b.quantity_reserved
            row['batches'].append(StockBatchSerializer(b).data)

        for row in grouped.values():
            row['available']     = row['on_hand'] - row['reserved']
            row['below_reorder'] = row['available'] <= row['reorder_level']

        return Response(list(grouped.values()))

    @action(detail=False, methods=['get'])
    def availability(self, request):
        item_code = request.query_params.get('item_code')
        qty       = request.query_params.get('qty')
        if not item_code or not qty:
            raise ValidationError({"detail": "item_code and qty are required."})
        try:
            item = ItemMaster.objects.get(item_code=item_code, tenant=request.tenant)
        except ItemMaster.DoesNotExist:
            raise ValidationError({"item_code": f"Item {item_code} not found."})
        warehouse = None
        if request.query_params.get('warehouse'):
            try:
                warehouse = Warehouse.objects.get(
                    id=request.query_params['warehouse'], tenant=request.tenant
                )
            except Warehouse.DoesNotExist:
                raise ValidationError({"warehouse": "Warehouse not found."})
        result = services.check_stock_availability(item, qty, warehouse)
        return Response(result)


class StockAlertView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request):
        summaries = (
            StockBatch.objects
            .filter(tenant=request.tenant, qc_status='PASSED')
            .values('item__id', 'item__item_code', 'item__name',
                    'item__uom', 'item__reorder_level', 'item__reorder_qty')
            .annotate(
                on_hand=Sum('quantity_on_hand'),
                reserved=Sum('quantity_reserved'),
                available=Sum(F('quantity_on_hand') - F('quantity_reserved')),
            )
            .filter(available__lte=F('item__reorder_level'))
            .order_by('item__item_code')
        )
        return Response([{
            'item_id':       str(s['item__id']),
            'item_code':     s['item__item_code'],
            'item_name':     s['item__name'],
            'uom':           s['item__uom'],
            'available_qty': s['available'],
            'reorder_level': s['item__reorder_level'],
            'reorder_qty':   s['item__reorder_qty'],
            'shortfall':     s['item__reorder_level'] - s['available'],
        } for s in summaries])


# ─────────────────────────────────────────────────────────────────────────────
# Stock Reservations
# ─────────────────────────────────────────────────────────────────────────────

class StockReservationViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Stock Reservation API

    PM creates reservations (auto-created by MRP, or manually).
    Store Manager approves/rejects.

    Filters:
      ?status=PENDING|APPROVED|REJECTED|PARTIALLY_ISSUED|FULLY_ISSUED|CANCELLED
      ?project=<uuid>
      ?item=<uuid>
    """
    serializer_class   = StockReservationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = StockReservation.objects.filter(
            tenant=self.request.tenant
        ).select_related('project', 'item', 'warehouse', 'requested_by', 'approved_by', 'mrp_run')

        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('project'):
            qs = qs.filter(project__id=params['project'])
        if params.get('item'):
            qs = qs.filter(item__id=params['item'])

        return qs.order_by('required_by_date', '-created_at')

    def perform_create(self, serializer):
        """PM creates a manual reservation request."""
        serializer.save(
            tenant=self.request.tenant,
            requested_by=self.request.user,
            status='PENDING',
        )

    @action(detail=True, methods=['get'], url_path='conflict-info')
    def conflict_info(self, request, pk=None):
        """
        GET /inventory/reservations/{id}/conflict-info/
        Shows store manager the full stock picture before approving:
        on_hand, other approved reservations, truly available, competing projects.
        """
        reservation = self.get_object()
        data = services.get_reservation_conflict_info(reservation)
        return Response(data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        POST /inventory/reservations/{id}/approve/
        Body: { "approved_qty": 50 }
        Store manager only. Soft-locks stock on StockBatch immediately.
        """
        _require_store_manager(request)
        reservation = self.get_object()

        approved_qty = request.data.get('approved_qty')
        if approved_qty is None:
            raise ValidationError({"approved_qty": "Required."})

        try:
            reservation = services.approve_reservation(
                reservation=reservation,
                approved_qty=approved_qty,
                approved_by=request.user,
            )
        except ValueError as e:
            raise ValidationError({"detail": str(e)})

        return Response(StockReservationSerializer(reservation).data)

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """
        POST /inventory/reservations/{id}/reject/
        Body: { "reason": "Higher priority project needs this stock." }
        Store manager only.
        """
        _require_store_manager(request)
        reservation = self.get_object()
        reason = (request.data.get('reason') or '').strip()

        try:
            reservation = services.reject_reservation(
                reservation=reservation,
                actioned_by=request.user,
                reason=reason,
            )
        except ValueError as e:
            raise ValidationError({"detail": str(e)})

        return Response(StockReservationSerializer(reservation).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        POST /inventory/reservations/{id}/cancel/
        PM or store manager. Releases soft lock if already approved.
        """
        reservation = self.get_object()
        try:
            reservation = services.cancel_reservation(
                reservation=reservation,
                cancelled_by=request.user,
            )
        except ValueError as e:
            raise ValidationError({"detail": str(e)})

        return Response(StockReservationSerializer(reservation).data)


# ─────────────────────────────────────────────────────────────────────────────
# Stock Ledger
# ─────────────────────────────────────────────────────────────────────────────

class StockLedgerViewSet(TenantModelViewSet):
    serializer_class   = StockLedgerSerializer
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'head', 'options']

    def get_queryset(self):
        qs = StockLedger.objects.filter(
            item__tenant=self.request.tenant
        ).select_related('item', 'batch', 'warehouse', 'created_by')
        params = self.request.query_params
        if params.get('item_code'):
            qs = qs.filter(item__item_code=params['item_code'])
        if params.get('warehouse'):
            qs = qs.filter(warehouse__id=params['warehouse'])
        if params.get('transaction_type'):
            qs = qs.filter(transaction_type=params['transaction_type'].upper())
        if params.get('date_from'):
            qs = qs.filter(created_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(created_at__date__lte=params['date_to'])
        return qs.order_by('-created_at')


# ─────────────────────────────────────────────────────────────────────────────
# Material Issue Slip
# ─────────────────────────────────────────────────────────────────────────────

class MaterialIssueSlipViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Flow:
      PM creates slip (DRAFT = request for material)
      Store Manager reviews and issues (ISSUED = physical handover)
      Either party can cancel (DRAFT or ISSUED → CANCELLED)
    """
    serializer_class   = MaterialIssueSlipSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = MaterialIssueSlip.objects.filter(
            tenant=self.request.tenant
        ).select_related('issued_by', 'indent', 'project').prefetch_related(
            'items__item', 'items__batch'
        )
        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('project'):
            qs = qs.filter(project__id=params['project'])
        if params.get('date_from'):
            qs = qs.filter(issued_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(issued_at__date__lte=params['date_to'])
        return qs.order_by('-issued_at')

    @action(detail=True, methods=['post'])
    def issue(self, request, pk=None):
        """
        POST /inventory/issue-slips/{id}/issue/
        Store manager physically hands over stock. DRAFT → ISSUED.
        Calls services.issue_stock() per line:
          - quantity_on_hand  decreases
          - quantity_reserved decreases (soft lock consumed)
          - StockLedger entry written
          - StockReservation.issued_qty updated
        """
        _require_store_manager(request)
        slip = self.get_object()

        if slip.status != 'DRAFT':
            raise ValidationError({"detail": f"Cannot issue a slip in {slip.status} status."})

        errors = []
        with transaction.atomic():
            for slip_item in slip.items.select_related('item', 'batch').all():
                try:
                    services.issue_stock(slip_item=slip_item, user=request.user)
                except ValueError as e:
                    errors.append(str(e))

            if errors:
                raise ValidationError({"stock_errors": errors})

            # Update total_cost on the slip
            total_cost = sum(
                (item.issued_qty * (item.batch.unit_cost if item.batch else 0))
                for item in slip.items.select_related('batch').all()
            )
            slip.status     = 'ISSUED'
            slip.total_cost = total_cost
            slip.save(update_fields=['status', 'total_cost'])

        return Response(MaterialIssueSlipSerializer(slip, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """
        POST /inventory/issue-slips/{id}/cancel/
        If ISSUED: reverses stock (on_hand and reserved both go back up).
        """
        slip = self.get_object()
        if slip.status == 'CANCELLED':
            raise ValidationError({"detail": "Slip is already cancelled."})

        with transaction.atomic():
            if slip.status == 'ISSUED':
                for slip_item in slip.items.all():
                    services.release_issue(slip_item=slip_item)

            slip.status = 'CANCELLED'
            slip.save(update_fields=['status'])

        return Response({"message": f"Issue slip {slip.slip_number} cancelled."})


# ─────────────────────────────────────────────────────────────────────────────
# Barcode Labels  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

class BarcodeViewSet(TenantModelViewSet):
    serializer_class   = BarcodeLabelSerializer
    permission_classes = [IsAuthenticated]
    http_method_names  = ['get', 'post', 'head', 'options']

    def get_queryset(self):
        return BarcodeLabel.objects.filter(
            item__tenant=self.request.tenant
        ).select_related('item', 'batch').order_by('-generated_at')

    @action(detail=False, methods=['post'])
    def generate(self, request):
        ser = BarcodeGenerateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data
        tenant_code = getattr(request.tenant, 'code', str(request.tenant.id)[:6]).upper()
        labels_created = []

        if data.get('grn_id'):
            try:
                from apps.purchase.models import GRN
                grn = GRN.objects.get(id=data['grn_id'], tenant=request.tenant)
            except Exception:
                raise ValidationError({"grn_id": "GRN not found."})
            for grn_item in grn.items.select_related('item').all():
                batch = StockBatch.objects.filter(grn=grn, item=grn_item.item).first()
                if batch:
                    labels_created.extend(services.create_barcode_labels(
                        item=grn_item.item, batch=batch,
                        label_type=data['label_type'], reference_id=grn.id,
                        tenant_code=tenant_code, count=data.get('count', 1),
                    ))
        elif data.get('item_id'):
            try:
                item  = ItemMaster.objects.get(id=data['item_id'], tenant=request.tenant)
                batch = StockBatch.objects.get(id=data['batch_id']) if data.get('batch_id') else None
            except ItemMaster.DoesNotExist:
                raise ValidationError({"item_id": "Item not found."})
            labels_created.extend(services.create_barcode_labels(
                item=item, batch=batch,
                label_type=data['label_type'],
                reference_id=data.get('grn_id') or item.id,
                tenant_code=tenant_code, count=data.get('count', 1),
            ))

        return Response(BarcodeLabelSerializer(labels_created, many=True).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'])
    def scan(self, request):
        code = request.query_params.get('code', '').strip()
        if not code:
            raise ValidationError({"code": "Barcode string is required."})
        try:
            result = services.resolve_barcode(code, request.tenant)
        except ValueError as e:
            return Response({"error": str(e)}, status=status.HTTP_404_NOT_FOUND)
        return Response(result)