# apps/enquiries/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin
from apps.customers.models import Customer


class Enquiry(TenantModelMixin):

    STATUS_CHOICES = [
        ('NEW', 'New Enquiry'),
        ('NEGOTIATION', 'Under Negotiation'),
        ('PO_RECEIVED', 'PO Received'),
        ('LOST', 'Enquiry Lost'),
        ('REGRET', 'Regret'),
    ]

    PRIORITY_CHOICES = [
        ('LOW', 'Low'),
        ('MEDIUM', 'Medium'),
        ('HIGH', 'High'),
    ]

    REGION_CHOICES = [
        ('NORTH', 'North'),
        ('SOUTH', 'South'),
        ('EAST', 'East'),
        ('WEST', 'West'),
        ('CENTRAL', 'Central'),
    ]

    # Updated ENQUIRY_TYPE_CHOICES
    ENQUIRY_TYPE_CHOICES = [
        ('BUDGETARY', 'Budgetary'),
        ('FIRM', 'Firm'),
        ('BID', 'Bid'),
        ('PURCHASE', 'Purchase'),
        ('NEGOTIATION', 'Negotiation'),
        ('TENDER', 'Tender'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    enquiry_number = models.CharField(max_length=50, unique=True, blank=True)
    enquiry_date = models.DateField(null=True, blank=True)

    # ── LIVE FK – no snapshot needed. Customer data is always current. ──
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="enquiries")

    subject = models.CharField(max_length=255, blank=True)
    product_name = models.CharField(max_length=255, blank=True)

    # ── Single source of truth for assignment. Quotation/OA/Order filter through this. ──
    assigned_to = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='assigned_enquiries'
    )

    priority = models.CharField(max_length=10, choices=PRIORITY_CHOICES, default='MEDIUM')

    enquiry_type = models.CharField(max_length=20, choices=ENQUIRY_TYPE_CHOICES, blank=True)
    source_of_enquiry = models.CharField(max_length=100, blank=True)

    due_date = models.DateField(null=True, blank=True)
    target_submission_date = models.DateField(null=True, blank=True)

    prospective_value = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    currency = models.CharField(max_length=10, blank=True)

    # ── Region this enquiry belongs to ──
    region = models.CharField(
        max_length=10, choices=REGION_CHOICES, blank=True, default=''
    )

    # ── Regional manager – informational note set by the employee. ──
    # Nullable; not used for access control, purely for record-keeping.
    regional_manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='regional_enquiries'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    rejection_reason = models.TextField(blank=True)

    # ── Tender Specific Fields (only applicable when enquiry_type = 'TENDER') ──
    emd_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True,
        help_text="Earnest Money Deposit amount"
    )
    dd_pbg = models.CharField(
        max_length=50, blank=True, null=True,
        help_text="DD/PBG type (Demand Draft / Performance Bank Guarantee)"
    )
    emd_due_date = models.DateField(null=True, blank=True)
    tender_number = models.CharField(max_length=100, blank=True, null=True)
    transaction_id = models.CharField(max_length=100, blank=True, null=True)
    emd_return_amount = models.DecimalField(
        max_digits=15, decimal_places=2, null=True, blank=True
    )
    emd_return_date = models.DateField(null=True, blank=True)

    last_activity_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_enquiries"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def save(self, *args, **kwargs):
        if not self.enquiry_number:
            last = Enquiry.objects.order_by("-created_at").first()
            number = 1 if not last else int(last.enquiry_number[3:]) + 1
            self.enquiry_number = f"ENQ{number:05d}"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.enquiry_number


class EnquiryAttachment(models.Model):
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="enquiry_files/")
    uploaded_at = models.DateTimeField(auto_now_add=True)