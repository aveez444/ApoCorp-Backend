# apps/engineering/models.py
import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class ItemClass(models.TextChoices):
    A = 'A', 'Class A - High Value (70-80% of spend)'
    B = 'B', 'Class B - Medium Value (15-20% of spend)'
    C = 'C', 'Class C - Low Value / Consumables'


class DocumentType(models.TextChoices):
    DRAWING = 'DRAWING', 'Engineering Drawing'
    SPEC = 'SPEC', 'Specification'
    DATASHEET = 'DATASHEET', 'Technical Datasheet'
    CERTIFICATE = 'CERTIFICATE', 'Test Certificate / Calibration'
    MANUAL = 'MANUAL', 'User Manual'
    OTHER = 'OTHER', 'Other Document'


# ─────────────────────────────────────────────────────────────────────────────
# Engineering Item Master (EIM)
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringItemMaster(TenantModelMixin):
    """
    Engineering identity of an item. Contains drawing numbers, specifications,
    make/model, and ABC classification. Linked 1:1 to inventory ItemMaster.
    
    The EIM is created by the Engineering team. When procurement is needed,
    the Stores team creates the linked Inventory ItemMaster.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item_code = models.CharField(
        max_length=30, 
        blank=True,
        help_text="Auto-generated: ENG-00001"
    )
    
    name = models.CharField(max_length=255, help_text="Engineering name / description")
    description = models.TextField(blank=True)
    
    # ABC Classification
    item_class = models.CharField(
        max_length=1, 
        choices=ItemClass.choices, 
        default=ItemClass.B,
        help_text="A=High Value, B=Medium, C=Low Value/Consumable"
    )
    
    # Categorization
    category = models.CharField(
        max_length=100, 
        blank=True,
        help_text="e.g. Instrumentation, Electrical, Mechanical"
    )
    sub_category = models.CharField(max_length=100, blank=True)
    
    # Engineering-specific fields
    drawing_number = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Primary drawing reference number"
    )
    current_revision = models.CharField(
        max_length=10, 
        blank=True,
        help_text="Current active revision (e.g. Rev C)"
    )
    
    specification = models.TextField(
        blank=True,
        help_text="Technical specification text"
    )
    
    # Vendor/Manufacturer info
    make = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Preferred manufacturer brand"
    )
    model = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Model / part number from manufacturer"
    )
    manufacturer_part_number = models.CharField(
        max_length=100, 
        blank=True,
        help_text="MPN"
    )
    customer_part_number = models.CharField(
        max_length=100, 
        blank=True,
        help_text="CPN - Customer-assigned part number"
    )
    
    # Unit of measure
    uom = models.CharField(
        max_length=20, 
        help_text="Unit of measure for engineering calculations"
    )
    
    # Link to Inventory Item Master (created when procurement is needed)
    inventory_item = models.OneToOneField(
        'inventory.ItemMaster',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='engineering_item',
        help_text="Link to Inventory Item Master (created by Stores when procurement needed)"
    )
    
    # Status
    is_active = models.BooleanField(default=True)
    
    # Audit
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='created_engineering_items'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['item_code']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'item_code'], 
                name='unique_eng_item_code_per_tenant'
            ),
        ]
        indexes = [
            models.Index(fields=['tenant', 'item_class']),
            models.Index(fields=['tenant', 'category']),
            models.Index(fields=['tenant', 'is_active']),
            models.Index(fields=['drawing_number']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate item_code
        if not self.item_code:
            last = (EngineeringItemMaster.objects
                    .filter(tenant=self.tenant, item_code__startswith='ENG-')
                    .order_by('-created_at')
                    .first())
            if last and last.item_code:
                try:
                    n = int(last.item_code[4:]) + 1
                except (ValueError, IndexError):
                    n = 1
            else:
                n = 1
            self.item_code = f'ENG-{n:05d}'
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f'{self.item_code} — {self.name}'
    
    @property
    def has_inventory_item(self):
        """Check if this engineering item has been linked to inventory"""
        return self.inventory_item_id is not None


# ─────────────────────────────────────────────────────────────────────────────
# Engineering Item Revision (History)
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringItemRevision(models.Model):
    """
    Revision history for an Engineering Item. Each revision captures a
    snapshot of the drawing number and specification at that point.
    Only one revision can be 'current' per item.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        EngineeringItemMaster, 
        on_delete=models.CASCADE,
        related_name='revisions'
    )
    
    revision = models.CharField(
        max_length=10,
        help_text="Revision identifier (e.g. Rev A, Rev B, Rev C)"
    )
    
    # Snapshot at this revision
    drawing_number = models.CharField(
        max_length=100, 
        blank=True,
        help_text="Drawing number at this revision"
    )
    specification = models.TextField(
        blank=True,
        help_text="Spec at this revision - full snapshot"
    )
    change_description = models.TextField(
        blank=True,
        help_text="What changed vs. previous revision"
    )
    
    effective_date = models.DateField(
        help_text="Date this revision becomes effective"
    )
    obsoleted_date = models.DateField(
        null=True, blank=True,
        help_text="Set when superseded by next revision"
    )
    
    is_current = models.BooleanField(
        default=False,
        help_text="Only one revision per item should be current"
    )
    
    # Document attachment (drawing PDF, spec doc, etc.)
    document = models.FileField(
        upload_to='engineering/revisions/%Y/%m/',
        null=True, blank=True,
        help_text="Drawing PDF or spec document for this revision"
    )
    
    # Audit
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(
                fields=['item', 'revision'],
                name='unique_revision_per_item'
            ),
            # Ensure only one current revision per item
            models.UniqueConstraint(
                fields=['item'],
                condition=models.Q(is_current=True),
                name='unique_current_revision_per_item'
            )
        ]
    
    def save(self, *args, **kwargs):
        # If this revision is marked as current, obsolete all others
        if self.is_current:
            EngineeringItemRevision.objects.filter(
                item=self.item,
                is_current=True
            ).exclude(pk=self.pk).update(
                is_current=False,
                obsoleted_date=timezone.now().date()
            )
            
            # Update the parent item's current_revision field
            self.item.current_revision = self.revision
            self.item.save(update_fields=['current_revision'])
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f'{self.item.item_code} — {self.revision}'


