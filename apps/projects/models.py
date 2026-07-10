# apps/projects/models.py
import uuid
from decimal import Decimal

from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User

from core.mixins import TenantModelMixin
from apps.customers.models import Customer


class Project(TenantModelMixin):

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('PLANNING', 'Planning'),
        ('ACTIVE', 'Active'),
        ('ON_HOLD', 'On Hold'),
        ('COMPLETED', 'Completed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project_number = models.CharField(max_length=20, unique=True, blank=True)

    name = models.CharField(max_length=255)
    customer = models.ForeignKey(Customer, on_delete=models.PROTECT, related_name='projects')

    sales_order = models.OneToOneField(
        'orders.Order', on_delete=models.SET_NULL, null=True, blank=True, related_name='project'
    )

    contract_value = models.DecimalField(max_digits=15, decimal_places=2)
    currency = models.CharField(max_length=10, default='INR')

    start_date = models.DateField()
    end_date = models.DateField(null=True, blank=True)
    actual_end_date = models.DateField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    project_manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='managed_projects'
    )
    description = models.TextField(blank=True)

    # Denormalized cost roll-ups — kept in sync via signals (see signals.py).
    procurement_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    inventory_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    labour_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    vendor_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    other_cost = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # # BOM FK — restored. Uses string ref to avoid import-order issues.
    # bom = models.ForeignKey(
    #     'engineering.EngineeringBOM',
    #     on_delete=models.SET_NULL,
    #     null=True, blank=True,
    #     related_name='projects',
    # ) we removed this for using engineering package 

    active_package = models.ForeignKey(
        'engineering.EngineeringPackage',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='active_projects',
        help_text="The currently accepted engineering package for this project"
    )
    
    # i was asked to... set_active_package() bypasses the service layer entirely (no PackageHistoryEntry, no notifications, no "only RELEASED can be accepted" check) and duplicates what accept_package() already does correctly. Delete it:
    
    # def set_active_package(self, package):
    #     """
    #     Set the active package for this project.
    #     Ensures only one active package at a time.
    #     """
    #     from django.db import transaction
        
    #     with transaction.atomic():
    #         # If this package is already active, do nothing
    #         if self.active_package_id == package.id:
    #             return
            
    #         # If there's an existing active package, mark it as OBSOLETE
    #         if self.active_package:
    #             old_package = self.active_package
    #             old_package.status = 'OBSOLETE'
    #             old_package.save(update_fields=['status'])
            
    #         # Set the new active package
    #         self.active_package = package
    #         self.save(update_fields=['active_package'])
            
    #         # Ensure package is ACCEPTED
    #         if package.status != 'ACCEPTED':
    #             package.status = 'ACCEPTED'
    #             package.save(update_fields=['status'])
    
    @property
    def package_history(self):
        """Get all packages for this project in chronological order"""
        return self.engineering_packages.all().order_by('-created_at')
    
    @property
    def accepted_packages(self):
        """Get all accepted packages"""
        return self.engineering_packages.filter(status='ACCEPTED').order_by('-accepted_at')
    
    @property
    def pending_packages(self):
        """Get all pending/released packages waiting for acceptance"""
        return self.engineering_packages.filter(status='RELEASED').order_by('-released_at')

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.project_number:
            year = timezone.now().year
            prefix = f"PRJ-{year}-"
            last = (
                Project.objects.filter(project_number__startswith=prefix)
                .order_by('-project_number')
                .first()
            )
            if last and last.project_number:
                try:
                    seq = int(last.project_number.replace(prefix, '')) + 1
                except ValueError:
                    seq = 1
            else:
                seq = 1
            self.project_number = f"{prefix}{seq:04d}"

        if self.status == 'COMPLETED' and not self.actual_end_date:
            self.actual_end_date = timezone.now().date()

        super().save(*args, **kwargs)

    @property
    def total_cost(self):
        return (
            self.procurement_cost + self.inventory_cost +
            self.labour_cost + self.vendor_cost + self.other_cost
        )

    @property
    def gross_profit(self):
        return self.contract_value - self.total_cost

    @property
    def margin_pct(self):
        if self.contract_value:
            return (self.gross_profit / self.contract_value) * 100
        return Decimal('0')

    def __str__(self):
        return f"{self.project_number} — {self.name}"


