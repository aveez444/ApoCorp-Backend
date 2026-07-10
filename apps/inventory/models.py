# apps/inventory/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class ItemType(models.TextChoices):
    RAW_MATERIAL  = 'RAW_MATERIAL',  'Raw Material'
    SEMI_FINISHED = 'SEMI_FINISHED', 'Semi-Finished'
    FINISHED      = 'FINISHED',      'Finished Good'
    CONSUMABLE    = 'CONSUMABLE',    'Consumable'
    SPARE         = 'SPARE',         'Spare Part'
    ASSEMBLY      = 'ASSEMBLY',      'Assembly / Sub-Assembly'
    # ASSEMBLY: for engineering companies making complex sub-assemblies
    # (SWAS panels, instrument racks, etc.). Stays unused for simpler manufacturers.


class ValuationMethod(models.TextChoices):
    FIFO         = 'FIFO',         'First In First Out'
    WEIGHTED_AVG = 'WEIGHTED_AVG', 'Weighted Average'


class QCStatus(models.TextChoices):
    PENDING = 'PENDING', 'Pending QC'
    PASSED  = 'PASSED',  'QC Passed'
    FAILED  = 'FAILED',  'QC Failed'
    ON_HOLD = 'ON_HOLD', 'On Hold'


class StockTransactionType(models.TextChoices):
    GRN_RECEIPT           = 'GRN_RECEIPT',           'GRN Receipt'
    ISSUE_TO_PRODUCTION   = 'ISSUE_TO_PRODUCTION',   'Issue to Production'
    DISPATCH_TO_CUSTOMER  = 'DISPATCH_TO_CUSTOMER',  'Dispatch to Customer'
    JOB_WORK_OUT          = 'JOB_WORK_OUT',          'Job Work Out'
    JOB_WORK_IN           = 'JOB_WORK_IN',           'Job Work In'
    TRANSFER              = 'TRANSFER',               'Warehouse Transfer'
    ADJUSTMENT            = 'ADJUSTMENT',             'Stock Adjustment'
    OPENING               = 'OPENING',                'Opening Stock'
    QC_REJECTION          = 'QC_REJECTION',           'QC Rejection'
    RETURN_FROM_VENDOR    = 'RETURN_FROM_VENDOR',     'Return to Vendor'


class IssueSlipStatus(models.TextChoices):
    DRAFT     = 'DRAFT',     'Draft'
    ISSUED    = 'ISSUED',    'Issued'
    CANCELLED = 'CANCELLED', 'Cancelled'


class BarcodeLabelType(models.TextChoices):
    GRN      = 'GRN',      'GRN Receipt Label'
    ISSUE    = 'ISSUE',    'Issue Slip Label'
    TRANSFER = 'TRANSFER', 'Transfer Label'

# Add this after BarcodeLabelType in the choices section

class ReservationStatus(models.TextChoices):
    PENDING          = 'PENDING',          'Pending Approval'
    APPROVED         = 'APPROVED',         'Approved'
    REJECTED         = 'REJECTED',         'Rejected'
    PARTIALLY_ISSUED = 'PARTIALLY_ISSUED', 'Partially Issued'
    FULLY_ISSUED     = 'FULLY_ISSUED',     'Fully Issued'
    CANCELLED        = 'CANCELLED',        'Cancelled'
    
# ─────────────────────────────────────────────────────────────────────────────
# Item Master
# ─────────────────────────────────────────────────────────────────────────────

