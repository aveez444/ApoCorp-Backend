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
    regional_manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name='regional_enquiries'
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='NEW')
    rejection_reason = models.TextField(blank=True)

    # ── Tender Specific Fields ──
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

    # ── Revision Tracking Fields ──
    revision_number = models.IntegerField(default=1)
    is_latest_revision = models.BooleanField(default=True)
    parent_enquiry = models.ForeignKey(
        'self', on_delete=models.SET_NULL, null=True, blank=True,
        related_name='revisions'
    )
    revision_reason = models.TextField(blank=True, help_text="Reason for this revision")
    
    # Track what fields were changed in this revision
    changed_fields = models.JSONField(default=dict, blank=True, help_text="Stores which fields were changed")

    last_activity_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="created_enquiries"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['enquiry_number', 'is_latest_revision']),
            models.Index(fields=['parent_enquiry', 'revision_number']),
        ]

    def save(self, *args, **kwargs):
        if not self.enquiry_number:
            last = Enquiry.objects.filter(is_latest_revision=True).order_by("-created_at").first()
            number = 1 if not last else int(last.enquiry_number[3:]) + 1
            self.enquiry_number = f"ENQ{number:05d}"
        super().save(*args, **kwargs)

    def create_revision(self, updated_data, changed_by, reason=None):
        """
        Create a new revision of this enquiry.
        
        Args:
            updated_data: Dict of fields to update
            changed_by: User making the change
            reason: Reason for revision
        
        Returns:
            The new revision Enquiry instance
        """
        # Mark current as not latest
        self.is_latest_revision = False
        self.save(update_fields=['is_latest_revision'])
        
        # Create new revision
        revision = Enquiry.objects.create(
            parent_enquiry=self.parent_enquiry or self,
            revision_number=self.revision_number + 1,
            is_latest_revision=True,
            revision_reason=reason or f"Revision {self.revision_number + 1}",
            changed_fields=updated_data,
            created_by=changed_by,
            **{field: updated_data.get(field, getattr(self, field)) 
               for field in ['customer', 'subject', 'product_name', 'assigned_to', 'priority',
                            'enquiry_type', 'source_of_enquiry', 'due_date', 'target_submission_date',
                            'prospective_value', 'currency', 'region', 'regional_manager', 'status',
                            'rejection_reason', 'emd_amount', 'dd_pbg', 'emd_due_date', 'tender_number',
                            'transaction_id', 'emd_return_amount', 'emd_return_date', 'enquiry_date']}
        )
        
        return revision

    def can_be_revised(self):
        """Determine if enquiry can be revised based on status"""
        # Can revise unless status is LOST or PO_RECEIVED (closed states)
        return self.status not in ['LOST', 'PO_RECEIVED']
    
    def is_overdue(self):
        """Check if enquiry is overdue based on due_date"""
        if not self.due_date:
            return False
        from django.utils import timezone
        return self.due_date < timezone.now().date()

    def __str__(self):
        if self.revision_number > 1:
            return f"{self.enquiry_number} (R{self.revision_number})"
        return self.enquiry_number


class EnquiryAttachment(models.Model):
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(upload_to="enquiry_files/")
    uploaded_at = models.DateTimeField(auto_now_add=True)
    
    def __str__(self):
        return f"{self.enquiry.enquiry_number} - {self.file.name}"


class EnquiryDelayReason(models.Model):
    """Track delay reasons for overdue enquiries"""
    enquiry = models.ForeignKey(Enquiry, on_delete=models.CASCADE, related_name="delay_reasons")
    status_update = models.CharField(max_length=20, choices=Enquiry.STATUS_CHOICES)
    reason = models.TextField()
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name="delay_reasons")
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
    
    def __str__(self):
        return f"{self.enquiry.enquiry_number} - Delay on {self.created_at.date()}"