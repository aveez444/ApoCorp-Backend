# apps/logistics/models.py

import uuid
from django.db import models, transaction
from django.contrib.auth.models import User
from django.utils import timezone
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# SalesInvoice
# ─────────────────────────────────────────────────────────────────────────────

class SalesInvoice(TenantModelMixin):

    STATUS_CHOICES = [
        ('DRAFT', 'Draft'),
        ('CONFIRMED', 'Confirmed'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    invoice_number = models.CharField(max_length=50, unique=True, blank=True)

    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name='invoices'
    )

    # ── Link to BackOrder (nullable — set when invoice is created for a dispatch) ─
    # OneToOne: one invoice per dispatch
    back_order = models.OneToOneField(
        'BackOrder',
        on_delete=models.SET_NULL,
        related_name='invoice',
        null=True,
        blank=True
    )

    # ── Step 1: Invoice Details ───────────────────────────────────────────────
    po_number = models.CharField(max_length=100, blank=True)
    po_date = models.DateField(null=True, blank=True)
    invoice_date = models.DateField(default=timezone.localdate)
    amd_number = models.CharField(max_length=100, blank=True)
    amd_date = models.DateField(null=True, blank=True)
    location = models.CharField(max_length=255, blank=True)
    invoice_type = models.CharField(max_length=100, blank=True)

    # ── Step 2: Address Snapshots (editable at invoice time) ─────────────────
    bill_to = models.JSONField(null=True, blank=True)
    ship_to = models.JSONField(null=True, blank=True)

    # Contact person
    contact_name = models.CharField(max_length=255, blank=True)
    contact_number = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    consignee_gst = models.CharField(max_length=20, blank=True)
    consignor_gst = models.CharField(max_length=20, blank=True)
    state_code = models.CharField(max_length=100, blank=True)

    # ── Step 3: Logistics Details ─────────────────────────────────────────────
    date_of_removal = models.DateField(null=True, blank=True)
    time_of_removal = models.TimeField(null=True, blank=True)
    mode_of_transport = models.CharField(max_length=100, blank=True)
    transporter = models.CharField(max_length=255, blank=True)
    vehicle_number = models.CharField(max_length=50, blank=True)
    lr_number = models.CharField(max_length=100, blank=True)

    # ── Financial ─────────────────────────────────────────────────────────────
    payment_due_date = models.DateField(null=True, blank=True)
    net_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.invoice_number:
            with transaction.atomic():
                last = (
                    SalesInvoice.objects
                    .select_for_update()
                    .order_by('-created_at')
                    .first()
                )
                number = 1 if not last else int(last.invoice_number.split('-')[-1]) + 1
                year = timezone.now().year
                self.invoice_number = f"INV-{year}-{number:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.invoice_number


# ─────────────────────────────────────────────────────────────────────────────
# SalesInvoiceLineItem
# ─────────────────────────────────────────────────────────────────────────────

class SalesInvoiceLineItem(models.Model):

    invoice = models.ForeignKey(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name='line_items'
    )

        # CRITICAL: Link back to source of truth
    oa_line_item = models.ForeignKey(
        'orders.OALineItem',
        on_delete=models.PROTECT,  # Don't allow deletion if invoiced
        related_name='invoice_items',
        null=True,  # Allow null for migration, but should always be set
        blank=True
    )


    job_code = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    hsn_code = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)
    tax_group_code = models.CharField(max_length=50, blank=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    def __str__(self):
        return f"{self.description} - {self.invoice.invoice_number}"


# ─────────────────────────────────────────────────────────────────────────────
# PackagingSlip
# ─────────────────────────────────────────────────────────────────────────────

class PackagingSlip(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    packing_list_number = models.CharField(max_length=50, unique=True, blank=True)

    invoice = models.OneToOneField(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name='packaging_slip'
    )

    no_of_packages = models.IntegerField(default=0)
    consignee_name = models.CharField(max_length=255, blank=True)
    consignee_address = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.packing_list_number:
            with transaction.atomic():
                today_str = timezone.now().strftime('%Y%m%d')
                last = (
                    PackagingSlip.objects
                    .select_for_update()
                    .filter(packing_list_number__startswith=f"PKG-{today_str}")
                    .order_by('-packing_list_number')
                    .first()
                )
                number = 1 if not last else int(last.packing_list_number.split('-')[-1]) + 1
                self.packing_list_number = f"PKG-{today_str}-{number:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.packing_list_number


# ─────────────────────────────────────────────────────────────────────────────
# PackagingItem
# ─────────────────────────────────────────────────────────────────────────────

class PackagingItem(models.Model):

    packaging_slip = models.ForeignKey(
        PackagingSlip,
        on_delete=models.CASCADE,
        related_name='items'
    )

    serial_number = models.IntegerField()
    unit = models.CharField(max_length=50, blank=True)
    packaging_type = models.CharField(max_length=100, blank=True)
    packaging_case_no = models.CharField(max_length=100, blank=True)
    packaging_dimension = models.CharField(max_length=100, blank=True)
    dimension_metric = models.CharField(max_length=20, blank=True)
    net_weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    net_weight_metric = models.CharField(max_length=20, blank=True)
    gross_weight = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    gross_weight_metric = models.CharField(max_length=20, blank=True)
    packing_list_number = models.CharField(max_length=50, blank=True)
    description = models.TextField(blank=True)

    class Meta:
        ordering = ['serial_number']

    def __str__(self):
        return f"{self.packaging_slip.packing_list_number} - Sr.{self.serial_number}"


# ─────────────────────────────────────────────────────────────────────────────
# DeliveryChallan
# ─────────────────────────────────────────────────────────────────────────────

class DeliveryChallan(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    challan_number = models.CharField(max_length=50, unique=True, blank=True)

    invoice = models.OneToOneField(
        SalesInvoice,
        on_delete=models.CASCADE,
        related_name='delivery_challan'
    )

    challan_date = models.DateField(default=timezone.localdate)
    remark = models.TextField(blank=True)

    bill_to = models.JSONField(null=True, blank=True)
    ship_to = models.JSONField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.challan_number:
            with transaction.atomic():
                today_str = timezone.now().strftime('%Y%m%d')
                last = (
                    DeliveryChallan.objects
                    .select_for_update()
                    .filter(challan_number__startswith=f"DC-{today_str}")
                    .order_by('-challan_number')
                    .first()
                )
                number = 1 if not last else int(last.challan_number.split('-')[-1]) + 1
                self.challan_number = f"DC-{today_str}-{number:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.challan_number


# ─────────────────────────────────────────────────────────────────────────────
# BackOrder
# ─────────────────────────────────────────────────────────────────────────────

class BackOrder(TenantModelMixin):
    """
    Represents one planned dispatch chunk from an Order.
    Created manually before the invoice.
    Invoice is linked after it is created for this dispatch.

    Lifecycle:
        PENDING → INVOICED → IN_TRANSIT → DELIVERED
                           ↘ DELAYED
                           ↘ RETURNED
        Any state → CANCELLED
    """

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),           # Created, waiting for invoice
        ('INVOICED', 'Invoiced'),         # Invoice confirmed for this dispatch
        ('IN_TRANSIT', 'In Transit'),     # Dispatched, on the way
        ('OUT_FOR_DELIVERY', 'Out for Delivery'),
        ('DELIVERED', 'Delivered'),       # Fully delivered
        ('DELAYED', 'Delayed'),
        ('RETURNED', 'Returned'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    back_order_number = models.CharField(max_length=50, unique=True, blank=True)

    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.CASCADE,
        related_name='back_orders'
    )

    # Set after invoice is confirmed for this dispatch
    # (invoice.back_order is the reverse OneToOne on SalesInvoice)

    reason = models.TextField(blank=True)
    expected_dispatch_date = models.DateField(null=True, blank=True)

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='PENDING'
    )

    # Tracking fields — filled once dispatched
    # Add to BackOrder model
    tracking_status = models.CharField(max_length=20, choices=STATUS_CHOICES, null=True, blank=True)
    etd = models.DateField(null=True, blank=True)
    current_location = models.CharField(max_length=255, blank=True)
    tracking_remark = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.back_order_number:
            with transaction.atomic():
                last = (
                    BackOrder.objects
                    .select_for_update()
                    .order_by('-created_at')
                    .first()
                )
                number = 1 if not last else int(last.back_order_number.split('-')[-1]) + 1
                year = timezone.now().year
                self.back_order_number = f"BO-{year}-{number:04d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.back_order_number


# ─────────────────────────────────────────────────────────────────────────────
# BackOrderLineItem
# ─────────────────────────────────────────────────────────────────────────────

class BackOrderLineItem(models.Model):
    """
    One line in a dispatch request.
    Links back to OALineItem so we know exactly what is being dispatched
    and can compute remaining quantities per line item.
    """

    back_order = models.ForeignKey(
        BackOrder,
        on_delete=models.CASCADE,
        related_name='line_items'
    )

    # Source of truth: which OA line item is this dispatching
    oa_line_item = models.ForeignKey(
        'orders.OALineItem',
        on_delete=models.PROTECT,   # Never delete OA line if dispatches exist
        related_name='dispatch_items'
    )

    # Mirrored from OALineItem at time of dispatch creation (snapshot)
    job_code = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    hsn_code = models.CharField(max_length=50, blank=True)
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_group_code = models.CharField(max_length=50, blank=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)

    # The key field: how many units are being dispatched in this BackOrder
    quantity_dispatching = models.DecimalField(max_digits=10, decimal_places=2)

    class Meta:
        # One OA line item can only appear once per BackOrder
        unique_together = ('back_order', 'oa_line_item')

    def __str__(self):
        return (
            f"{self.back_order.back_order_number} — "
            f"{self.description} × {self.quantity_dispatching}"
        )
    
from .einvoice_models import TenantGSPConfig, EInvoiceRecord  # noqa