class ItemMaster(TenantModelMixin):
    """
    Central item / material master for procurement and inventory.

    Relation to apps/products/Product:
        `product` is an optional FK. Items used in sales quotations/OA have
        a matching Product record; items that are purely internal (consumables,
        maintenance spares) may not. This avoids forcing every stock item to
        also exist in the product catalogue.

    Engineering-specific fields (blank for simple manufacturers):
        drawing_number  — engineering drawing reference (e.g. DRG-SWAS-001)
        revision_number — drawing revision (Rev A, Rev B …)
        is_serial_tracked — True for serialised instruments/valves where each
                            unit needs a unique batch_number acting as serial no.

    Valuation:
        FIFO (default) — each StockBatch carries its own unit_cost at receipt.
        WEIGHTED_AVG   — unit_cost on StockBatch is updated on every new receipt.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    item_code = models.CharField(max_length=20, blank=True,
                                 help_text="Auto-generated: ITM00001")

    # Optional link to the sales Product master
    product = models.ForeignKey(
        'products.Product',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='inventory_items',
        help_text="Link to sales Product master if this item is also sold."
    )

    name        = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    item_type    = models.CharField(max_length=20, choices=ItemType.choices, default=ItemType.RAW_MATERIAL)
    category     = models.CharField(max_length=100, blank=True,
                                    help_text="Maps to VendorCategory / AVL category for vendor-item matching.")
    sub_category = models.CharField(max_length=100, blank=True)

    # Unit of measure
    uom               = models.CharField(max_length=30, help_text="Primary UOM e.g. NOS, KG, MTR")
    secondary_uom     = models.CharField(max_length=30, blank=True,
                                         help_text="Optional secondary UOM e.g. BOX when primary is NOS")
    conversion_factor = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True,
        help_text="qty_secondary = qty_primary × conversion_factor"
    )

    # Tax / compliance
    hsn_code       = models.CharField(max_length=20, blank=True)
    tax_group_code = models.CharField(max_length=50, blank=True)
    tax_percent    = models.DecimalField(max_digits=5, decimal_places=2, default=18)

    # Stock control levels
    min_stock_level = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    max_stock_level = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    reorder_level   = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    reorder_qty     = models.DecimalField(max_digits=15, decimal_places=3, default=0)

    # Costing
    standard_cost     = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    valuation_method  = models.CharField(max_length=15, choices=ValuationMethod.choices,
                                         default=ValuationMethod.FIFO)

    # Tracking flags
    is_batch_tracked  = models.BooleanField(default=True,
                                             help_text="Track stock by batch/lot number")
    is_serial_tracked = models.BooleanField(default=False,
                                             help_text="Each unit has a unique serial number (batch_number = serial no)")

    # Engineering-specific (blank for simple manufacturers — no conditional logic)
    drawing_number  = models.CharField(max_length=100, blank=True,
                                       help_text="Engineering drawing reference number")
    revision_number = models.CharField(max_length=20, blank=True,
                                       help_text="Drawing / BOM revision e.g. Rev A")

    is_active  = models.BooleanField(default=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='created_items')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['item_code']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'item_code'], name='unique_item_code_per_tenant'),
        ]
        indexes = [
            models.Index(fields=['tenant', 'item_type']),
            models.Index(fields=['tenant', 'category']),
            models.Index(fields=['tenant', 'is_active']),
        ]

    def save(self, *args, **kwargs):
        if not self.item_code:
            last = (ItemMaster.objects
                    .filter(tenant=self.tenant, item_code__startswith='ITM')
                    .order_by('-created_at')
                    .first())
            if last and last.item_code:
                try:
                    n = int(last.item_code[3:]) + 1
                except (ValueError, IndexError):
                    n = 1
            else:
                n = 1
            self.item_code = f'ITM{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.item_code} — {self.name}'


# ─────────────────────────────────────────────────────────────────────────────
# Warehouse + Storage Location
# ─────────────────────────────────────────────────────────────────────────────

class Warehouse(TenantModelMixin):
    """
    Physical warehouse. A tenant may have multiple warehouses
    (factory store, finished goods store, site store, etc.).
    """
    id      = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    code    = models.CharField(max_length=20)
    name    = models.CharField(max_length=150)
    address = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['code']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'code'], name='unique_warehouse_code_per_tenant'),
        ]

    def __str__(self):
        return f'{self.code} — {self.name}'


class StorageLocation(models.Model):
    """
    Bin/shelf within a warehouse.
    bin_code is auto-composed from warehouse.code + zone + rack + shelf.
    """
    warehouse = models.ForeignKey(Warehouse, on_delete=models.CASCADE,
                                  related_name='locations')
    zone      = models.CharField(max_length=10, blank=True, help_text="e.g. A, B, C")
    rack      = models.CharField(max_length=10, blank=True, help_text="e.g. 01, 02")
    shelf     = models.CharField(max_length=10, blank=True, help_text="e.g. 01, 02, 03")
    bin_code  = models.CharField(max_length=50, blank=True,
                                 help_text="Auto-composed: WH1-A-02-03")
    capacity  = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['bin_code']
        constraints = [
            models.UniqueConstraint(
                fields=['warehouse', 'zone', 'rack', 'shelf'],
                name='unique_bin_per_warehouse'
            )
        ]

    def save(self, *args, **kwargs):
        # Build composite bin_code from parts; skip empty segments
        parts = [self.warehouse.code]
        for seg in [self.zone, self.rack, self.shelf]:
            if seg:
                parts.append(seg)
        self.bin_code = '-'.join(parts)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.bin_code


# ─────────────────────────────────────────────────────────────────────────────
# Stock Batch
# ─────────────────────────────────────────────────────────────────────────────

class StockBatch(TenantModelMixin):
    """
    One row per item-batch-warehouse combination.

    quantity_available is a computed property (on_hand − reserved).
    Direct DB queries for availability must use:
        StockBatch.objects.filter(...).annotate(
            available=F('quantity_on_hand') - F('quantity_reserved')
        )

    qc_status is set to PASSED by the QC close action before any stock issue
    is allowed. The services.receive_stock() function creates this record.

    grn is a string FK ref to avoid circular import between inventory and purchase.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item             = models.ForeignKey(ItemMaster, on_delete=models.CASCADE,
                                         related_name='batches')
    batch_number     = models.CharField(max_length=100)
    warehouse        = models.ForeignKey(Warehouse, on_delete=models.PROTECT,
                                         related_name='batches')
    storage_location = models.ForeignKey(StorageLocation, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='batches')

    quantity_on_hand  = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    quantity_reserved = models.DecimalField(max_digits=15, decimal_places=3, default=0)

    unit_cost    = models.DecimalField(max_digits=15, decimal_places=2, default=0,
                                       help_text="Cost per unit at time of receipt (FIFO) or current weighted average.")
    received_date = models.DateField(null=True, blank=True)
    expiry_date   = models.DateField(null=True, blank=True)

    qc_status = models.CharField(max_length=10, choices=QCStatus.choices, default=QCStatus.PENDING)

    # String FK ref to avoid circular import with purchase app
    grn = models.ForeignKey(
        'purchase.GRN',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='stock_batches',
        help_text="GRN that created this batch. Null for opening stock and manual entries."
    )

    class Meta:
        ordering = ['received_date', 'batch_number']  # FIFO default ordering
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'item', 'batch_number', 'warehouse'],
                name='unique_batch_per_item_warehouse'
            )
        ]
        indexes = [
            models.Index(fields=['tenant', 'item', 'qc_status']),
            models.Index(fields=['tenant', 'warehouse']),
        ]

    @property
    def quantity_available(self):
        return self.quantity_on_hand - self.quantity_reserved

    def __str__(self):
        return f'{self.item.item_code} / Batch {self.batch_number} @ {self.warehouse.code}'


