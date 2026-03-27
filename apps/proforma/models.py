import uuid
from decimal import Decimal
from django.db import models
from core.mixins import TenantModelMixin
from apps.orders.models import Order


class ProformaInvoice(TenantModelMixin):

    STATUS_CHOICES = [
        ('DRAFT',     'Draft'),
        ('SENT',      'Sent'),
        ('PARTIAL',   'Partial'),
        ('PAID',      'Paid'),
        ('CANCELLED', 'Cancelled'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    proforma_number = models.CharField(max_length=50, unique=True)

    # One proforma per order — enforced at DB level
    order = models.OneToOneField(
        Order, on_delete=models.CASCADE, related_name='proforma'
    )

    currency      = models.CharField(max_length=10, default="INR")
    exchange_rate = models.DecimalField(max_digits=12, decimal_places=4, default=1)
    invoice_date  = models.DateField()

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='DRAFT')

    # ── User-editable deduction fields ───────────────────────────────────────
    ff_percentage       = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    ff_amount           = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    discount_percentage = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    discount_amount     = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    advance_percentage  = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    advance_amount      = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    # ── Server-calculated financials ─────────────────────────────────────────
    sub_total        = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_tax        = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_amount     = models.DecimalField(max_digits=15, decimal_places=2, default=0)  # incl. tax, before deductions
    total_paid       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total_receivable = models.DecimalField(max_digits=15, decimal_places=2, default=0)  # after all deductions & payments

    # Legacy aliases kept for compatibility
    net_amount     = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    gst_percentage = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    gst_amount     = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.proforma_number

    def recalculate_deductions(self):
        """
        Recompute ff_amount, discount_amount, advance_amount from their percentages,
        then set total_receivable = total_amount - deductions - total_paid.
        Call whenever percentages change OR after adding a payment.
        """
        total = self.total_amount
        self.ff_amount       = (total * self.ff_percentage       / 100).quantize(Decimal('0.01'))
        self.discount_amount = (total * self.discount_percentage / 100).quantize(Decimal('0.01'))
        self.advance_amount  = (total * self.advance_percentage  / 100).quantize(Decimal('0.01'))

        after_deductions     = total - self.ff_amount - self.discount_amount - self.advance_amount
        self.total_receivable = max(after_deductions - self.total_paid, Decimal('0'))

    def recalculate_payments(self):
        """Call after adding/removing a payment."""
        from django.db.models import Sum
        paid = self.payments.aggregate(total=Sum('amount'))['total'] or Decimal('0')
        self.total_paid = paid
        self.recalculate_deductions()

    def save_financials(self):
        self.recalculate_deductions()
        self.save()


class ProformaLineItem(models.Model):

    proforma = models.ForeignKey(
        ProformaInvoice, on_delete=models.CASCADE, related_name='line_items'
    )

    job_code         = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no          = models.CharField(max_length=100, blank=True)
    description      = models.CharField(max_length=500, blank=True)
    hsn_code         = models.CharField(max_length=50,  blank=True)
    quantity         = models.DecimalField(max_digits=10, decimal_places=2)
    unit             = models.CharField(max_length=50, blank=True)
    unit_price       = models.DecimalField(max_digits=15, decimal_places=2)
    tax_group_code   = models.CharField(max_length=50, blank=True)
    tax_percent      = models.DecimalField(max_digits=5,  decimal_places=2, default=0)
    tax_amount       = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    total            = models.DecimalField(max_digits=15, decimal_places=2, default=0)  # incl. tax


class ProformaPayment(models.Model):

    proforma = models.ForeignKey(
        ProformaInvoice, on_delete=models.CASCADE, related_name='payments'
    )

    payment_date     = models.DateField()
    amount           = models.DecimalField(max_digits=15, decimal_places=2)
    mode             = models.CharField(max_length=50)
    reference_number = models.CharField(max_length=100, blank=True)
    created_at       = models.DateTimeField(auto_now_add=True)