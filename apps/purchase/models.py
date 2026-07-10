# apps/purchase/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class IndentType(models.TextChoices):
    PRODUCTION  = 'PRODUCTION',  'Production'
    MAINTENANCE = 'MAINTENANCE', 'Maintenance'
    GENERAL     = 'GENERAL',     'General'
    PROJECT     = 'PROJECT',     'Project / Contract'


class IndentStatus(models.TextChoices):
    DRAFT     = 'DRAFT',     'Draft'
    SUBMITTED = 'SUBMITTED', 'Submitted'
    APPROVED  = 'APPROVED',  'Approved'
    FULFILLED = 'FULFILLED', 'Fulfilled'
    CANCELLED = 'CANCELLED', 'Cancelled'


class IndentItemStatus(models.TextChoices):
    PENDING   = 'PENDING',   'Pending'
    PARTIAL   = 'PARTIAL',   'Partially Fulfilled'
    FULFILLED = 'FULFILLED', 'Fulfilled'


class RFQStatus(models.TextChoices):
    DRAFT  = 'DRAFT',  'Draft'
    SENT   = 'SENT',   'Sent to Vendors'
    QUOTED = 'QUOTED', 'Quotations Received'
    CLOSED = 'CLOSED', 'Closed'


class RFQVendorStatus(models.TextChoices):
    SENT      = 'SENT',      'Sent'
    RESPONDED = 'RESPONDED', 'Responded'
    NO_RESPONSE = 'NO_RESPONSE', 'No Response'


class QuotationStatus(models.TextChoices):
    RECEIVED    = 'RECEIVED',    'Received'
    SHORTLISTED = 'SHORTLISTED', 'Shortlisted'
    SELECTED    = 'SELECTED',    'Selected'
    REJECTED    = 'REJECTED',    'Rejected'


class POStatus(models.TextChoices):
    DRAFT              = 'DRAFT',              'Draft'
    PENDING_APPROVAL   = 'PENDING_APPROVAL',   'Pending Approval'
    APPROVED           = 'APPROVED',           'Approved'
    SENT               = 'SENT',               'Sent to Vendor'
    PARTIALLY_RECEIVED = 'PARTIALLY_RECEIVED', 'Partially Received'
    RECEIVED           = 'RECEIVED',           'Fully Received'
    CANCELLED          = 'CANCELLED',          'Cancelled'


class GRNStatus(models.TextChoices):
    DRAFT        = 'DRAFT',        'Draft'
    QC_PENDING   = 'QC_PENDING',   'Pending QC'
    QC_DONE      = 'QC_DONE',      'QC Completed'
    STOCK_UPDATED = 'STOCK_UPDATED', 'Stock Updated'
    CANCELLED    = 'CANCELLED',    'Cancelled'


class InvoiceMatchStatus(models.TextChoices):
    PENDING  = 'PENDING',  'Pending Match'
    MATCHED  = 'MATCHED',  'Matched'
    MISMATCH = 'MISMATCH', 'Mismatch'
    APPROVED = 'APPROVED', 'Approved'