# ─────────────────────────────────────────────────────────────────────────────
# Stock Ledger  (append-only — never updated or deleted)
# ─────────────────────────────────────────────────────────────────────────────

class StockLedger(models.Model):
    """
    Immutable audit trail of every stock movement.

    Rules:
      - Created only through inventory.services.write_ledger().
      - No update or delete endpoint exposed in views.
      - balance_after is the item's total quantity across all batches in this
        warehouse AFTER this transaction (computed in write_ledger).

    reference_type + reference_id link back to the source document
    (GRN, IssueSlip, TransferOrder, etc.) without hard FK constraints —
    keeps this model decoupled from every other app.
    """
    id               = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item             = models.ForeignKey(ItemMaster, on_delete=models.PROTECT,
                                         related_name='ledger_entries')
    batch            = models.ForeignKey(StockBatch, on_delete=models.SET_NULL,
                                         null=True, blank=True, related_name='ledger_entries')
    warehouse        = models.ForeignKey(Warehouse, on_delete=models.PROTECT,
                                         related_name='ledger_entries')

    transaction_type = models.CharField(max_length=30, choices=StockTransactionType.choices)

    # Soft reference — no FK so the ledger stays intact even if source docs are purged
    reference_type   = models.CharField(max_length=30, blank=True,
                                         help_text="GRN / ISSUE_SLIP / TRANSFER / ADJUSTMENT etc.")
    reference_id     = models.UUIDField(null=True, blank=True)

    qty_in        = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    qty_out       = models.DecimalField(max_digits=15, decimal_places=3, null=True, blank=True)
    balance_after = models.DecimalField(max_digits=15, decimal_places=3)
    unit_cost     = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    remarks    = models.TextField(blank=True)
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='ledger_entries')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['item', 'warehouse', '-created_at']),
            models.Index(fields=['reference_type', 'reference_id']),
        ]

    def __str__(self):
        direction = f'+{self.qty_in}' if self.qty_in else f'-{self.qty_out}'
        return f'{self.item.item_code} {direction} ({self.transaction_type}) @ {self.created_at:%Y-%m-%d}'


