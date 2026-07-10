# apps/qc/views.py

from django.db.models import Count, Q, Avg, F
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import InspectionPlan, QCInspectionOrder, QCAttachment, NCR
from .serializers import (
    InspectionPlanSerializer,
    QCInspectionOrderListSerializer,
    QCInspectionOrderDetailSerializer,
    CloseInspectionSerializer,
    NCRSerializer,
)
from . import services


def _get_tenant_user(request):
    return TenantUser.objects.filter(
        user=request.user, tenant=request.tenant
    ).first()


def _require_manager(request):
    tu = _get_tenant_user(request)
    if not tu or tu.role != 'manager':
        raise PermissionDenied("Only managers can perform this action.")


# ─────────────────────────────────────────────────────────────────────────────
# Inspection Plan
# ─────────────────────────────────────────────────────────────────────────────

class InspectionPlanViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    CRUD for inspection plans.
    Filters: ?qc_type= ?item_code= ?item_category= ?is_active=
    """
    serializer_class   = InspectionPlanSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = InspectionPlan.objects.filter(
            tenant=self.request.tenant
        ).select_related('item').prefetch_related('parameters')

        params = self.request.query_params
        if params.get('qc_type'):
            qs = qs.filter(qc_type=params['qc_type'].upper())
        if params.get('item_code'):
            qs = qs.filter(item__item_code=params['item_code'])
        if params.get('item_category'):
            qs = qs.filter(item_category__iexact=params['item_category'])
        if params.get('is_active') is not None:
            qs = qs.filter(is_active=(params['is_active'].lower() == 'true'))

        return qs.order_by('item_category', 'qc_type')


# ─────────────────────────────────────────────────────────────────────────────
# QC Inspection Order
# ─────────────────────────────────────────────────────────────────────────────

class QCInspectionOrderViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Filters: ?status= ?qc_type= ?reference_type= ?inspector= ?date_from= ?date_to=
    """
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = QCInspectionOrder.objects.filter(
            tenant=self.request.tenant
        ).select_related(
            'item', 'batch', 'plan', 'inspector',
            'grn_item__grn__vendor',
        ).prefetch_related('results__parameter', 'attachments')

        params = self.request.query_params
        tu = _get_tenant_user(self.request)

        # Employees see only their assigned inspections
        if tu and tu.role == 'employee':
            qs = qs.filter(inspector=self.request.user)

        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('qc_type'):
            qs = qs.filter(qc_type=params['qc_type'].upper())
        if params.get('reference_type'):
            qs = qs.filter(reference_type=params['reference_type'].upper())
        if params.get('outcome'):
            qs = qs.filter(outcome=params['outcome'].upper())
        if params.get('date_from'):
            qs = qs.filter(created_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(created_at__date__lte=params['date_to'])

        return qs.order_by('-created_at')

    def get_serializer_class(self):
        if self.action == 'list':
            return QCInspectionOrderListSerializer
        return QCInspectionOrderDetailSerializer

    # ── Start ────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """PENDING → IN_PROGRESS. Sets started_at."""
        order = self.get_object()
        if order.status != 'PENDING':
            raise ValidationError({"detail": "Only PENDING inspections can be started."})

        order.status     = 'IN_PROGRESS'
        order.started_at = timezone.now()
        # Auto-assign inspector to request.user if not already assigned
        if not order.inspector:
            order.inspector = request.user
        order.save(update_fields=['status', 'started_at', 'inspector'])

        return Response(
            QCInspectionOrderDetailSerializer(order, context={'request': request}).data
        )

    # ── Close ────────────────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """
        POST /qc/inspection-orders/{id}/close/
        Body: {outcome, remarks, results[], accepted_qty (optional)}

        Delegates to qc.services.close_inspection() which:
          - Records QCResult rows
          - On PASS: calls inventory.services.receive_stock(), updates GRN + PO
          - On FAIL: creates NCR automatically
          - On HOLD: flags batch as ON_HOLD
        """
        order = self.get_object()
        if order.status == 'COMPLETED':
            raise ValidationError({"detail": "Inspection is already completed."})

        ser = CloseInspectionSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        data = ser.validated_data

        # Optional: allow inspector to specify partial acceptance qty
        if data.get('accepted_qty') is not None and order.grn_item:
            order.grn_item.accepted_qty = data['accepted_qty']
            order.grn_item.rejected_qty = (
                order.grn_item.received_qty - data['accepted_qty']
            )
            order.grn_item.save(update_fields=['accepted_qty', 'rejected_qty'])

        services.close_inspection(
            inspection_order=order,
            outcome=data['outcome'],
            results_data=data.get('results', []),
            remarks=data.get('remarks', ''),
            user=request.user,
        )

        # Reload from DB after service mutations
        order.refresh_from_db()
        return Response(
            QCInspectionOrderDetailSerializer(order, context={'request': request}).data
        )

    # ── Disposition (resolve HOLD) ────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def disposition(self, request, pk=None):
        """
        POST /qc/inspection-orders/{id}/disposition/
        Manager resolves a HOLD — body: {outcome (PASS|FAIL), remarks}
        """
        _require_manager(request)
        order = self.get_object()

        if order.outcome != 'HOLD':
            raise ValidationError({"detail": "Only HOLD inspections can be dispositioned."})

        new_outcome = request.data.get('outcome', '').upper()
        if new_outcome not in ('PASS', 'FAIL'):
            raise ValidationError({"outcome": "Must be PASS or FAIL."})

        remarks = request.data.get('remarks', '')

        services.close_inspection(
            inspection_order=order,
            outcome=new_outcome,
            results_data=[],   # results already recorded
            remarks=f'[Disposition by manager] {remarks}',
            user=request.user,
        )

        order.refresh_from_db()
        return Response(
            QCInspectionOrderDetailSerializer(order, context={'request': request}).data
        )

    # ── Assign inspector ──────────────────────────────────────────────────────

    @action(detail=True, methods=['post'])
    def assign(self, request, pk=None):
        """Manager assigns an inspector. Body: {inspector_id}"""
        _require_manager(request)
        order = self.get_object()

        from django.contrib.auth.models import User
        inspector_id = request.data.get('inspector_id')
        if not inspector_id:
            raise ValidationError({"inspector_id": "This field is required."})
        try:
            inspector = User.objects.get(pk=inspector_id)
        except User.DoesNotExist:
            raise ValidationError({"inspector_id": "User not found."})

        order.inspector = inspector
        order.save(update_fields=['inspector'])
        return Response({"message": f"Inspector {inspector.get_full_name()} assigned."})

    # ── Upload attachment ─────────────────────────────────────────────────────

    @action(detail=True, methods=['post'],
            parser_classes=[MultiPartParser, FormParser],
            url_path='upload')
    def upload_attachment(self, request, pk=None):
        order    = self.get_object()
        file_obj = request.FILES.get('file')
        if not file_obj:
            raise ValidationError({"file": "No file provided."})

        attachment = QCAttachment.objects.create(
            inspection_order=order,
            file=file_obj,
            description=request.data.get('description', ''),
        )
        from .serializers import QCAttachmentSerializer
        return Response(
            QCAttachmentSerializer(attachment, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )

    # ── PDF report ────────────────────────────────────────────────────────────

    @action(detail=True, methods=['get'], url_path='report/pdf')
    def report_pdf(self, request, pk=None):
        """
        GET /qc/inspection-orders/{id}/report/pdf/
        QC certificate PDF via pdf_engine.
        """
        order = self.get_object()
        if order.status != 'COMPLETED':
            raise ValidationError({"detail": "Report only available for completed inspections."})

        try:
            from django.template.loader import render_to_string
            from apps.documents.pdf_engine import generate_quotation_pdf
            from django.http import HttpResponse

            context = {
                'order':       order,
                'results':     order.results.select_related('parameter').order_by('parameter__sequence'),
                'attachments': order.attachments.all(),
                'item':        order.item,
                'batch':       order.batch,
                'tenant':      request.tenant,
            }
            html      = render_to_string('documents/qc_certificate.html', context)
            base_url  = request.build_absolute_uri('/')
            letterhead = getattr(request.tenant, 'letterhead_pdf', None)
            pdf_bytes  = generate_quotation_pdf(html, base_url, letterhead)

            response = HttpResponse(pdf_bytes, content_type='application/pdf')
            response['Content-Disposition'] = (
                f'attachment; filename="QC-{order.qc_number}.pdf"'
            )
            return response
        except Exception as e:
            return Response({"error": str(e)}, status=500)


# ─────────────────────────────────────────────────────────────────────────────
# NCR
# ─────────────────────────────────────────────────────────────────────────────

class NCRViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Filters: ?status= ?disposition= ?date_from= ?date_to=
    """
    serializer_class   = NCRSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = NCR.objects.filter(
            tenant=self.request.tenant
        ).select_related(
            'inspection_order__item',
            'inspection_order__grn_item__grn__vendor',
            'raised_by', 'disposition_by',
        )

        params = self.request.query_params
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('disposition'):
            qs = qs.filter(disposition=params['disposition'].upper())
        if params.get('date_from'):
            qs = qs.filter(created_at__date__gte=params['date_from'])
        if params.get('date_to'):
            qs = qs.filter(created_at__date__lte=params['date_to'])

        return qs.order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(raised_by=self.request.user)

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        """
        POST /qc/ncr/{id}/close/
        Manager resolves NCR. Body: {disposition, corrective_action, root_cause}
        """
        _require_manager(request)
        ncr = self.get_object()

        if ncr.status == 'CLOSED':
            raise ValidationError({"detail": "NCR is already closed."})

        disposition = request.data.get('disposition', '').upper()
        valid_dispositions = [d[0] for d in NCR._meta.get_field('disposition').choices]
        if not disposition or disposition not in valid_dispositions:
            raise ValidationError({
                "disposition": f"Must be one of: {', '.join(valid_dispositions)}"
            })

        ncr.disposition       = disposition
        ncr.corrective_action = request.data.get('corrective_action', ncr.corrective_action)
        ncr.root_cause        = request.data.get('root_cause', ncr.root_cause)
        ncr.status            = 'CLOSED'
        ncr.disposition_by    = request.user
        ncr.disposition_at    = timezone.now()
        ncr.save()

        return Response(NCRSerializer(ncr, context={'request': request}).data)


# ─────────────────────────────────────────────────────────────────────────────
# QC Analytics
# ─────────────────────────────────────────────────────────────────────────────

class QCAnalyticsView(APIView):
    """
    GET /qc/analytics/
    Optional filters: ?date_from= ?date_to= ?qc_type=

    Returns:
    {
      rejection_rate_by_vendor,   — % fail by vendor (inward QC only)
      failure_rate_by_item,       — % fail by item
      ncr_ageing,                 — open NCRs bucketed by age (days)
      qc_throughput               — inspections completed per week (last 8 weeks)
    }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = request.tenant
        params = request.query_params

        base_qs = QCInspectionOrder.objects.filter(
            tenant=tenant, status='COMPLETED'
        )
        if params.get('date_from'):
            base_qs = base_qs.filter(completed_at__date__gte=params['date_from'])
        if params.get('date_to'):
            base_qs = base_qs.filter(completed_at__date__lte=params['date_to'])
        if params.get('qc_type'):
            base_qs = base_qs.filter(qc_type=params['qc_type'].upper())

        # ── 1. Rejection rate by vendor (inward QC) ──────────────────────────
        rejection_by_vendor = []
        inward_qs = base_qs.filter(qc_type='INWARD').select_related(
            'grn_item__grn__vendor'
        )
        vendor_map = {}
        for order in inward_qs:
            try:
                vendor_name = order.grn_item.grn.vendor.name
                vendor_code = order.grn_item.grn.vendor.vendor_code
            except AttributeError:
                continue
            key = vendor_code
            if key not in vendor_map:
                vendor_map[key] = {'vendor_name': vendor_name, 'vendor_code': vendor_code,
                                   'total': 0, 'failed': 0}
            vendor_map[key]['total'] += 1
            if order.outcome == 'FAIL':
                vendor_map[key]['failed'] += 1
        for v in vendor_map.values():
            v['rejection_rate_pct'] = round(
                (v['failed'] / v['total']) * 100, 1
            ) if v['total'] else 0
        rejection_by_vendor = sorted(
            vendor_map.values(), key=lambda x: x['rejection_rate_pct'], reverse=True
        )

        # ── 2. Failure rate by item ───────────────────────────────────────────
        failure_by_item = []
        item_map = {}
        for order in base_qs.select_related('item'):
            key = order.item.item_code
            if key not in item_map:
                item_map[key] = {
                    'item_code': order.item.item_code,
                    'item_name': order.item.name,
                    'total': 0, 'failed': 0,
                }
            item_map[key]['total'] += 1
            if order.outcome == 'FAIL':
                item_map[key]['failed'] += 1
        for i in item_map.values():
            i['failure_rate_pct'] = round(
                (i['failed'] / i['total']) * 100, 1
            ) if i['total'] else 0
        failure_by_item = sorted(
            item_map.values(), key=lambda x: x['failure_rate_pct'], reverse=True
        )

        # ── 3. NCR ageing ─────────────────────────────────────────────────────
        from django.utils import timezone as tz
        import datetime
        now = tz.now()
        open_ncrs = NCR.objects.filter(tenant=tenant, status__in=['OPEN', 'UNDER_REVIEW'])
        ncr_ageing = {'0_7_days': 0, '8_30_days': 0, '31_60_days': 0, 'over_60_days': 0}
        ncr_details = []
        for ncr in open_ncrs.select_related('inspection_order__item',
                                             'inspection_order__grn_item__grn__vendor'):
            age = (now - ncr.created_at).days
            if age <= 7:
                ncr_ageing['0_7_days'] += 1
            elif age <= 30:
                ncr_ageing['8_30_days'] += 1
            elif age <= 60:
                ncr_ageing['31_60_days'] += 1
            else:
                ncr_ageing['over_60_days'] += 1

            try:
                vendor = ncr.inspection_order.grn_item.grn.vendor.name
            except AttributeError:
                vendor = None
            ncr_details.append({
                'ncr_number': ncr.ncr_number,
                'item_code':  ncr.inspection_order.item.item_code,
                'vendor':     vendor,
                'age_days':   age,
                'status':     ncr.status,
            })

        # ── 4. Weekly throughput (last 8 weeks) ───────────────────────────────
        throughput = []
        for week_offset in range(7, -1, -1):
            week_start = (now - datetime.timedelta(weeks=week_offset + 1)).date()
            week_end   = (now - datetime.timedelta(weeks=week_offset)).date()
            count = base_qs.filter(
                completed_at__date__gte=week_start,
                completed_at__date__lt=week_end,
            ).count()
            passed = base_qs.filter(
                completed_at__date__gte=week_start,
                completed_at__date__lt=week_end,
                outcome='PASS',
            ).count()
            throughput.append({
                'week_start': week_start.isoformat(),
                'week_end':   week_end.isoformat(),
                'total':      count,
                'passed':     passed,
                'failed':     count - passed,
            })

        return Response({
            'rejection_rate_by_vendor': rejection_by_vendor,
            'failure_rate_by_item':     failure_by_item,
            'ncr_ageing':               {'buckets': ncr_ageing, 'details': ncr_details},
            'qc_throughput':            throughput,
        })