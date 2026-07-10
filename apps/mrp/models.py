# apps/mrp/models.py
import uuid
from decimal import Decimal
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class MRPRunStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    RUNNING = 'RUNNING', 'Running'
    COMPLETED = 'COMPLETED', 'Completed'
    FAILED = 'FAILED', 'Failed'
    CANCELLED = 'CANCELLED', 'Cancelled'


class MRPLineStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending'
    CONVERTED = 'CONVERTED', 'Converted to Indent'
    CANCELLED = 'CANCELLED', 'Cancelled'


# ─────────────────────────────────────────────────────────────────────────────
# MRP Run
# ─────────────────────────────────────────────────────────────────────────────

class MRPRun(TenantModelMixin):
    """
    One MRP run per project trigger. Captures the state of material
    requirements at a point in time.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    
    # Link to project and BOM
    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='mrp_runs'
    )
    bom = models.ForeignKey(
        'engineering.EngineeringBOM',
        on_delete=models.PROTECT,
        related_name='mrp_runs',
        help_text="BOM version used for this MRP run"
    )
    
    # Run identifier
    run_number = models.CharField(
        max_length=50, 
        blank=True,
        help_text="Auto-generated: MRP-PRJ-2026-0001-001"
    )
    
    # Status
    status = models.CharField(
        max_length=20, 
        choices=MRPRunStatus.choices,
        default=MRPRunStatus.PENDING
    )
    
    # Metadata
    run_by = models.ForeignKey(
        User, 
        on_delete=models.SET_NULL, 
        null=True, blank=True,
        related_name='mrp_runs'
    )
    run_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    # Notes/errors
    notes = models.TextField(blank=True)
    
    # Summary statistics (denormalized for quick display)
    total_items = models.IntegerField(default=0)
    items_with_shortage = models.IntegerField(default=0)
    total_shortage_value = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Estimated cost of all shortages"
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-run_at']
        indexes = [
            models.Index(fields=['project', 'status']),
            models.Index(fields=['run_at']),
        ]
    
    def save(self, *args, **kwargs):
        if not self.run_number:
            # Format: MRP-PRJ-2026-0001-001
            if self.project and self.project.project_number:
                # Get the last run number for this project
                last = (MRPRun.objects
                        .filter(project=self.project)
                        .order_by('-run_at')
                        .first())
                if last and last.run_number:
                    try:
                        parts = last.run_number.split('-')
                        seq = int(parts[-1]) + 1
                    except (ValueError, IndexError):
                        seq = 1
                else:
                    seq = 1
                
                # Use project number as base (PRJ-2026-0001)
                self.run_number = f"MRP-{self.project.project_number}-{seq:03d}"
            else:
                # Fallback
                from datetime import datetime
                timestamp = datetime.now().strftime('%Y%m%d%H%M%S')
                self.run_number = f"MRP-{timestamp}"
        
        super().save(*args, **kwargs)
    
    def __str__(self):
        return f"{self.run_number} — {self.project.name}"


class MRPLine(models.Model):
    """
    One row per item in the BOM explosion. Tracks requirement vs availability.
    """
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    mrp_run = models.ForeignKey(
        MRPRun, 
        on_delete=models.CASCADE,
        related_name='lines'
    )
    
    # Link to engineering and inventory items
    engineering_item = models.ForeignKey(
        'engineering.EngineeringItemMaster',
        on_delete=models.PROTECT,
        related_name='mrp_lines'
    )
    inventory_item = models.ForeignKey(
        'inventory.ItemMaster',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mrp_lines',
        help_text="Linked inventory item (if exists)"
    )
    
    # Item classification (denormalized from engineering item)
    item_class = models.CharField(
        max_length=1,
        choices=[('A', 'A'), ('B', 'B'), ('C', 'C')],
        default='B'
    )
    
    # Requirements
    required_qty = models.DecimalField(max_digits=15, decimal_places=3)
    uom = models.CharField(max_length=20)
    
    # Availability
    available_qty = models.DecimalField(
        max_digits=15, 
        decimal_places=3, 
        default=0,
        help_text="Stock available at time of MRP run (QC PASSED)"
    )
    on_order_qty = models.DecimalField(
        max_digits=15, 
        decimal_places=3, 
        default=0,
        help_text="Quantity already on open POs"
    )
    
    # Calculated
    shortage_qty = models.DecimalField(
        max_digits=15, 
        decimal_places=3, 
        default=0,
        help_text="required - available - on_order (if positive)"
    )
    has_shortage = models.BooleanField(default=False)
    
    # Status
    status = models.CharField(
        max_length=20, 
        choices=MRPLineStatus.choices,
        default=MRPLineStatus.PENDING
    )
    
    # Link to purchase indent (when converted)
    indent_raised = models.BooleanField(default=False)
    indent = models.ForeignKey(
        'purchase.PurchaseIndent',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='mrp_lines'
    )
    
    # Recommendation text
    recommendation = models.TextField(
        blank=True,
        help_text="System recommendation for procurement"
    )
    
    # Path in BOM (for traceability)
    bom_path = models.TextField(
        blank=True,
        help_text="Hierarchical path: SWAS Panel > Analyzer Rack > Analyser"
    )
    depth = models.IntegerField(default=0)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-has_shortage', 'engineering_item__item_class', 'engineering_item__item_code']
        indexes = [
            models.Index(fields=['mrp_run', 'has_shortage']),
            models.Index(fields=['mrp_run', 'indent_raised']),
            models.Index(fields=['engineering_item', 'mrp_run']),
        ]
    
    def __str__(self):
        return f"{self.mrp_run.run_number} — {self.engineering_item.item_code} × {self.required_qty}"