# ─────────────────────────────────────────────────────────────────────────────
# Material Issue Slip
# ─────────────────────────────────────────────────────────────────────────────

class MaterialIssueSlip(TenantModelMixin):
    """
    Outbound stock document. Issues materials to production / job work.

    indent is optional — issues can be ad hoc (maintenance, tooling, etc.)
    job_order_ref is a placeholder CharField for the future production module.
    
    Flow:
        DRAFT   → save slip without affecting stock (for supervisor preview)
        ISSUED  → reserve/deduct stock + write StockLedger entries (atomic)
        CANCELLED → release any reservations
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    slip_number = models.CharField(max_length=20, blank=True,
                                   help_text="Auto-generated: ISS00001")

    indent = models.ForeignKey(
        'purchase.PurchaseIndent',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='issue_slips',
        help_text="Linked purchase indent if this issue was planned."
    )
    job_order_ref = models.CharField(max_length=100, blank=True,
                                     help_text="Production job order reference (future module placeholder).")
    issued_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                  null=True, blank=True, related_name='issued_slips')
    issued_at = models.DateTimeField(auto_now_add=True)
    status    = models.CharField(max_length=10, choices=IssueSlipStatus.choices,
                                 default=IssueSlipStatus.DRAFT)
    remarks   = models.TextField(blank=True)

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='issue_slips',
        help_text="Project this issue is for"
    )
    total_cost = models.DecimalField(
        max_digits=15, 
        decimal_places=2, 
        default=0,
        help_text="Total cost of issued materials (sum of issued_qty × unit_cost)"
    )

    class Meta:
        ordering = ['-issued_at']

    def save(self, *args, **kwargs):
        if not self.slip_number:
            last = (MaterialIssueSlip.objects
                    .filter(tenant=self.tenant, slip_number__startswith='ISS')
                    .order_by('-issued_at')
                    .first())
            if last and last.slip_number:
                try:
                    n = int(last.slip_number[3:]) + 1
                except (ValueError, IndexError):
                    n = 1
            else:
                n = 1
            self.slip_number = f'ISS{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.slip_number


class MaterialIssueSlipItem(models.Model):
    slip             = models.ForeignKey(MaterialIssueSlip, on_delete=models.CASCADE,
                                         related_name='items')
    item             = models.ForeignKey(ItemMaster, on_delete=models.PROTECT)
    batch            = models.ForeignKey(StockBatch, on_delete=models.SET_NULL,
                                         null=True, blank=True)
    storage_location = models.ForeignKey(StorageLocation, on_delete=models.SET_NULL,
                                         null=True, blank=True)
    requested_qty    = models.DecimalField(max_digits=15, decimal_places=3)
    issued_qty       = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    uom              = models.CharField(max_length=30)

    def __str__(self):
        return f'{self.slip.slip_number} — {self.item.item_code} × {self.issued_qty}'


# ─────────────────────────────────────────────────────────────────────────────
# Barcode Label
# ─────────────────────────────────────────────────────────────────────────────

class BarcodeLabel(models.Model):
    """
    Each scanned/printed label in the system.

    barcode_data format: {TENANT_CODE}-{ITEM_CODE}-{BATCH_NO}-{SEQ:04d}
    Example: ACME-ITM00023-BT0045-0001

    The scan endpoint GET /inventory/barcode/scan/?code=ACME-ITM00023-BT0045-0001
    resolves this string back to item + batch + current storage location.

    printed_at is null until the label is actually sent to the printer queue.
    """
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    item         = models.ForeignKey(ItemMaster, on_delete=models.CASCADE,
                                     related_name='barcode_labels')
    batch        = models.ForeignKey(StockBatch, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='labels')
    label_type   = models.CharField(max_length=10, choices=BarcodeLabelType.choices)
    reference_id = models.UUIDField(help_text="ID of the source document (GRN, IssueSlip, etc.)")
    barcode_data = models.CharField(max_length=200, unique=True,
                                    help_text="Scannable string: TENANT-ITEM-BATCH-SEQ")
    generated_at = models.DateTimeField(auto_now_add=True)
    printed_at   = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-generated_at']
        indexes = [
            models.Index(fields=['barcode_data']),
            models.Index(fields=['reference_id']),
        ]

    def __str__(self):
        return self.barcode_data


# ─────────────────────────────────────────────────────────────────────────────
# Stock Reservation
# ─────────────────────────────────────────────────────────────────────────────

class StockReservation(TenantModelMixin):
    """
    Soft lock on stock for a specific project.

    StockBatch.quantity_reserved is NOT touched here — it tracks only
    physically issued stock (Issue Slips). Reservations are a separate
    soft-lock layer that check_stock_availability() and run_mrp() both
    respect when calculating truly available qty.

    Lifecycle:
        PENDING          → store manager approves/rejects
        APPROVED         → stock is soft-locked for this project;
                           issue slips consume it → PARTIALLY_ISSUED → FULLY_ISSUED
        REJECTED         → no stock change, PM sees reason
        CANCELLED        → can be done from any non-terminal state;
                           if APPROVED, remaining qty released
    """
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.CASCADE,
        related_name='stock_reservations',
    )
    mrp_run = models.ForeignKey(
        'mrp.MRPRun',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='stock_reservations',
        help_text="Set when auto-created by MRP run. Null for manual reservations.",
    )
    item = models.ForeignKey(
        ItemMaster,
        on_delete=models.PROTECT,
        related_name='reservations',
    )
    warehouse = models.ForeignKey(
        Warehouse,
        on_delete=models.PROTECT,
        related_name='reservations',
    )

    requested_qty = models.DecimalField(max_digits=15, decimal_places=3)
    approved_qty  = models.DecimalField(
        max_digits=15, decimal_places=3,
        null=True, blank=True,
        help_text="Set by store manager. May be less than requested (partial approval).",
    )
    issued_qty = models.DecimalField(
        max_digits=15, decimal_places=3,
        default=0,
        help_text="Cumulative qty consumed by issue slips against this reservation.",
    )

    required_by_date = models.DateField(
        null=True, blank=True,
        help_text="Pulled from project.end_date on creation. Drives priority display.",
    )

    status = models.CharField(
        max_length=20,
        choices=ReservationStatus.choices,
        default=ReservationStatus.PENDING,
    )

    requested_by     = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='requested_reservations')
    approved_by      = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                          related_name='actioned_reservations')
    rejection_reason = models.TextField(blank=True)
    notes            = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['required_by_date', '-created_at']
        indexes = [
            models.Index(fields=['tenant', 'item', 'status']),
            models.Index(fields=['tenant', 'project', 'status']),
        ]

    @property
    def remaining_qty(self):
        """Approved qty not yet consumed by issue slips."""
        from decimal import Decimal
        if self.approved_qty is None:
            return Decimal('0')
        return max(self.approved_qty - self.issued_qty, Decimal('0'))

    def __str__(self):
        return f'RSV-{self.project.project_number}-{self.item.item_code} ({self.status})'