class PaymentStatus(models.TextChoices):
    UNPAID  = 'UNPAID',  'Unpaid'
    PARTIAL = 'PARTIAL', 'Partially Paid'
    PAID    = 'PAID',    'Paid'


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Indent (MRN)
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseIndent(TenantModelMixin):
    """
    Material Requisition Note. Raised by any department to request items.
    Can be linked to a production job order or project (engineering use case).
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    indent_number = models.CharField(max_length=20, blank=True,
                                     help_text="Auto-generated: MRN00001")
    indent_type   = models.CharField(max_length=15, choices=IndentType.choices,
                                     default=IndentType.PRODUCTION)
    raised_by     = models.ForeignKey(User, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='raised_indents')
    department    = models.CharField(max_length=100, blank=True)
    required_by_date = models.DateField(null=True, blank=True)

    # Placeholder FKs for future production / project modules
    job_order_ref = models.CharField(max_length=100, blank=True,
                                     help_text="Production job order ref (future module)")
    project_ref   = models.CharField(max_length=100, blank=True,
                                     help_text="Project / contract ref for engineering orders")

    status      = models.CharField(max_length=15, choices=IndentStatus.choices,
                                   default=IndentStatus.DRAFT)
    approved_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='approved_indents')
    approved_at = models.DateTimeField(null=True, blank=True)
    remarks     = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='indents',
        help_text="Project this indent is for"
    )

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.indent_number:
            last = (PurchaseIndent.objects
                    .filter(tenant=self.tenant, indent_number__startswith='MRN')
                    .order_by('-created_at').first())
            n = 1
            if last and last.indent_number:
                try:
                    n = int(last.indent_number[3:]) + 1
                except (ValueError, IndexError):
                    pass
            self.indent_number = f'MRN{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.indent_number


class PurchaseIndentItem(models.Model):
    indent       = models.ForeignKey(PurchaseIndent, on_delete=models.CASCADE,
                                     related_name='items')
    item         = models.ForeignKey('inventory.ItemMaster', on_delete=models.PROTECT)
    required_qty = models.DecimalField(max_digits=15, decimal_places=3)
    uom          = models.CharField(max_length=30)

    # Snapshot of available stock when indent was raised — for reference only
    available_qty_at_time = models.DecimalField(max_digits=15, decimal_places=3, default=0)

    fulfilled_qty = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    status        = models.CharField(max_length=10, choices=IndentItemStatus.choices,
                                     default=IndentItemStatus.PENDING)
    specifications = models.TextField(blank=True,
                                      help_text="Drawing revision, special requirements (engineering)")
    
    mrp_line = models.ForeignKey(
        'mrp.MRPLine',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='indent_items'
    )

    def __str__(self):
        return f'{self.indent.indent_number} — {self.item.item_code}'


# ─────────────────────────────────────────────────────────────────────────────
# RFQ
# ─────────────────────────────────────────────────────────────────────────────

class RFQ(TenantModelMixin):
    """
    Request for Quotation sent to one or more vendors.
    Can be created directly (without an indent) for ad-hoc purchases.
    """
    id          = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfq_number  = models.CharField(max_length=20, blank=True)
    indent      = models.ForeignKey(PurchaseIndent, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='rfqs')
    created_by  = models.ForeignKey(User, on_delete=models.SET_NULL,
                                    null=True, blank=True, related_name='created_rfqs')
    required_delivery_date = models.DateField(null=True, blank=True)
    delivery_address = models.JSONField(null=True, blank=True)
    status      = models.CharField(max_length=10, choices=RFQStatus.choices,
                                   default=RFQStatus.DRAFT)
    notes       = models.TextField(blank=True)
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.rfq_number:
            last = (RFQ.objects
                    .filter(tenant=self.tenant, rfq_number__startswith='RFQ')
                    .order_by('-created_at').first())
            n = 1
            if last and last.rfq_number:
                try:
                    n = int(last.rfq_number[3:]) + 1
                except (ValueError, IndexError):
                    pass
            self.rfq_number = f'RFQ{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.rfq_number


class RFQItem(models.Model):
    rfq            = models.ForeignKey(RFQ, on_delete=models.CASCADE, related_name='items')
    item           = models.ForeignKey('inventory.ItemMaster', on_delete=models.PROTECT)
    quantity       = models.DecimalField(max_digits=15, decimal_places=3)
    uom            = models.CharField(max_length=30)
    specifications = models.TextField(blank=True)
    drawing_ref    = models.CharField(max_length=100, blank=True,
                                      help_text="Engineering drawing reference")

    def __str__(self):
        return f'{self.rfq.rfq_number} — {self.item.item_code}'


class RFQVendor(models.Model):
    rfq               = models.ForeignKey(RFQ, on_delete=models.CASCADE,
                                          related_name='rfq_vendors')
    vendor            = models.ForeignKey('vendors.Vendor', on_delete=models.PROTECT)
    sent_at           = models.DateTimeField(null=True, blank=True)
    response_deadline = models.DateField(null=True, blank=True)
    status            = models.CharField(max_length=15, choices=RFQVendorStatus.choices,
                                         default=RFQVendorStatus.SENT)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['rfq', 'vendor'], name='unique_vendor_per_rfq')
        ]

    def __str__(self):
        return f'{self.rfq.rfq_number} → {self.vendor.vendor_code}'


# ─────────────────────────────────────────────────────────────────────────────
# Vendor Quotation
# ─────────────────────────────────────────────────────────────────────────────

class VendorQuotation(TenantModelMixin):
    """
    Quotation submitted by a vendor against an RFQ.
    selection_justification is required when a non-L1 (cheapest) vendor is selected.
    """
    id                     = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    rfq                    = models.ForeignKey(RFQ, on_delete=models.CASCADE,
                                               related_name='quotations')
    vendor                 = models.ForeignKey('vendors.Vendor', on_delete=models.PROTECT)
    quote_number           = models.CharField(max_length=100, blank=True)
    quote_date             = models.DateField(null=True, blank=True)
    valid_until            = models.DateField(null=True, blank=True)
    currency               = models.CharField(max_length=10, default='INR')
    delivery_days          = models.IntegerField(default=0)
    payment_terms          = models.CharField(max_length=150, blank=True)
    status                 = models.CharField(max_length=15, choices=QuotationStatus.choices,
                                              default=QuotationStatus.RECEIVED)
    is_selected            = models.BooleanField(default=False)
    selection_justification = models.TextField(
        blank=True,
        help_text="Mandatory if selecting a vendor who is not the L1 (lowest price) bidder."
    )
    total_value            = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    created_at             = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['rfq', 'vendor'], name='unique_quotation_per_rfq_vendor')
        ]

    def __str__(self):
        return f'{self.rfq.rfq_number} / {self.vendor.vendor_code}'


class VendorQuotationItem(models.Model):
    quotation        = models.ForeignKey(VendorQuotation, on_delete=models.CASCADE,
                                         related_name='items')
    rfq_item         = models.ForeignKey(RFQItem, on_delete=models.PROTECT)
    unit_price       = models.DecimalField(max_digits=15, decimal_places=2)
    qty              = models.DecimalField(max_digits=15, decimal_places=3)
    discount_pct     = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_pct          = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    tax_amount       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_price      = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    delivery_days    = models.IntegerField(default=0)
    brand            = models.CharField(max_length=100, blank=True)
    make             = models.CharField(max_length=100, blank=True)
    country_of_origin = models.CharField(max_length=100, blank=True,
                                          help_text="Import vs local distinction for engineering projects")

    def save(self, *args, **kwargs):
        from decimal import Decimal
        discounted = self.unit_price * self.qty * (1 - Decimal(str(self.discount_pct)) / 100)
        self.tax_amount  = round(discounted * Decimal(str(self.tax_pct)) / 100, 2)
        self.total_price = round(discounted + self.tax_amount, 2)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.quotation} — {self.rfq_item.item.item_code}'


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Order
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseOrder(TenantModelMixin):
    """
    Formal purchase order sent to vendor.
    Approval: all POs require GM approval (flat rule, no threshold tiers).
    """
    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    po_number     = models.CharField(max_length=20, blank=True)
    rfq           = models.ForeignKey(RFQ, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='purchase_orders')
    quotation     = models.ForeignKey(VendorQuotation, on_delete=models.SET_NULL,
                                      null=True, blank=True, related_name='purchase_orders')
    vendor        = models.ForeignKey('vendors.Vendor', on_delete=models.PROTECT,
                                      related_name='purchase_orders')
    po_date       = models.DateField(auto_now_add=True)
    delivery_date = models.DateField(null=True, blank=True)

    # Snapshots (captured at PO creation so changes to master data don't affect the PO)
    billing_address  = models.JSONField(null=True, blank=True)
    delivery_address = models.JSONField(null=True, blank=True)

    payment_terms     = models.CharField(max_length=150, blank=True)
    currency          = models.CharField(max_length=10, default='INR')
    exchange_rate     = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    sub_total         = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_amount        = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_value       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    advance_amount    = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    status            = models.CharField(max_length=20, choices=POStatus.choices,
                                         default=POStatus.DRAFT)
    approved_by       = models.ForeignKey(User, on_delete=models.SET_NULL,
                                          null=True, blank=True, related_name='approved_pos')
    approved_at       = models.DateTimeField(null=True, blank=True)
    cancelled_reason  = models.TextField(blank=True)

    notes                = models.TextField(blank=True)
    terms_and_conditions = models.TextField(blank=True,
                                             help_text="Project-specific T&C for engineering orders")

    created_by = models.ForeignKey(User, on_delete=models.SET_NULL,
                                   null=True, blank=True, related_name='created_pos')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    project = models.ForeignKey(
        'projects.Project',
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name='purchase_orders',
        help_text="Project this PO is for"
    )

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.po_number:
            last = (PurchaseOrder.objects
                    .filter(tenant=self.tenant, po_number__startswith='PO')
                    .order_by('-created_at').first())
            n = 1
            if last and last.po_number:
                try:
                    n = int(last.po_number[2:]) + 1
                except (ValueError, IndexError):
                    pass
            self.po_number = f'PO{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return self.po_number


class PurchaseOrderItem(models.Model):
    po               = models.ForeignKey(PurchaseOrder, on_delete=models.CASCADE,
                                         related_name='items')
    item             = models.ForeignKey('inventory.ItemMaster', on_delete=models.PROTECT)
    item_description = models.TextField(blank=True,
                                        help_text="Overrides item master description on the PO document")
    quantity         = models.DecimalField(max_digits=15, decimal_places=3)
    received_qty     = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    uom              = models.CharField(max_length=30)
    unit_price       = models.DecimalField(max_digits=15, decimal_places=2)
    discount_pct     = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_group_code   = models.CharField(max_length=50, blank=True)
    tax_pct          = models.DecimalField(max_digits=5, decimal_places=2, default=18)
    tax_amount       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_price      = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    hsn_code         = models.CharField(max_length=20, blank=True)
    make             = models.CharField(max_length=100, blank=True)
    drawing_ref      = models.CharField(max_length=100, blank=True,
                                        help_text="Engineering drawing reference on PO line")

    @property
    def pending_qty(self):
        return self.quantity - self.received_qty

    def save(self, *args, **kwargs):
        from decimal import Decimal
        discounted   = self.unit_price * self.quantity * (1 - Decimal(str(self.discount_pct)) / 100)
        self.tax_amount  = round(discounted * Decimal(str(self.tax_pct)) / 100, 2)
        self.total_price = round(discounted + self.tax_amount, 2)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.po.po_number} — {self.item.item_code}'


# ─────────────────────────────────────────────────────────────────────────────
# GRN
# ─────────────────────────────────────────────────────────────────────────────

class GRN(TenantModelMixin):
    """
    Goods Receipt Note. Records physical receipt of materials from vendor.
    On save to QC_PENDING, auto-creates QC Inspection Orders (if grn_auto_qc=True).
    """
    id           = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    grn_number   = models.CharField(max_length=20, blank=True)
    po           = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='grns')
    vendor       = models.ForeignKey('vendors.Vendor', on_delete=models.PROTECT,
                                     related_name='grns')
    received_by  = models.ForeignKey(User, on_delete=models.SET_NULL,
                                     null=True, blank=True, related_name='received_grns')
    received_date = models.DateField()
    vehicle_number = models.CharField(max_length=50, blank=True)
    dc_number    = models.CharField(max_length=100, blank=True,
                                    help_text="Vendor delivery challan number")
    dc_date      = models.DateField(null=True, blank=True)
    dc_attachment = models.FileField(upload_to='grn_docs/%Y/%m/', null=True, blank=True)
    warehouse    = models.ForeignKey('inventory.Warehouse', on_delete=models.PROTECT,
                                     related_name='grns')
    status       = models.CharField(max_length=15, choices=GRNStatus.choices,
                                    default=GRNStatus.DRAFT)
    remarks      = models.TextField(blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if not self.grn_number:
            last = (GRN.objects
                    .filter(tenant=self.tenant, grn_number__startswith='GRN')
                    .order_by('-created_at').first())
            n = 1
            if last and last.grn_number:
                try:
                    n = int(last.grn_number[3:]) + 1
                except (ValueError, IndexError):
                    pass
            self.grn_number = f'GRN{n:05d}'

        # Track if status changed to QC_PENDING for the post-save hook
        self._status_changed_to_qc_pending = False
        if self.pk:
            try:
                old = GRN.objects.get(pk=self.pk)
                if old.status != 'QC_PENDING' and self.status == 'QC_PENDING':
                    self._status_changed_to_qc_pending = True
            except GRN.DoesNotExist:
                pass
        elif self.status == 'QC_PENDING':
            self._status_changed_to_qc_pending = True

        super().save(*args, **kwargs)

        # Auto-create QC inspection orders after saving
        if getattr(self, '_status_changed_to_qc_pending', False):
            self._trigger_qc_inspection()

    def _trigger_qc_inspection(self):
        """
        Called after save when status → QC_PENDING.
        Delegates to qc.services to avoid circular imports.
        """
        try:
            from apps.vendors.models import PurchaseSettings
            settings = PurchaseSettings.objects.filter(tenant=self.tenant).first()
            if settings and not settings.grn_auto_qc:
                return
            from apps.qc.services import create_inspection_from_grn
            create_inspection_from_grn(self)
        except Exception as e:
            import logging
            logging.getLogger(__name__).warning(
                'Auto QC creation failed for GRN %s: %s', self.grn_number, e
            )

    def __str__(self):
        return self.grn_number


class GRNItem(models.Model):
    grn              = models.ForeignKey(GRN, on_delete=models.CASCADE, related_name='items')
    po_item          = models.ForeignKey(PurchaseOrderItem, on_delete=models.SET_NULL,
                                         null=True, blank=True)
    item             = models.ForeignKey('inventory.ItemMaster', on_delete=models.PROTECT)
    received_qty     = models.DecimalField(max_digits=15, decimal_places=3)
    accepted_qty     = models.DecimalField(max_digits=15, decimal_places=3, default=0,
                                           help_text="Set by QC after inspection")
    rejected_qty     = models.DecimalField(max_digits=15, decimal_places=3, default=0)
    uom              = models.CharField(max_length=30)
    batch_number     = models.CharField(max_length=100, blank=True,
                                        help_text="Assigned at GRN; auto-generated if blank")
    storage_location = models.ForeignKey('inventory.StorageLocation', on_delete=models.SET_NULL,
                                          null=True, blank=True,
                                          help_text="Assigned after QC pass")
    unit_cost        = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    def save(self, *args, **kwargs):
        # Auto-generate batch_number if item is batch-tracked and none provided
        if not self.batch_number and self.item_id:
            try:
                if self.item.is_batch_tracked:
                    import uuid as uuid_mod
                    self.batch_number = f'BT{str(uuid_mod.uuid4())[:8].upper()}'
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.grn.grn_number} — {self.item.item_code}'


# ─────────────────────────────────────────────────────────────────────────────
# Vendor Invoice
# ─────────────────────────────────────────────────────────────────────────────

class VendorInvoice(TenantModelMixin):
    """
    Vendor's invoice against a PO/GRN.
    Three-way match: PO value = GRN accepted value = Invoice amount.
    """
    id             = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=100,
                                      help_text="Vendor's own invoice number")
    po             = models.ForeignKey(PurchaseOrder, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='invoices')
    grn            = models.ForeignKey(GRN, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='invoices')
    vendor         = models.ForeignKey('vendors.Vendor', on_delete=models.PROTECT,
                                       related_name='invoices')
    invoice_date   = models.DateField()
    amount         = models.DecimalField(max_digits=15, decimal_places=2)
    tax_amount     = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_amount   = models.DecimalField(max_digits=15, decimal_places=2)
    attachment     = models.FileField(upload_to='vendor_invoices/%Y/%m/',
                                      null=True, blank=True)
    match_status   = models.CharField(max_length=10, choices=InvoiceMatchStatus.choices,
                                      default=InvoiceMatchStatus.PENDING)
    mismatch_notes = models.TextField(blank=True)
    approved_by    = models.ForeignKey(User, on_delete=models.SET_NULL,
                                       null=True, blank=True, related_name='approved_invoices')
    due_date       = models.DateField(null=True, blank=True,
                                      help_text="Calculated from PO credit_days at creation")
    payment_status = models.CharField(max_length=10, choices=PaymentStatus.choices,
                                      default=PaymentStatus.UNPAID)
    created_at     = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        # Auto-calculate due_date from vendor credit_days if not set
        if not self.due_date and self.invoice_date and self.vendor_id:
            from datetime import timedelta
            try:
                credit_days = self.vendor.credit_days or 0
                self.due_date = self.invoice_date + timedelta(days=credit_days)
            except Exception:
                pass
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.invoice_number} — {self.vendor.vendor_code}'