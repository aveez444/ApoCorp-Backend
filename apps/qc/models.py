# apps/qc/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class QCType(models.TextChoices):
    INWARD            = 'INWARD',            'Inward (GRN)'
    IN_PROCESS        = 'IN_PROCESS',        'In-Process'
    FINAL             = 'FINAL',             'Final / Pre-Dispatch'
    RETURN            = 'RETURN',            'Customer Return'


class SamplingType(models.TextChoices):
    HUNDRED_PCT = 'HUNDRED_PCT', '100% Inspection'
    AQL         = 'AQL',         'AQL Sampling'
    SKIP_LOT    = 'SKIP_LOT',    'Skip-Lot'


class ParameterType(models.TextChoices):
    VISUAL      = 'VISUAL',      'Visual'
    DIMENSIONAL = 'DIMENSIONAL', 'Dimensional'
    FUNCTIONAL  = 'FUNCTIONAL',  'Functional'
    DOCUMENT    = 'DOCUMENT',    'Document Check'
    CHEMICAL    = 'CHEMICAL',    'Chemical / Composition'  # raw material receipt


class InspectionStatus(models.TextChoices):
    PENDING     = 'PENDING',     'Pending'
    IN_PROGRESS = 'IN_PROGRESS', 'In Progress'
    COMPLETED   = 'COMPLETED',   'Completed'


class InspectionOutcome(models.TextChoices):
    PASS = 'PASS', 'Pass'
    FAIL = 'FAIL', 'Fail'
    HOLD = 'HOLD', 'Hold'


class ResultStatus(models.TextChoices):
    PASS = 'PASS', 'Pass'
    FAIL = 'FAIL', 'Fail'
    NA   = 'NA',   'Not Applicable'


class NCRDisposition(models.TextChoices):
    RETURN_TO_VENDOR  = 'RETURN_TO_VENDOR',  'Return to Vendor'
    SCRAP             = 'SCRAP',             'Scrap'
    REWORK            = 'REWORK',            'Rework'
    ACCEPT_DEVIATION  = 'ACCEPT_DEVIATION',  'Accept with Deviation'


class NCRStatus(models.TextChoices):
    OPEN         = 'OPEN',         'Open'
    UNDER_REVIEW = 'UNDER_REVIEW', 'Under Review'
    CLOSED       = 'CLOSED',       'Closed'


# ─────────────────────────────────────────────────────────────────────────────
# Inspection Plan  (defines what to check for an item or category)
# ─────────────────────────────────────────────────────────────────────────────