# ─────────────────────────────────────────────────────────────────────────────
# Engineering Document
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringDocument(models.Model):
    """
    Documents attached to an Engineering Item. Can be linked to a specific
    revision or just to the item in general.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item = models.ForeignKey(
        EngineeringItemMaster, 
        on_delete=models.CASCADE,
        related_name='documents'
    )
    
    # Optional link to a specific revision
    revision = models.ForeignKey(
        EngineeringItemRevision,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='documents'
    )
    
    doc_type = models.CharField(
        max_length=30, 
        choices=DocumentType.choices,
        default=DocumentType.DRAWING
    )
    
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    file = models.FileField(
        upload_to='engineering/documents/%Y/%m/',
        help_text="Upload the document file"
    )
    
    # Version tracking
    version = models.CharField(
        max_length=10, 
        blank=True,
        help_text="Document version (if different from revision)"
    )
    
    # Audit
    uploaded_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-uploaded_at']
    
    def __str__(self):
        return f'{self.item.item_code} — {self.title}'
    
# apps/engineering/models.py - Add these to existing models

# ─────────────────────────────────────────────────────────────────────────────
# Engineering BOM (Bill of Materials)
# ─────────────────────────────────────────────────────────────────────────────

class EngineeringBOM(TenantModelMixin):
    """
    Bill of Materials header. Defines what a finished product or assembly
    is made of. Each BOM version is a separate record.
    
    Versioning: When a new revision is created, old ones remain for historical
    reference. Projects using Rev A continue to reference Rev A; new projects
    get Rev B.
    """
    
    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PENDING_APPROVAL', 'Pending Approval'),
        ('APPROVED', 'Approved'),
        ('OBSOLETE', 'Obsolete'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bom_number = models.CharField(
        max_length=30, 
        unique=True, 
        blank=True,
        help_text="Auto-generated: BOM-2026-0001"
    )
    
    name = models.CharField(
        max_length=255,
        help_text="e.g. SWAS Panel BOM"
    )
    
    # The finished product / top-level assembly
    parent_item = models.ForeignKey(
        'EngineeringItemMaster',
        on_delete=models.PROTECT,
        related_name='boms',
        help_text="The finished product / top-level assembly"
    )
    
    version = models.CharField(
        max_length=10,
        help_text="BOM Rev A, BOM Rev B ..."
    )
    
    description = models.TextField(blank=True)
    
    # Dates
    effective_date = models.DateField(
        help_text="Date this BOM version is valid from"
    )
    obsolete_date = models.DateField(
        null=True, blank=True,
        help_text="Set when superseded"
    )
    
    # Status
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='DRAFT'
    )
    is_active = models.BooleanField(
        default=True,
        help_text="Only one active BOM per parent_item at a time"
    )
    
    # Approval
    approved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='approved_boms'
    )
    approved_at = models.DateTimeField(null=True, blank=True)
    
    # Audit
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='created_boms'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        constraints = [
            # Only one active BOM per parent item
            models.UniqueConstraint(
                fields=['parent_item'],
                condition=models.Q(is_active=True),
                name='unique_active_bom_per_parent'
            )
        ]
        indexes = [
            models.Index(fields=['parent_item', 'is_active']),
            models.Index(fields=['status']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-generate bom_number
        if not self.bom_number:
            year = timezone.now().year
            prefix = f"BOM-{year}-"
            last = (EngineeringBOM.objects
                    .filter(bom_number__startswith=prefix)
                    .order_by('-bom_number')
                    .first())
            if last and last.bom_number:
                try:
                    seq = int(last.bom_number.split('-')[-1]) + 1
                except (ValueError, IndexError):
                    seq = 1
            else:
                seq = 1
            self.bom_number = f"{prefix}{seq:04d}"
        
        # If this BOM is active, deactivate others for same parent
        if self.is_active:
            EngineeringBOM.objects.filter(
                parent_item=self.parent_item,
                is_active=True
            ).exclude(pk=self.pk).update(
                is_active=False,
                status='OBSOLETE',
                obsolete_date=timezone.now().date()
            )
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.bom_number} — {self.name} ({self.version})"


class BOMLine(models.Model):
    """
    Each component in the BOM. Supports multi-level BOM via parent_line.
    
    parent_line = NULL → top-level component
    parent_line = BOMLine → sub-component of that assembly
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    bom = models.ForeignKey(
        EngineeringBOM, 
        on_delete=models.CASCADE,
        related_name='lines'
    )
    
    # Self-referential for multi-level BOM
    parent_line = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True, blank=True,
        related_name='children',
        help_text="NULL = top-level component"
    )
    
    # The component
    item = models.ForeignKey(
        'EngineeringItemMaster',
        on_delete=models.PROTECT,
        related_name='bom_lines'
    )
    
    quantity = models.DecimalField(
        max_digits=15, 
        decimal_places=3,
        help_text="Quantity per parent assembly"
    )
    uom = models.CharField(
        max_length=20,
        help_text="Unit of measure"
    )
    
    # Denormalized from EngineeringItemMaster for fast MRP queries
    item_class = models.CharField(
        max_length=1, 
        choices=ItemClass.choices,
        blank=True,
        help_text="Denormalized from item for fast MRP"
    )
    
    # Engineering-specific fields
    reference_designator = models.CharField(
        max_length=100, 
        blank=True,
        help_text="e.g. PLC-001, V-003 (from engineering drawing)"
    )
    note = models.TextField(
        blank=True,
        help_text="Engineering notes / substitution info"
    )
    
    is_phantom = models.BooleanField(
        default=False,
        help_text="Phantom assemblies are exploded through, not procured"
    )
    
    # Line-level date overrides
    effective_date = models.DateField(
        null=True, blank=True,
        help_text="Override BOM-level effective date"
    )
    obsolete_date = models.DateField(
        null=True, blank=True
    )
    
    # Display order
    sort_order = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['sort_order', 'id']
        indexes = [
            models.Index(fields=['bom', 'parent_line']),
            models.Index(fields=['item']),
        ]
    
    def save(self, *args, **kwargs):
        # Auto-populate item_class from item
        if self.item_id and not self.item_class:
            try:
                self.item_class = self.item.item_class
            except EngineeringItemMaster.DoesNotExist:
                pass
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.bom.bom_number} → {self.item.item_code} × {self.quantity}"
    