class ProjectCostEntry(models.Model):
    """Full audit ledger — every cost booking against a project lands here."""

    COST_TYPE_CHOICES = [
        ('PROCUREMENT', 'Procurement'),
        ('INVENTORY', 'Inventory'),
        ('LABOUR', 'Labour'),
        ('VENDOR', 'Vendor'),
        ('OTHER', 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='cost_entries')

    cost_type = models.CharField(max_length=20, choices=COST_TYPE_CHOICES)
    amount = models.DecimalField(max_digits=15, decimal_places=2)

    reference_type = models.CharField(max_length=50, blank=True)
    reference_id = models.UUIDField(null=True, blank=True)
    description = models.TextField(blank=True)

    recorded_at = models.DateTimeField(auto_now_add=True)
    recorded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)

    class Meta:
        ordering = ['-recorded_at']


class ProjectMilestone(models.Model):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='milestones')

    title = models.CharField(max_length=255)
    due_date = models.DateField()
    completed_date = models.DateField(null=True, blank=True)
    is_completed = models.BooleanField(default=False)
    notes = models.TextField(blank=True)

    class Meta:
        ordering = ['due_date']


class ProjectDocument(models.Model):

    DOC_TYPE_CHOICES = [
        ('CONTRACT', 'Contract'),
        ('DRAWING', 'Drawing'),
        ('SPEC', 'Specification'),
        ('PO', 'Purchase Order'),
        ('OTHER', 'Other'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name='documents')

    doc_type = models.CharField(max_length=30, choices=DOC_TYPE_CHOICES, default='OTHER')
    title = models.CharField(max_length=255)
    file = models.FileField(upload_to='projects/')
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

# apps/projects/models.py - Add this

class ProjectMaterialRequirement(models.Model):
    """
    Individual material requirement for a project.
    Created when a BOM is transferred to a project.
    """
    
    STATUS_CHOICES = [
        ('REQUIRED', 'Required'),
        ('RESERVED', 'Reserved'),
        ('PURCHASED', 'Purchased'),
        ('RECEIVED', 'Received'),
        ('ISSUED', 'Issued'),
        ('CANCELLED', 'Cancelled'),
    ]
    
    PROCUREMENT_CHOICES = [
        ('STOCK', 'Stock Item'),
        ('PURCHASE', 'Purchase External'),
        ('MANUFACTURE', 'Manufacture In-House'),
    ]
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    project = models.ForeignKey(
        Project, 
        on_delete=models.CASCADE, 
        related_name='material_requirements'
    )
    
    # Link to engineering package that created this requirement
    bom_requirement = models.ForeignKey(
        'engineering.EngineeringPackage',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='material_requirements',
        help_text="The engineering package that created this requirement"
    )
    
    # The component
    item = models.ForeignKey(
        'engineering.EngineeringItemMaster',
        on_delete=models.PROTECT,
        related_name='project_material_requirements'
    )
    
    # Quantities
    required_qty = models.DecimalField(max_digits=15, decimal_places=3)
    reserved_qty = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    purchased_qty = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    received_qty = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    issued_qty = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    
    # Dates
    required_by = models.DateField(null=True, blank=True)
    
    # Status
    status = models.CharField(
        max_length=20, 
        choices=STATUS_CHOICES, 
        default='REQUIRED'
    )
    
    # Procurement info
    procurement_type = models.CharField(
        max_length=20,
        choices=PROCUREMENT_CHOICES,
        default='PURCHASE'
    )
    
    indent_line = models.ForeignKey(
        'purchase.PurchaseIndentItem',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='project_requirements'
    )
    
    # Audit
    created_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, 
        blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['required_by', 'item__item_code']
        unique_together = [['project', 'item']]  # One requirement per item per project
    
    def __str__(self):
        return f"{self.project.project_number} → {self.item.item_code} × {self.required_qty}"