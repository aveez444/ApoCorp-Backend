# apps/mrp/views.py
from django.db import transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser
from decimal import Decimal

from .models import MRPRun, MRPLine
from .serializers import (
    MRPRunListSerializer,
    MRPRunDetailSerializer,
    MRPLineSerializer,
    MRPLineShortageSerializer,
    RunMRPSerializer,
    ConvertToIndentSerializer,
    MRPRunSummarySerializer,
    MRPLineUpdateSerializer,
)
from . import services


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_tenant_user(request):
    return TenantUser.objects.filter(
        user=request.user, tenant=request.tenant
    ).first()


def _require_pm(request):
    """Require Project Manager role"""
    tu = _get_tenant_user(request)
    if not tu or tu.role not in ('manager', 'project_manager'):
        raise PermissionDenied("Only Project Managers can perform this action.")


# ─────────────────────────────────────────────────────────────────────────────
# MRP Run ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class MRPRunViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    MRP Run API
    
    Filters:
    - ?project=<uuid>
    - ?status=PENDING|RUNNING|COMPLETED|FAILED
    - ?has_shortages=true|false
    """
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return MRPRunListSerializer
        return MRPRunDetailSerializer
    
    def get_queryset(self):
        qs = MRPRun.objects.filter(
            tenant=self.request.tenant
        ).select_related(
            'project', 'bom', 'run_by'
        ).prefetch_related('lines')
        
        params = self.request.query_params
        
        if params.get('project'):
            qs = qs.filter(project__id=params['project'])
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('has_shortages'):
            if params['has_shortages'].lower() == 'true':
                qs = qs.filter(items_with_shortage__gt=0)
            else:
                qs = qs.filter(items_with_shortage=0)
        
        return qs.order_by('-run_at')
    
    @action(detail=False, methods=['post'], url_path='run')
    def run_mrp(self, request):
        """
        POST /api/mrp/run/
        Trigger an MRP run for a project.
        Body: {"project_id": "uuid"}
        """
        _require_pm(request)
        
        serializer = RunMRPSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        project = serializer.validated_data['project_id']
        
        try:
            mrp_run = services.run_mrp(project, request.user)
            return Response(
                MRPRunDetailSerializer(mrp_run, context={'request': request}).data,
                status=status.HTTP_201_CREATED
            )
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({"detail": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'], url_path='shortages')
    def shortages(self, request, pk=None):
        """
        GET /api/mrp/runs/{id}/shortages/
        Returns only lines with shortages (has_shortage=True)
        """
        mrp_run = self.get_object()
        shortage_lines = mrp_run.lines.filter(
            has_shortage=True
        ).select_related(
            'engineering_item', 'inventory_item'
        ).order_by('-shortage_qty')
        
        serializer = MRPLineShortageSerializer(
            shortage_lines, 
            many=True,
            context={'request': request}
        )
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'], url_path='summary')
    def summary(self, request, pk=None):
        """
        GET /api/mrp/runs/{id}/summary/
        Returns summary statistics for this MRP run.
        """
        mrp_run = self.get_object()
        summary = services.get_mrp_summary(mrp_run)
        return Response(summary)
    
    @action(detail=True, methods=['post'], url_path='convert-to-indent')
    def convert_to_indent(self, request, pk=None):
        """
        POST /api/mrp/runs/{id}/convert-to-indent/
        Convert all shortages (or selected lines) to Purchase Indent.
        
        Body (optional):
        {
            "line_ids": ["uuid1", "uuid2"],  # Optional - if omitted, converts all
            "indent_type": "PRODUCTION",
            "notes": "Notes for the indent"
        }
        """
        _require_pm(request)
        mrp_run = self.get_object()
        
        if mrp_run.status != 'COMPLETED':
            raise ValidationError({
                "detail": f"Cannot convert shortages from a {mrp_run.status} MRP run."
            })
        
        serializer = ConvertToIndentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        try:
            indent = services.convert_shortages_to_indent(
                mrp_run=mrp_run,
                line_ids=serializer.validated_data.get('line_ids'),
                indent_type=serializer.validated_data.get('indent_type', 'PRODUCTION'),
                notes=serializer.validated_data.get('notes', ''),
                raised_by=request.user
            )
            
            # Return the created indent
            from apps.purchase.serializers import PurchaseIndentSerializer
            return Response({
                "message": f"Purchase Indent {indent.indent_number} created successfully.",
                "indent": PurchaseIndentSerializer(indent, context={'request': request}).data
            }, status=status.HTTP_201_CREATED)
            
        except ValidationError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['patch'], url_path='lines/(?P<line_id>[^/.]+)')
    def update_line(self, request, pk=None, line_id=None):
        """
        PATCH /api/mrp/runs/{id}/lines/{line_id}/
        Override MRP line quantities before conversion.
        """
        _require_pm(request)
        mrp_run = self.get_object()
        
        if mrp_run.status != 'COMPLETED':
            raise ValidationError({
                "detail": f"Cannot modify lines of a {mrp_run.status} MRP run."
            })
        
        try:
            line = MRPLine.objects.get(id=line_id, mrp_run=mrp_run)
        except MRPLine.DoesNotExist:
            raise ValidationError({"detail": "MRP line not found."})
        
        if line.indent_raised:
            raise ValidationError({
                "detail": "This line has already been converted to an indent."
            })
        
        serializer = MRPLineUpdateSerializer(data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        
        # Update the line
        if 'override_required_qty' in serializer.validated_data:
            new_qty = serializer.validated_data['override_required_qty']
            line.required_qty = new_qty
            # Recalculate shortage
            line.shortage_qty = max(
                new_qty - line.available_qty - line.on_order_qty,
                Decimal('0')
            )
            line.has_shortage = line.shortage_qty > 0
            line.save()
        
        # Update note if provided
        if 'note' in serializer.validated_data:
            # Could add a note field to MRPLine
            pass
        
        return Response(MRPLineSerializer(line, context={'request': request}).data)
    
    @action(detail=True, methods=['get'], url_path='export')
    def export(self, request, pk=None):
        """
        GET /api/mrp/runs/{id}/export/
        Export MRP data for Excel/CSV (returns JSON structured for export).
        """
        mrp_run = self.get_object()
        
        data = {
            'run_number': mrp_run.run_number,
            'project': mrp_run.project.project_number,
            'project_name': mrp_run.project.name,
            'bom': mrp_run.bom.bom_number,
            'bom_version': mrp_run.bom.version,
            'run_at': mrp_run.run_at.isoformat(),
            'status': mrp_run.status,
            'lines': []
        }
        
        for line in mrp_run.lines.all().order_by('item_class', 'engineering_item__item_code'):
            data['lines'].append({
                'item_code': line.engineering_item.item_code,
                'item_name': line.engineering_item.name,
                'item_class': line.item_class,
                'required_qty': str(line.required_qty),
                'uom': line.uom,
                'available_qty': str(line.available_qty),
                'on_order_qty': str(line.on_order_qty),
                'shortage_qty': str(line.shortage_qty),
                'has_shortage': line.has_shortage,
                'recommendation': line.recommendation,
                'bom_path': line.bom_path,
            })
        
        return Response(data)