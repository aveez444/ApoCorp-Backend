# apps/engineering/views.py
from django.db import transaction
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
    EngineeringItemMaster,
    EngineeringItemRevision,
    EngineeringDocument,
    EngineeringPackage,
)
from .serializers import (
    EngineeringItemMasterListSerializer,
    EngineeringItemMasterDetailSerializer,
    EngineeringItemRevisionSerializer,
    EngineeringDocumentSerializer,
    CreateRevisionSerializer,
    LinkInventorySerializer,
    EngineeringPackageSerializer,
    BOMLineSerializer,
)

from .services import (
    release_package, accept_package, reject_package, obsolete_package,
    create_and_release_package, create_change_notice,
)
# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_tenant_user(request):
    return TenantUser.objects.filter(
        user=request.user, tenant=request.tenant
    ).first()


def _require_engineering(request):
    tu = _get_tenant_user(request)
    # Engineering team role check - adjust based on your role system
    if not tu or tu.role not in ('engineering', 'manager'):
        raise PermissionDenied("Only engineering team members can perform this action.")


# ─────────────────────────────────────────────────────────────────────────────
# Engineering Item Master ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringItemViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Engineering Item Master API
    
    Filters:
    - ?class=A|B|C
    - ?category=Instrumentation
    - ?search=name (partial match)
    - ?has_inventory=true|false
    - ?is_active=true|false
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return EngineeringItemMasterListSerializer
        return EngineeringItemMasterDetailSerializer
    
    def get_queryset(self):
        qs = EngineeringItemMaster.objects.filter(
            tenant=self.request.tenant
        ).select_related('inventory_item', 'created_by').prefetch_related(
            'revisions', 'documents'
        )
        
        params = self.request.query_params
        
        if params.get('class'):
            qs = qs.filter(item_class=params['class'].upper())
        if params.get('category'):
            qs = qs.filter(category__iexact=params['category'])
        if params.get('search'):
            qs = qs.filter(name__icontains=params['search'])
        if params.get('has_inventory'):
            if params['has_inventory'].lower() == 'true':
                qs = qs.filter(inventory_item__isnull=False)
            else:
                qs = qs.filter(inventory_item__isnull=True)
        if params.get('is_active'):
            qs = qs.filter(is_active=params['is_active'].lower() == 'true')
        
        return qs.order_by('item_code')
    
    @action(detail=True, methods=['post'], url_path='revisions')
    def create_revision(self, request, pk=None):
        """
        POST /engineering/items/{id}/revisions/
        Create a new revision for this item. Auto-obsoletes the current revision.
        """
        _require_engineering(request)
        item = self.get_object()
        
        serializer = CreateRevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Check if revision already exists
        if EngineeringItemRevision.objects.filter(
            item=item, 
            revision=serializer.validated_data['revision']
        ).exists():
            raise ValidationError({
                "revision": f"Revision {serializer.validated_data['revision']} already exists for this item."
            })
        
        with transaction.atomic():
            # Create the new revision (it will auto-obsolete the current one)
            revision = EngineeringItemRevision.objects.create(
                item=item,
                revision=serializer.validated_data['revision'],
                drawing_number=serializer.validated_data.get('drawing_number', item.drawing_number),
                specification=serializer.validated_data.get('specification', item.specification),
                change_description=serializer.validated_data['change_description'],
                effective_date=serializer.validated_data['effective_date'],
                is_current=True,
                created_by=request.user,
            )
            
            # Update the item's drawing number if changed
            if serializer.validated_data.get('drawing_number'):
                item.drawing_number = serializer.validated_data['drawing_number']
                item.save(update_fields=['drawing_number'])
        
        return Response(
            EngineeringItemRevisionSerializer(revision, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['get'], url_path='revisions')
    def list_revisions(self, request, pk=None):
        """GET /engineering/items/{id}/revisions/ - List all revisions for this item"""
        item = self.get_object()
        revisions = item.revisions.all().order_by('-created_at')
        serializer = EngineeringItemRevisionSerializer(
            revisions, 
            many=True, 
            context={'request': request}
        )
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='documents')
    def upload_document(self, request, pk=None):
        """POST /engineering/items/{id}/documents/ - Upload a document for this item"""
        _require_engineering(request)
        item = self.get_object()
        
        file = request.FILES.get('file')
        if not file:
            raise ValidationError({"file": "File is required"})
        
        doc = EngineeringDocument.objects.create(
            item=item,
            doc_type=request.data.get('doc_type', 'OTHER'),
            title=request.data.get('title', file.name),
            description=request.data.get('description', ''),
            file=file,
            uploaded_by=request.user,
        )
        
        return Response(
            EngineeringDocumentSerializer(doc, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['get'], url_path='documents')
    def list_documents(self, request, pk=None):
        """GET /engineering/items/{id}/documents/ - List all documents for this item"""
        item = self.get_object()
        docs = item.documents.all().order_by('-uploaded_at')
        serializer = EngineeringDocumentSerializer(
            docs, 
            many=True, 
            context={'request': request}
        )
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], url_path='link-inventory')
    def link_inventory(self, request, pk=None):
        """
        POST /engineering/items/{id}/link-inventory/
        Link this Engineering Item to an Inventory Item Master.
        Body: {"inventory_item_id": "uuid"}
        """
        _require_engineering(request)
        item = self.get_object()
        
        if item.inventory_item_id:
            raise ValidationError({
                "detail": f"This item is already linked to {item.inventory_item.item_code}"
            })
        
        serializer = LinkInventorySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        inventory_item = serializer.validated_data['inventory_item_id']
        
        # Check if this inventory item is already linked to another engineering item
        if EngineeringItemMaster.objects.filter(
            inventory_item=inventory_item,
            tenant=request.tenant
        ).exists():
            raise ValidationError({
                "inventory_item_id": "This inventory item is already linked to another engineering item."
            })
        
        item.inventory_item = inventory_item
        item.save(update_fields=['inventory_item'])
        
        return Response(
            EngineeringItemMasterDetailSerializer(item, context={'request': request}).data
        )
    
    @action(detail=True, methods=['post'])
    def toggle_active(self, request, pk=None):
        """POST /engineering/items/{id}/toggle_active/ - Toggle is_active status"""
        _require_engineering(request)
        item = self.get_object()
        item.is_active = not item.is_active
        item.save(update_fields=['is_active'])
        return Response({
            "id": str(item.id),
            "item_code": item.item_code,
            "is_active": item.is_active
        })
    
    @action(detail=False, methods=['get'], url_path='dropdown')
    def dropdown(self, request):
        """
        GET /engineering/items/dropdown/
        Lightweight list for BOM dropdowns. Returns id, item_code, name, uom, item_class.
        """
        qs = EngineeringItemMaster.objects.filter(
            tenant=request.tenant,
            is_active=True
        ).only('id', 'item_code', 'name', 'uom', 'item_class')
        
        # Filter by class
        if request.query_params.get('class'):
            qs = qs.filter(item_class=request.query_params['class'].upper())
        
        # Search
        if request.query_params.get('search'):
            qs = qs.filter(name__icontains=request.query_params['search'])
        
        return Response([
            {
                'id': str(item.id),
                'item_code': item.item_code,
                'name': item.name,
                'uom': item.uom,
                'item_class': item.item_class,
            }
            for item in qs[:100]  # Limit for dropdown performance
        ])