# apps/engineering/models.py - Add this
class EngineeringPackage(TenantModelMixin):
    """
    A complete engineering deliverable for a specific project.
    IMMUTABLE once it leaves DRAFT/UNDER_REVIEW.

    Status must NEVER be set directly (package.status = 'X'; package.save()).
    Always go through apps/engineering/services.py, or call
    package.transition_to(new_status, user, reason).
    """

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('UNDER_REVIEW', 'Under Review'),
        ('RELEASED', 'Released'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED', 'Rejected'),
        ('OBSOLETE', 'Obsolete'),
    ]

    # Single source of truth for legal status moves.
    ALLOWED_TRANSITIONS = {
        'DRAFT': ['UNDER_REVIEW', 'RELEASED', 'OBSOLETE'],
        'UNDER_REVIEW': ['DRAFT', 'RELEASED', 'OBSOLETE'],
        'RELEASED': ['ACCEPTED', 'REJECTED', 'OBSOLETE'],
        'ACCEPTED': ['OBSOLETE'],
        'REJECTED': ['OBSOLETE'],
        'OBSOLETE': [],  # terminal
    }

    # Statuses past which the package is frozen — no field edits, only
    # status transitions permitted by ALLOWED_TRANSITIONS.
    FROZEN_STATUSES = ('RELEASED', 'ACCEPTED', 'REJECTED', 'OBSOLETE')

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    package_number = models.CharField(max_length=30, blank=True)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='engineering_packages'
    )

    bom_snapshot = models.JSONField(
        default=dict,
        help_text="Full BOM snapshot with all components (frozen on release)"
    )
    source_bom = models.ForeignKey(
        'EngineeringBOM',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='snapshots',
        help_text="Original BOM this was created from (for traceability)"
    )

    version = models.CharField(max_length=10, help_text="Package version (e.g., v1.0, v2.0)")

    document_snapshots = models.JSONField(default=list, help_text="Frozen document snapshots")

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')

    released_at = models.DateTimeField(null=True, blank=True)
    released_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='released_packages'
    )

    accepted_at = models.DateTimeField(null=True, blank=True)
    accepted_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='accepted_packages'
    )
    acceptance_notes = models.TextField(blank=True)

    rejected_at = models.DateTimeField(null=True, blank=True)
    rejected_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='rejected_packages'
    )
    rejection_reason = models.TextField(blank=True)

    change_description = models.TextField(blank=True)

    previous_package = models.ForeignKey(
        'self',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='next_package',
        help_text="Previous version of this package"
    )

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        unique_together = [['project', 'version']]
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['status', 'released_at']),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            try:
                original = EngineeringPackage.objects.get(pk=self.pk)
            except EngineeringPackage.DoesNotExist:
                original = None

            if original and original.status in self.FROZEN_STATUSES:
                if self.status != original.status:
                    if not original.can_transition_to(self.status):
                        raise ValueError(
                            f"Cannot transition from {original.status} to {self.status}. "
                            f"Allowed: {original.ALLOWED_TRANSITIONS.get(original.status, [])}"
                        )
                else:
                    raise ValueError(
                        f"Cannot modify a package that is {original.status}. "
                        "Create a new package version instead."
                    )

        if not self.package_number:
            year = timezone.now().year
            prefix = f"EPKG-{year}-"
            last = (EngineeringPackage.objects
                    .filter(package_number__startswith=prefix)
                    .order_by('-package_number')
                    .first())
            if last and last.package_number:
                try:
                    seq = int(last.package_number.split('-')[-1]) + 1
                except (ValueError, IndexError):
                    seq = 1
            else:
                seq = 1
            self.package_number = f"{prefix}{seq:04d}"

        super().save(*args, **kwargs)

    # ─── Status transition machinery ─────────────────────────────────────

    def can_transition_to(self, new_status):
        if self.status == new_status:
            return True
        return new_status in self.ALLOWED_TRANSITIONS.get(self.status, [])

    def get_allowed_transitions(self):
        return self.ALLOWED_TRANSITIONS.get(self.status, [])

    def transition_to(self, new_status, user=None, reason=''):
        """
        The ONE way to move a package between statuses.
        Delegates to the service layer so notifications, snapshots and
        history stay in one place.
        """
        if not self.can_transition_to(new_status):
            raise ValueError(
                f"Cannot transition from {self.status} to {new_status}. "
                f"Allowed: {self.get_allowed_transitions()}"
            )

        if new_status == 'RELEASED':
            from .services import release_package
            return release_package(self, user)
        elif new_status == 'ACCEPTED':
            from .services import accept_package
            return accept_package(self, user, reason)
        elif new_status == 'REJECTED':
            from .services import reject_package
            return reject_package(self, user, reason)
        elif new_status == 'OBSOLETE':
            from .services import obsolete_package
            return obsolete_package(self, user, reason)
        else:
            # DRAFT <-> UNDER_REVIEW — no side effects, direct is fine.
            self.status = new_status
            self.save()
            return self

    @property
    def is_frozen(self):
        return self.status in self.FROZEN_STATUSES

    def release(self, user):
        """Convenience wrapper — delegates to services.release_package via transition_to."""
        return self.transition_to('RELEASED', user)

    def _get_document_snapshots(self):
        snapshots = []
        for doc in self.documents.all():
            snapshots.append({
                'id': str(doc.id),
                'title': doc.title,
                'doc_type': doc.doc_type,
                'file_url': doc.file.url if doc.file else None,
                'version': doc.version,
                'uploaded_at': doc.uploaded_at.isoformat() if doc.uploaded_at else None,
            })
        return snapshots


