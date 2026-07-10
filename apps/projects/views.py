# apps/projects/views.py - Complete updated file

from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.parsers import JSONParser, MultiPartParser, FormParser  # Import parsers
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.viewsets import TenantModelViewSet
from core.mixins import ModelPermissionMixin
from apps.accounts.models import TenantUser

from .models import Project, ProjectCostEntry, ProjectMilestone, ProjectDocument
from .serializers import (
    ProjectListSerializer, ProjectDetailSerializer,
    ProjectCostEntrySerializer, ProjectMilestoneSerializer, ProjectDocumentSerializer,
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


class ProjectViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Projects API endpoint.

    Filtering:
    - ?status=DRAFT|PLANNING|ACTIVE|ON_HOLD|COMPLETED|CANCELLED
    - ?customer=<uuid>
    - ?project_manager=<user_id>
    - ?search=name (partial match)
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]  # ← ADD THIS

    def get_serializer_class(self):
        if self.action == 'list':
            return ProjectListSerializer
        return ProjectDetailSerializer

    def get_queryset(self):
        qs = Project.objects.filter(tenant=self.request.tenant).select_related(
            'customer', 'project_manager', 'sales_order', 'bom'
        ).prefetch_related('milestones', 'documents')

        params = self.request.query_params
        tu = _get_tenant_user(self.request)

        if tu and tu.role == 'employee':
            qs = qs.filter(project_manager=self.request.user)

        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('customer'):
            qs = qs.filter(customer__id=params['customer'])
        if params.get('project_manager'):
            qs = qs.filter(project_manager__id=params['project_manager'])
        if params.get('search'):
            qs = qs.filter(name__icontains=params['search'])

        return qs.order_by('-created_at')

    @action(detail=True, methods=['post'], url_path='assign-bom')
    def assign_bom(self, request, pk=None):
        """POST /projects/{id}/assign-bom/ Body: { "bom_id": "<uuid>" }"""
        _require_manager(request)
        project = self.get_object()
        bom_id = request.data.get('bom_id')
        if not bom_id:
            raise ValidationError({"bom_id": "Required."})

        from apps.engineering.models import EngineeringBOM
        try:
            bom = EngineeringBOM.objects.get(id=bom_id, tenant=request.tenant)
        except EngineeringBOM.DoesNotExist:
            raise ValidationError({"bom_id": "BOM not found in this tenant."})

        if bom.status != 'APPROVED':
            raise ValidationError({
                "bom_id": f"BOM is not approved (status: {bom.status})."
            })

        project.bom = bom
        project.save(update_fields=['bom'])
        return Response(ProjectDetailSerializer(project, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """DRAFT/PLANNING → ACTIVE."""
        _require_manager(request)
        project = self.get_object()
        if project.status not in ('DRAFT', 'PLANNING'):
            raise ValidationError({"detail": f"Cannot start a project in {project.status} status."})
        project.status = 'ACTIVE'
        project.start_date = timezone.now().date()
        project.save(update_fields=['status', 'start_date'])
        return Response(ProjectDetailSerializer(project, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """ACTIVE/ON_HOLD → COMPLETED."""
        _require_manager(request)
        project = self.get_object()
        if project.status not in ('ACTIVE', 'ON_HOLD'):
            raise ValidationError({"detail": f"Cannot complete a project in {project.status} status."})
        project.status = 'COMPLETED'
        project.actual_end_date = timezone.now().date()
        project.save(update_fields=['status', 'actual_end_date'])
        return Response(ProjectDetailSerializer(project, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def hold(self, request, pk=None):
        """ACTIVE → ON_HOLD."""
        _require_manager(request)
        project = self.get_object()
        if project.status != 'ACTIVE':
            raise ValidationError({"detail": "Only ACTIVE projects can be put on hold."})
        project.status = 'ON_HOLD'
        project.save(update_fields=['status'])
        return Response(ProjectDetailSerializer(project, context={'request': request}).data)

    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Any non-completed status → CANCELLED."""
        _require_manager(request)
        project = self.get_object()
        if project.status == 'COMPLETED':
            raise ValidationError({"detail": "Cannot cancel a COMPLETED project."})
        reason = (request.data.get('reason') or '').strip()
        if not reason:
            raise ValidationError({"reason": "Cancellation reason is required."})
        project.status = 'CANCELLED'
        project.save(update_fields=['status'])
        return Response({"message": f"Project {project.project_number} cancelled.", "reason": reason})

    @action(detail=True, methods=['post'], url_path='assign-manager')
    def assign_manager(self, request, pk=None):
        """POST /projects/{id}/assign-manager/ Body: { "user_id": <id> }"""
        _require_manager(request)
        project = self.get_object()

        user_id = request.data.get('user_id')
        if not user_id:
            raise ValidationError({"user_id": "Required."})

        target_tu = TenantUser.objects.filter(
            user__id=user_id, tenant=request.tenant
        ).select_related('user').first()

        if not target_tu:
            raise ValidationError({"user_id": "User not found in this tenant."})

        if target_tu.role != 'manager':
            raise ValidationError({
                "user_id": f"Only users with the 'manager' role can be assigned as Project Manager."
            })

        project.project_manager = target_tu.user
        project.save(update_fields=['project_manager'])
        return Response(ProjectDetailSerializer(project, context={'request': request}).data)

    @action(detail=False, methods=['get'], url_path='eligible-managers')
    def eligible_managers(self, request):
        """GET /projects/eligible-managers/ — users assignable as PM."""
        managers = TenantUser.objects.filter(
            tenant=request.tenant, role='manager'
        ).select_related('user')
        return Response([
            {"user_id": tu.user.id, "name": tu.user.get_full_name() or tu.user.username}
            for tu in managers
        ])

    @action(detail=True, methods=['get'], url_path='dashboard')
    def dashboard(self, request, pk=None):
        """GET /projects/{id}/dashboard/"""
        project = self.get_object()
        data = services.get_dashboard_data(project)
        return Response(data)

    @action(detail=True, methods=['get'], url_path='cost-breakdown')
    def cost_breakdown(self, request, pk=None):
        """GET /projects/{id}/cost-breakdown/"""
        project = self.get_object()
        entries = project.cost_entries.all().select_related('recorded_by')
        serializer = ProjectCostEntrySerializer(entries, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'], url_path='available-oas')
    def available_oas(self, request):
        """GET /projects/available-oas/"""
        from apps.orders.models import OrderAcknowledgement
        oas = (
            OrderAcknowledgement.objects
            .filter(tenant=request.tenant, status='CONVERTED')
            .exclude(order__project__isnull=False)
            .select_related('quotation__enquiry__customer')
            .order_by('-created_at')
        )
        return Response([
            {
                'oa_id': str(oa.id),
                'oa_number': oa.oa_number,
                'customer_name': oa.customer.company_name,
                'enquiry_number': oa.quotation.enquiry.enquiry_number,
                'total_value': oa.total_value,
                'currency': oa.currency,
                'status': oa.status,
            }
            for oa in oas
        ])

    @action(detail=False, methods=['post'], url_path='create-from-oa', parser_classes=[JSONParser])
    def create_from_oa(self, request):
        """
        POST /projects/create-from-oa/
        Body: {
          "oa_id": "<uuid>",
          "project_manager_id": <user_id>,
          "start_date": "YYYY-MM-DD"
        }
        """
        _require_manager(request)

        oa_id = request.data.get('oa_id')
        if not oa_id:
            raise ValidationError({"oa_id": "Required."})

        from apps.orders.models import OrderAcknowledgement
        try:
            oa = OrderAcknowledgement.objects.get(id=oa_id, tenant=request.tenant)
        except OrderAcknowledgement.DoesNotExist:
            raise ValidationError({"oa_id": "OA not found in this tenant."})

        project_manager = None
        pm_id = request.data.get('project_manager_id')
        if pm_id:
            from apps.accounts.models import TenantUser
            target_tu = TenantUser.objects.filter(
                user__id=pm_id, tenant=request.tenant, role='manager'
            ).select_related('user').first()
            if not target_tu:
                raise ValidationError({
                    "project_manager_id": "User not found or does not hold the 'manager' role."
                })
            project_manager = target_tu.user

        start_date = None
        if request.data.get('start_date'):
            from datetime import date
            try:
                start_date = date.fromisoformat(request.data['start_date'])
            except ValueError:
                raise ValidationError({"start_date": "Use YYYY-MM-DD format."})

        try:
            project = services.create_project_from_oa(
                oa=oa,
                project_manager=project_manager,
                start_date=start_date,
            )
        except ValueError as e:
            raise ValidationError({"detail": str(e)})

        return Response(
            ProjectDetailSerializer(project, context={'request': request}).data,
            status=status.HTTP_201_CREATED,
        )
    @action(detail=True, methods=['get', 'post'], url_path='milestones')
    def project_milestones(self, request, pk=None):
        """GET/POST /projects/{id}/milestones/"""
        project = self.get_object()
        
        if request.method == 'GET':
            milestones = project.milestones.all()
            serializer = ProjectMilestoneSerializer(milestones, many=True)
            return Response(serializer.data)
        
        # POST - create new milestone
        serializer = ProjectMilestoneSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(project=project)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get', 'post'], url_path='documents')
    def project_documents(self, request, pk=None):
        """GET/POST /projects/{id}/documents/"""
        project = self.get_object()
        
        if request.method == 'GET':
            documents = project.documents.all()
            serializer = ProjectDocumentSerializer(documents, many=True)
            return Response(serializer.data)
        
        # POST - upload document
        serializer = ProjectDocumentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(project=project, uploaded_by=request.user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get', 'post'], url_path='cost-entries')
    def project_cost_entries(self, request, pk=None):
        """GET/POST /projects/{id}/cost-entries/"""
        project = self.get_object()
        
        if request.method == 'GET':
            entries = project.cost_entries.all().select_related('recorded_by')
            serializer = ProjectCostEntrySerializer(entries, many=True)
            return Response(serializer.data)
        
        # POST - create cost entry
        serializer = ProjectCostEntrySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save(project=project, recorded_by=request.user)
        
        # Update project cost fields
        services.record_cost_entry(
            project=project,
            cost_type=serializer.validated_data['cost_type'],
            amount=serializer.validated_data['amount'],
            description=serializer.validated_data.get('description', ''),
            recorded_by=request.user,
        )
        
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'], url_path='milestones/(?P<milestone_id>[^/.]+)')
    def milestone_detail(self, request, milestone_id=None, pk=None):
        """GET/PATCH/DELETE /projects/milestones/{milestone_id}/"""
        try:
            milestone = ProjectMilestone.objects.get(id=milestone_id, project__tenant=request.tenant)
        except ProjectMilestone.DoesNotExist:
            return Response({"error": "Milestone not found"}, status=404)
        
        if request.method == 'GET':
            serializer = ProjectMilestoneSerializer(milestone)
            return Response(serializer.data)
        
        if request.method == 'PATCH':
            serializer = ProjectMilestoneSerializer(milestone, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        
        if request.method == 'DELETE':
            milestone.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=False, methods=['get', 'patch', 'delete'], url_path='documents/(?P<doc_id>[^/.]+)')
    def document_detail(self, request, doc_id=None, pk=None):
        """GET/PATCH/DELETE /projects/documents/{doc_id}/"""
        try:
            doc = ProjectDocument.objects.get(id=doc_id, project__tenant=request.tenant)
        except ProjectDocument.DoesNotExist:
            return Response({"error": "Document not found"}, status=404)
        
        if request.method == 'GET':
            serializer = ProjectDocumentSerializer(doc)
            return Response(serializer.data)
        
        if request.method == 'PATCH':
            serializer = ProjectDocumentSerializer(doc, data=request.data, partial=True)
            serializer.is_valid(raise_exception=True)
            serializer.save()
            return Response(serializer.data)
        
        if request.method == 'DELETE':
            doc.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        