# ─────────────────────────────────────────────────────────────────────────────
# Engineering Document ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringDocumentViewSet(ModelPermissionMixin, TenantModelViewSet):
    """Direct CRUD for Engineering Documents (for when you need to update/delete)"""
    serializer_class = EngineeringDocumentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def get_queryset(self):
        qs = EngineeringDocument.objects.filter(
            item__tenant=self.request.tenant
        ).select_related('item', 'revision', 'uploaded_by')
        
        if self.request.query_params.get('item'):
            qs = qs.filter(item__id=self.request.query_params['item'])
        if self.request.query_params.get('doc_type'):
            qs = qs.filter(doc_type=self.request.query_params['doc_type'].upper())
        
        return qs.order_by('-uploaded_at')
    
    def perform_update(self, serializer):
        _require_engineering(self.request)
        serializer.save()
    
    def perform_destroy(self, instance):
        _require_engineering(self.request)
        # Delete the file from storage
        if instance.file:
            instance.file.delete(save=False)
        instance.delete()

# apps/engineering/views.py - Add these to existing views

from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework import status
from django.db import transaction
from django.utils import timezone

from .models import EngineeringBOM, BOMLine
from .serializers import (
    EngineeringBOMListSerializer,
    EngineeringBOMDetailSerializer,
    BOMLineCreateSerializer,
    CreateBOMRevisionSerializer,
    BOMExplosionItemSerializer,
)
from .services import explode_bom, clone_bom_revision