class PackageHistoryEntry(models.Model):
    """Audit trail of package status changes. The only history model — do not re-add PackageHistory."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    package = models.ForeignKey(EngineeringPackage, on_delete=models.CASCADE, related_name='history')

    from_status = models.CharField(max_length=20, blank=True)
    to_status = models.CharField(max_length=20)

    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    changed_at = models.DateTimeField(auto_now_add=True)
    comment = models.TextField(blank=True)

    class Meta:
        ordering = ['changed_at']

    def __str__(self):
        return f"{self.package.package_number}: {self.from_status} → {self.to_status}"


class EngineeringPackageDocument(models.Model):
    """
    Documents attached to an engineering package.
    This includes drawings, datasheets, wiring diagrams, etc.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    package = models.ForeignKey(
        EngineeringPackage,
        on_delete=models.CASCADE,
        related_name='documents'
    )
    
    # Link to existing EngineeringDocument
    document = models.ForeignKey(
        'EngineeringDocument',
        on_delete=models.CASCADE,
        related_name='package_links'
    )
    
    # Package-specific info
    doc_type = models.CharField(
        max_length=30,
        choices=DocumentType.choices,
        default=DocumentType.DRAWING
    )
    
    # Ordering
    sort_order = models.IntegerField(default=0)
    
    class Meta:
        ordering = ['sort_order']


