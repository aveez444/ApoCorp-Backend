import uuid
from django.db import models
from django.utils import timezone
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin
from apps.quotations.models import Quotation


class OrderAcknowledgement(TenantModelMixin):

    STATUS_CHOICES = [
        ('PENDING', 'Pending'),      # Auto-created when Generate OA is clicked, pre-filled
        ('DRAFT', 'Draft'),          # User has saved edits at least once
        ('CONVERTED', 'Converted'),  # Shared — Order created
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    oa_number = models.CharField(max_length=50, unique=True)

    # ── Live FK chain: oa → quotation → enquiry → customer ──
    quotation = models.OneToOneField(
        Quotation, on_delete=models.CASCADE, related_name='oa'
    )

    # ── Intentional snapshots — confirmed address at order time ──
    billing_snapshot = models.JSONField(null=True, blank=True)
    shipping_snapshot = models.JSONField(null=True, blank=True)

    # Transport details are OA-specific (agreed per order)
    transport_details = models.JSONField(null=True, blank=True)

    # Financial values — updated on every save (not locked)
    currency = models.CharField(max_length=10, default="INR")
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    total_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_cancelled = models.BooleanField(default=False)
    cancellation_reason = models.TextField(blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='PENDING')
    last_activity_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.last_activity_at:
            self.last_activity_at = timezone.now()
        if self.is_cancelled and not self.cancelled_at:
            self.cancelled_at = timezone.now()
            self.status = "CANCELLED"

        # Auto-generate OA number if not set
        if not self.oa_number:
            if self.quotation and self.quotation.quotation_number:
                self.oa_number = f"OA-{self.quotation.quotation_number}"
            else:
                timestamp = timezone.now().strftime('%Y%m%d%H%M%S')
                self.oa_number = f"OA-{timestamp}"

        super().save(*args, **kwargs)

    @property
    def customer(self):
        return self.quotation.enquiry.customer

    @property
    def enquiry(self):
        return self.quotation.enquiry


class OALineItem(models.Model):

    oa = models.ForeignKey(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='line_items'
    )

    job_code = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no = models.CharField(max_length=100, blank=True)
    description = models.TextField(blank=True)
    hsn_code = models.CharField(max_length=50, blank=True)
    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit = models.CharField(max_length=50, blank=True)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)

    # Tax fields — stored so calculations survive save/reload
    tax_group_code = models.CharField(max_length=50, blank=True)
    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # total = unit_price * quantity + tax_amount (incl. tax)
    total = models.DecimalField(max_digits=15, decimal_places=2, default=0)


class OACommercialTerms(models.Model):

    oa = models.OneToOneField(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='commercial_terms'
    )

    payment_terms = models.CharField(max_length=150, blank=True)
    advance_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    days_after_invoicing = models.PositiveIntegerField(default=0)

    price_basis = models.CharField(max_length=100, blank=True)
    insurance = models.CharField(max_length=100, blank=True)
    inspection = models.CharField(max_length=100, blank=True)

    ld_clause = models.CharField(max_length=100, blank=True)
    test_certificate = models.CharField(max_length=100, blank=True)
    warranty = models.CharField(max_length=255, blank=True)

    drawing_approval = models.CharField(max_length=100, blank=True)
    freight_charges = models.CharField(max_length=150, blank=True)

    abg_format = models.CharField(max_length=100, blank=True)
    pbg_format = models.CharField(max_length=100, blank=True)
    sd_format = models.CharField(max_length=100, blank=True)

    dispatch_clearance = models.CharField(max_length=100, blank=True)
    commissioning_support = models.CharField(max_length=150, blank=True)

    schedule_dispatch_date = models.DateField(null=True, blank=True)

    net_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    igst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    cgst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    sgst = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    channel_partner_name = models.CharField(max_length=255, blank=True)
    consultant_name = models.CharField(max_length=255, blank=True)

    commission_percentage = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    commission_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    consultant_charges = models.DecimalField(max_digits=15, decimal_places=2, default=0)


class Order(TenantModelMixin):

    STATUS_CHOICES = [
        ('HOLD', 'Hold'),
        ('IN_PROGRESS', 'In Progress'),
        ('COMPLETED', 'Completed'),
    ]

    STAGE_CHOICES = [
        ('PLANNING', 'Planning'),
        ('ENGINEERING', 'Engineering'),
        ('PRODUCTION', 'Production'),
        ('QA', 'QA'),
        ('DISPATCH', 'Dispatch'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    order_number = models.CharField(max_length=50, unique=True)

    oa = models.OneToOneField(
        OrderAcknowledgement, on_delete=models.CASCADE, related_name='order'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='IN_PROGRESS')
    stage = models.CharField(max_length=20, choices=STAGE_CHOICES, default='PLANNING')

    currency = models.CharField(max_length=10)
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    total_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    advance_paid = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.order_number