# ─────────────────────────────────────────────────────────────────────────────
# Engineering BOM ViewSet
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringBOMViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Engineering BOM API
    
    Filters:
    - ?parent_item=<uuid>
    - ?status=DRAFT|APPROVED|OBSOLETE
    - ?is_active=true|false
    - ?search=name (partial match)
    """
    permission_classes = [IsAuthenticated]
    
    def get_serializer_class(self):
        if self.action == 'list':
            return EngineeringBOMListSerializer
        return EngineeringBOMDetailSerializer
    
    def get_queryset(self):
        qs = EngineeringBOM.objects.filter(
            tenant=self.request.tenant
        ).select_related('parent_item', 'created_by', 'approved_by').prefetch_related('lines')
        
        params = self.request.query_params
        
        if params.get('parent_item'):
            qs = qs.filter(parent_item__id=params['parent_item'])
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
        if params.get('is_active'):
            qs = qs.filter(is_active=params['is_active'].lower() == 'true')
        if params.get('search'):
            qs = qs.filter(name__icontains=params['search'])
        
        return qs.order_by('-created_at')
    
    @transaction.atomic
    def create(self, request, *args, **kwargs):
        """Create a new BOM with its top-level lines"""
        _require_engineering(request)
        
        lines_data = request.data.pop('lines', [])
        
        # Create BOM header
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        bom = serializer.save(created_by=request.user)
        
        # Create top-level lines
        for line_data in lines_data:
            BOMLine.objects.create(
                bom=bom,
                parent_line=None,  # Top-level
                **line_data
            )
        
        # Return the full BOM with lines
        return Response(
            EngineeringBOMDetailSerializer(bom, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['post'], url_path='lines')
    def add_line(self, request, pk=None):
        """
        POST /engineering/boms/{id}/lines/
        Add a new line to the BOM
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status not in ('DRAFT', 'PENDING_APPROVAL'):
            raise ValidationError({
                "detail": "Lines can only be added to DRAFT or PENDING_APPROVAL BOMs."
            })
        
        serializer = BOMLineCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        line = serializer.save(bom=bom)
        
        return Response(
            BOMLineSerializer(line, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['patch'], url_path='lines/(?P<line_id>[^/.]+)')
    def update_line(self, request, pk=None, line_id=None):
        """
        PATCH /engineering/boms/{id}/lines/{line_id}/
        Update a specific line
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status not in ('DRAFT', 'PENDING_APPROVAL'):
            raise ValidationError({
                "detail": "Lines can only be updated on DRAFT or PENDING_APPROVAL BOMs."
            })
        
        try:
            line = BOMLine.objects.get(id=line_id, bom=bom)
        except BOMLine.DoesNotExist:
            raise ValidationError({"detail": "Line not found in this BOM."})
        
        serializer = BOMLineCreateSerializer(line, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        
        return Response(BOMLineSerializer(line, context={'request': request}).data)
    
    @action(detail=True, methods=['delete'], url_path='lines/(?P<line_id>[^/.]+)')
    def delete_line(self, request, pk=None, line_id=None):
        """
        DELETE /engineering/boms/{id}/lines/{line_id}/
        Delete a specific line (only on DRAFT BOMs)
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status != 'DRAFT':
            raise ValidationError({
                "detail": "Lines can only be deleted from DRAFT BOMs."
            })
        
        try:
            line = BOMLine.objects.get(id=line_id, bom=bom)
        except BOMLine.DoesNotExist:
            raise ValidationError({"detail": "Line not found in this BOM."})
        
        # Check if this line has children
        if line.children.exists():
            raise ValidationError({
                "detail": "Cannot delete a line that has child components. Delete children first."
            })
        
        line.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """
        POST /engineering/boms/{id}/approve/
        Approve BOM for use in MRP. Manager/Engineering lead only.
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status != 'PENDING_APPROVAL':
            raise ValidationError({
                "detail": "Only PENDING_APPROVAL BOMs can be approved."
            })
        
        if not bom.lines.exists():
            raise ValidationError({
                "detail": "Cannot approve a BOM with no lines."
            })
        
        bom.status = 'APPROVED'
        bom.approved_by = request.user
        bom.approved_at = timezone.now()
        bom.save(update_fields=['status', 'approved_by', 'approved_at'])
        
        return Response(
            EngineeringBOMDetailSerializer(bom, context={'request': request}).data
        )
    
    @action(detail=True, methods=['post'], url_path='submit-for-approval')
    def submit_for_approval(self, request, pk=None):
        """
        POST /engineering/boms/{id}/submit-for-approval/
        Submit BOM for approval. Engineering team only.
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status != 'DRAFT':
            raise ValidationError({
                "detail": "Only DRAFT BOMs can be submitted for approval."
            })
        
        if not bom.lines.exists():
            raise ValidationError({
                "detail": "Cannot submit a BOM with no lines for approval."
            })
        
        bom.status = 'PENDING_APPROVAL'
        bom.save(update_fields=['status'])
        
        return Response(
            EngineeringBOMDetailSerializer(bom, context={'request': request}).data
        )
    
    @action(detail=True, methods=['post'], url_path='new-revision')
    def new_revision(self, request, pk=None):
        """
        POST /engineering/boms/{id}/new-revision/
        Clone this BOM into a new revision. Only approved BOMs can be revised.
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status != 'APPROVED':
            raise ValidationError({
                "detail": "Only APPROVED BOMs can be revised."
            })
        
        serializer = CreateBOMRevisionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        
        # Clone the BOM
        new_bom = clone_bom_revision(
            existing_bom=bom,
            new_version=serializer.validated_data['new_version'],
            effective_date=serializer.validated_data['effective_date'],
            description=serializer.validated_data.get('description', ''),
            created_by=request.user
        )
        
        return Response(
            EngineeringBOMDetailSerializer(new_bom, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['get'], url_path='explode')
    def explode(self, request, pk=None):
        """
        GET /engineering/boms/{id}/explode/
        Returns flat explosion of the BOM with rolled-up quantities.
        """
        bom = self.get_object()
        
        if bom.status != 'APPROVED':
            return Response({
                "warning": "This BOM is not approved. Explosion may contain draft changes.",
                "explosion": explode_bom(bom)
            })
        
        explosion = explode_bom(bom)
        return Response({
            "bom": {
                "id": str(bom.id),
                "bom_number": bom.bom_number,
                "name": bom.name,
                "version": bom.version,
            },
            "explosion": explosion
        })
    
    # New added for package option 
    @action(detail=True, methods=['post'], url_path='create-package')
    def create_package(self, request, pk=None):
        """
        POST /engineering/boms/{id}/create-package/
        Body: {
            "project_id": "uuid",
            "revision": "Rev A",
            "documents": ["doc-id-1", "doc-id-2"]  # optional
        }
        Creates and releases an engineering package for a project.
        """
        _require_engineering(request)
        bom = self.get_object()
        
        if bom.status != 'APPROVED':
            raise ValidationError({
                'detail': f'BOM must be APPROVED to create a package (status: {bom.status})'
            })
        
        project_id = request.data.get('project_id')
        if not project_id:
            raise ValidationError({'project_id': 'Required'})
        
        from apps.projects.models import Project
        try:
            project = Project.objects.get(id=project_id, tenant=request.tenant)
        except Project.DoesNotExist:
            raise ValidationError({'project_id': 'Project not found'})
        
        revision = request.data.get('revision', bom.version or 'Rev A')
        
        # Get documents if provided
        documents = []
        doc_ids = request.data.get('documents', [])
        if doc_ids:
            from .models import EngineeringDocument
            documents = EngineeringDocument.objects.filter(
                id__in=doc_ids,
                item__tenant=request.tenant
            )
        
        try:
            package = create_and_release_package(
                project=project,
                bom=bom,
                revision=revision,
                documents=documents,
                created_by=request.user
            )
        except ValueError as e:
            raise ValidationError({'detail': str(e)})
        
        from .serializers import EngineeringPackageSerializer
        return Response(
            EngineeringPackageSerializer(package, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )


    @action(detail=True, methods=['post'], url_path='create-ecn')
    def create_ecn(self, request, pk=None):
        """
        POST /engineering/boms/{id}/create-ecn/
        Body: {
            "package_id": "uuid",
            "new_revision": "Rev B",
            "change_reason": "Design improvement"
        }
        Creates a change notice for a package revision.
        """
        _require_engineering(request)
        new_bom = self.get_object()
        
        if new_bom.status != 'APPROVED':
            raise ValidationError({
                'detail': f'New BOM must be APPROVED (status: {new_bom.status})'
            })
        
        package_id = request.data.get('package_id')
        if not package_id:
            raise ValidationError({'package_id': 'Required'})
        
        from .models import EngineeringPackage
        try:
            old_package = EngineeringPackage.objects.get(
                id=package_id, 
                project__tenant=request.tenant
            )
        except EngineeringPackage.DoesNotExist:
            raise ValidationError({'package_id': 'Package not found'})
        
        new_revision = request.data.get('new_revision')
        if not new_revision:
            raise ValidationError({'new_revision': 'Required'})
        
        change_reason = request.data.get('change_reason', '')
        if not change_reason:
            raise ValidationError({'change_reason': 'Required'})
        
        try:
            ecn = create_change_notice(
                old_package=old_package,
                new_bom=new_bom,
                new_revision=new_revision,
                change_reason=change_reason,
                created_by=request.user
            )
        except ValueError as e:
            raise ValidationError({'detail': str(e)})
        
        from .serializers import EngineeringPackageChangeNoticeSerializer
        return Response(
            EngineeringPackageChangeNoticeSerializer(ecn, context={'request': request}).data,
            status=status.HTTP_201_CREATED
        )
    
    @action(detail=True, methods=['get'], url_path='tree')
    def tree(self, request, pk=None):
        """
        GET /engineering/boms/{id}/tree/
        Returns the full BOM tree with all levels.
        """
        bom = self.get_object()
        serializer = EngineeringBOMDetailSerializer(bom, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], url_path='active-for-item')
    def active_for_item(self, request):
        """
        GET /engineering/boms/active-for-item/?item_id=<uuid>
        Returns the active BOM for a given parent item.
        """
        item_id = request.query_params.get('item_id')
        if not item_id:
            raise ValidationError({"item_id": "Required"})
        
        try:
            bom = EngineeringBOM.objects.get(
                parent_item__id=item_id,
                is_active=True,
                status='APPROVED',
                tenant=request.tenant
            )
            return Response(EngineeringBOMDetailSerializer(bom, context={'request': request}).data)
        except EngineeringBOM.DoesNotExist:
            return Response({"detail": "No active BOM found for this item."}, status=status.HTTP_404_NOT_FOUND)
        

class EngineeringPackageViewSet(ModelPermissionMixin, TenantModelViewSet):
    """
    Engineering Package API.
 
    Status is NEVER changed via PATCH/PUT — only through the transition
    actions below, which go through apps/engineering/services.py.
 
    Filters:
    - ?project=<uuid>
    - ?status=DRAFT|UNDER_REVIEW|RELEASED|ACCEPTED|REJECTED|OBSOLETE
    """
    permission_classes = [IsAuthenticated]
    serializer_class = EngineeringPackageSerializer
 
    def get_queryset(self):
        qs = EngineeringPackage.objects.filter(
            tenant=self.request.tenant
        ).select_related('project', 'source_bom', 'created_by', 'released_by', 'accepted_by')
 
        params = self.request.query_params
        if params.get('project'):
            qs = qs.filter(project__id=params['project'])
        if params.get('status'):
            qs = qs.filter(status=params['status'].upper())
 
        return qs.order_by('-created_at')
 
    @action(detail=True, methods=['post'], url_path='release')
    def release(self, request, pk=None):
        """POST /engineering/packages/{id}/release/"""
        _require_engineering(request)
        package = self.get_object()
        try:
            result = release_package(package, request.user)
        except ValueError as e:
            raise ValidationError({'detail': str(e)})
        return Response(EngineeringPackageSerializer(result, context={'request': request}).data)
 
    @action(detail=True, methods=['post'], url_path='accept')
    def accept(self, request, pk=None):
        """POST /engineering/packages/{id}/accept/  Body: {"notes": "..."}  — Project Manager only."""
        package = self.get_object()
        try:
            result = accept_package(package, request.user, request.data.get('notes', ''))
        except ValueError as e:
            raise ValidationError({'detail': str(e)})
        return Response(EngineeringPackageSerializer(result, context={'request': request}).data)
 
    @action(detail=True, methods=['post'], url_path='reject')
    def reject(self, request, pk=None):
        """POST /engineering/packages/{id}/reject/  Body: {"reason": "..."}  — Project Manager only."""
        package = self.get_object()
        try:
            result = reject_package(package, request.user, request.data.get('reason', ''))
        except ValueError as e:
            raise ValidationError({'detail': str(e)})
        return Response(EngineeringPackageSerializer(result, context={'request': request}).data)
 
    @action(detail=True, methods=['post'], url_path='obsolete')
    def obsolete(self, request, pk=None):
        """POST /engineering/packages/{id}/obsolete/  Body: {"reason": "..."}"""
        _require_engineering(request)
        package = self.get_object()
        try:
            result = obsolete_package(package, request.user, request.data.get('reason', ''))
        except ValueError as e:
            raise ValidationError({'detail': str(e)})
        return Response(EngineeringPackageSerializer(result, context={'request': request}).data)