class EngineeringPackageChangeNotice(models.Model):
    """
    Engineering Change Notice (ECN) - formal notification of changes.
    
    This is the "Engineering Change Notice" that triggers the review process.
    """
    
    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
        ('CRITICAL', 'Critical'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ecn_number = models.CharField(max_length=30, blank=True)
    
    # Which package is changing
    package = models.ForeignKey(
        EngineeringPackage,
        on_delete=models.CASCADE,
        related_name='change_notices'
    )
    
    # New version
    new_package = models.ForeignKey(
        EngineeringPackage,
        on_delete=models.CASCADE,
        related_name='superseding_notices',
        null=True,
        blank=True
    )
    
    # Change details
    title = models.CharField(max_length=255)
    description = models.TextField()
    reason = models.TextField(help_text="Why is this change needed?")
    
    # Impact
    impact_analysis = models.TextField(blank=True)
    
    # Priority
    priority = models.CharField(max_length=20, choices=PRIORITY_CHOICES, default='MEDIUM')
    
    # Dates
    requested_at = models.DateTimeField(auto_now_add=True)
    reviewed_at = models.DateTimeField(null=True, blank=True)
    approved_at = models.DateTimeField(null=True, blank=True)
    effective_at = models.DateField(null=True, blank=True)
    
    # Status
    STATUS_CHOICES = [
        ('PENDING', 'Pending'),
        ('UNDER_REVIEW', 'Under Review'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
        ('DEFERRED', 'Deferred'),
    ]
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    
    # Approvals
    requested_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='requested_ecns'
    )
    reviewed_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='reviewed_ecns'
    )
    approved_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True,
        related_name='approved_ecns'
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def save(self, *args, **kwargs):
        if not self.ecn_number:
            year = timezone.now().year
            prefix = f"ECN-{year}-"
            last = (EngineeringPackageChangeNotice.objects
                    .filter(ecn_number__startswith=prefix)
                    .order_by('-ecn_number')
                    .first())
            if last and last.ecn_number:
                try:
                    seq = int(last.ecn_number.split('-')[-1]) + 1
                except (ValueError, IndexError):
                    seq = 1
            else:
                seq = 1
            self.ecn_number = f"{prefix}{seq:04d}"
        super().save(*args, **kwargs)