class InspectionPlan(TenantModelMixin):
    """
    Defines the inspection checklist for a specific item or item category.

    Resolution priority when creating an inspection order:
      1. Exact item match + qc_type  (most specific)
      2. item_category match + qc_type
      3. No plan found → inspector fills in manually

    Either item or item_category must be set — not both, not neither.
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    # One of these must be set
    item          = models.ForeignKey(
        'inventory.ItemMaster', on_delete=models.CASCADE,
        null=True, blank=True, related_name='inspection_plans',
        help_text="Item-level plan. Takes priority over category plan."
    )
    item_category = models.CharField(
        max_length=100, blank=True,
        help_text="Category-level plan. Used when no item-specific plan exists."
    )

    qc_type       = models.CharField(max_length=15, choices=QCType.choices, default=QCType.INWARD)
    sampling_type = models.CharField(max_length=15, choices=SamplingType.choices,
                                     default=SamplingType.HUNDRED_PCT)
    aql_level     = models.CharField(max_length=10, blank=True,
                                     help_text="AQL level e.g. II, S-1, S-2 (only for AQL sampling)")
    is_active     = models.BooleanField(default=True)

    class Meta:
        ordering = ['item_category', 'qc_type']

    def clean(self):
        from django.core.exceptions import ValidationError
        if not self.item and not self.item_category:
            raise ValidationError("Either item or item_category must be set.")

    def __str__(self):
        scope = str(self.item) if self.item else self.item_category
        return f'Plan: {scope} / {self.qc_type}'


class InspectionParameter(models.Model):
    """
    One row per checkpoint in an inspection plan.
    Numeric parameters use min_value/max_value; text parameters use acceptance_criteria.
    """
    plan               = models.ForeignKey(InspectionPlan, on_delete=models.CASCADE,
                                           related_name='parameters')
    parameter_name     = models.CharField(max_length=150)
    parameter_type     = models.CharField(max_length=15, choices=ParameterType.choices)
    acceptance_criteria = models.TextField(blank=True,
                                           help_text="Text description e.g. 'No visible scratches'")
    measurement_unit   = models.CharField(max_length=30, blank=True)
    min_value          = models.DecimalField(max_digits=15, decimal_places=4,
                                             null=True, blank=True)
    max_value          = models.DecimalField(max_digits=15, decimal_places=4,
                                             null=True, blank=True)
    is_mandatory       = models.BooleanField(default=True)
    sequence           = models.IntegerField(default=0, help_text="Display/execution order")

    class Meta:
        ordering = ['sequence', 'parameter_name']

    def __str__(self):
        return f'{self.plan} — {self.parameter_name}'


# ─────────────────────────────────────────────────────────────────────────────
# QC Inspection Order
# ─────────────────────────────────────────────────────────────────────────────

class QCInspectionOrder(TenantModelMixin):
    """
    One inspection order per item per GRN (for inward QC).
    Can also be created for IN_PROCESS, FINAL, and RETURN inspections.

    reference_type + reference_id are soft links (no FK) so QC stays
    decoupled from the source modules.

    grn_item is a direct FK for inward QC only — null for other types.
    """
    id         = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    qc_number  = models.CharField(max_length=20, blank=True)

    qc_type        = models.CharField(max_length=15, choices=QCType.choices)
    reference_type = models.CharField(max_length=30,
                                      help_text="GRN / PRODUCTION_STAGE / DISPATCH / RETURN")
    reference_id   = models.UUIDField(null=True, blank=True)

    item     = models.ForeignKey('inventory.ItemMaster', on_delete=models.PROTECT,
                                 related_name='inspection_orders')
    batch    = models.ForeignKey('inventory.StockBatch', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='inspection_orders')
    grn_item = models.ForeignKey('purchase.GRNItem', on_delete=models.SET_NULL,
                                 null=True, blank=True, related_name='inspection_orders',
                                 help_text="Set for INWARD QC only.")

    plan      = models.ForeignKey(InspectionPlan, on_delete=models.SET_NULL,
                                  null=True, blank=True,
                                  help_text="Auto-resolved; null if no matching plan found.")
    inspector = models.ForeignKey(User, on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='assigned_inspections')

    status   = models.CharField(max_length=15, choices=InspectionStatus.choices,
                                default=InspectionStatus.PENDING)
    outcome  = models.CharField(max_length=5, choices=InspectionOutcome.choices,
                                null=True, blank=True)

    sample_qty    = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    inspected_qty = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    passed_qty    = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    failed_qty    = models.DecimalField(max_digits=15, decimal_places=3, default=0)

    started_at   = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    remarks      = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.qc_number:
            last = (QCInspectionOrder.objects
                    .filter(tenant=self.tenant, qc_number__startswith='QC')
                    .order_by('-created_at').first())
            n = 1
            if last and last.qc_number:
                try:
                    n = int(last.qc_number[2:]) + 1
                except (ValueError, IndexError):
                    pass
            self.qc_number = f'QC{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.qc_number


class QCResult(models.Model):
    """One row per parameter per inspection order."""
    inspection_order = models.ForeignKey(QCInspectionOrder, on_delete=models.CASCADE,
                                          related_name='results')
    parameter        = models.ForeignKey(InspectionParameter, on_delete=models.PROTECT)
    measured_value   = models.CharField(max_length=200,
                                        help_text="Flexible: '12.3' or 'PASS' or 'No scratches'")
    status           = models.CharField(max_length=5, choices=ResultStatus.choices)
    remarks          = models.TextField(blank=True)

    class Meta:
        ordering = ['parameter__sequence']

    def __str__(self):
        return f'{self.inspection_order.qc_number} — {self.parameter.parameter_name}: {self.status}'


class QCAttachment(models.Model):
    inspection_order = models.ForeignKey(QCInspectionOrder, on_delete=models.CASCADE,
                                          related_name='attachments')
    file             = models.FileField(upload_to='qc_attachments/%Y/%m/')
    description      = models.CharField(max_length=255, blank=True)
    uploaded_at      = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f'{self.inspection_order.qc_number} — {self.description or self.file.name}'


# ─────────────────────────────────────────────────────────────────────────────
# NCR (Non-Conformance Report)
# ─────────────────────────────────────────────────────────────────────────────

class NCR(TenantModelMixin):
    """
    Raised automatically when a QC inspection outcome is FAIL.
    Can also be raised manually during IN_PROCESS or FINAL inspection.
    Disposition is set by a manager to resolve the NCR.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ncr_number       = models.CharField(max_length=20, blank=True)
    inspection_order = models.ForeignKey(QCInspectionOrder, on_delete=models.CASCADE,
                                          related_name='ncrs')
    raised_by        = models.ForeignKey(User, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='raised_ncrs')
    description      = models.TextField()
    root_cause       = models.TextField(blank=True)
    corrective_action = models.TextField(blank=True)
    disposition      = models.CharField(max_length=20, choices=NCRDisposition.choices,
                                        blank=True)
    disposition_by   = models.ForeignKey(User, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='disposed_ncrs')
    disposition_at   = models.DateTimeField(null=True, blank=True)
    status           = models.CharField(max_length=15, choices=NCRStatus.choices,
                                        default=NCRStatus.OPEN)
    created_at       = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.ncr_number:
            last = (NCR.objects
                    .filter(tenant=self.tenant, ncr_number__startswith='NCR')
                    .order_by('-created_at').first())
            n = 1
            if last and last.ncr_number:
                try:
                    n = int(last.ncr_number[3:]) + 1
                except (ValueError, IndexError):
                    pass
            self.ncr_number = f'NCR{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.ncr_number