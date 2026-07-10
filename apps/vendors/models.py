# apps/vendors/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


# ─────────────────────────────────────────────────────────────────────────────
# Choices
# ─────────────────────────────────────────────────────────────────────────────

class VendorType(models.TextChoices):
    SUPPLIER      = 'SUPPLIER',      'Supplier'
    CONTRACTOR    = 'CONTRACTOR',    'Contractor'
    SUBCONTRACTOR = 'SUBCONTRACTOR', 'Sub-Contractor'
    SERVICE       = 'SERVICE',       'Service Provider'


class VendorCategory(models.TextChoices):
    MECHANICAL   = 'MECHANICAL',   'Mechanical'
    ELECTRICAL   = 'ELECTRICAL',   'Electrical'
    RAW_MATERIAL = 'RAW_MATERIAL', 'Raw Material'
    CONSUMABLE   = 'CONSUMABLE',   'Consumable'
    CIVIL        = 'CIVIL',        'Civil'
    IT           = 'IT',           'IT / Software'
    LOGISTICS    = 'LOGISTICS',    'Logistics / Transport'
    OTHER        = 'OTHER',        'Other'


class VendorStatus(models.TextChoices):
    ACTIVE      = 'ACTIVE',      'Active'
    INACTIVE    = 'INACTIVE',    'Inactive'
    BLACKLISTED = 'BLACKLISTED', 'Blacklisted'


class AccountType(models.TextChoices):
    SAVINGS = 'SAVINGS', 'Savings'
    CURRENT = 'CURRENT', 'Current'
    CC      = 'CC',      'Cash Credit'


class AddressType(models.TextChoices):
    BILLING  = 'BILLING',  'Billing'
    SHIPPING = 'SHIPPING', 'Shipping'


class VendorDocType(models.TextChoices):
    GST_CERT         = 'GST_CERT',         'GST Certificate'
    PAN              = 'PAN',              'PAN Card'
    MSME             = 'MSME',             'MSME Certificate'
    CANCELLED_CHEQUE = 'CANCELLED_CHEQUE', 'Cancelled Cheque'
    OTHER            = 'OTHER',            'Other'


# ─────────────────────────────────────────────────────────────────────────────
# Vendor (core master record)
# ─────────────────────────────────────────────────────────────────────────────

class Vendor(TenantModelMixin):
    """
    Central vendor master. Shared across Purchase, QC, and Accounts modules.

    vendor_code  — auto-generated per-tenant sequence (VND00001).
    legacy_vendor_code — nullable; populated by import_vendors_from_legacy
                         management command to map records from the old ERP.
    is_approved  — set via POST /vendors/{id}/approve/ (manager only).
                   Only approved vendors appear in the RFQ vendor picker by default.
    status       — BLACKLISTED vendors are excluded from all procurement flows.
    rating       — 0.00–5.00 decimal; updated manually or via future scoring logic.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    vendor_code        = models.CharField(max_length=20, blank=True)
    legacy_vendor_code = models.CharField(max_length=50, blank=True, null=True,
                                          help_text="Vendor code from the previous ERP. Used for migration mapping.")

    name        = models.CharField(max_length=255)
    vendor_type = models.CharField(max_length=20, choices=VendorType.choices, default=VendorType.SUPPLIER)
    category    = models.CharField(max_length=20, choices=VendorCategory.choices, default=VendorCategory.OTHER)

    # Tax / compliance
    gstin           = models.CharField(max_length=20, blank=True)
    pan             = models.CharField(max_length=15, blank=True)
    msme_registered = models.BooleanField(default=False)
    msme_number     = models.CharField(max_length=50, blank=True)

    # Commercial terms
    payment_terms = models.TextField(blank=True,
                                     help_text="e.g. 30 days net, 50% advance + 50% against documents")
    credit_days   = models.IntegerField(default=0)
    currency      = models.CharField(max_length=10, default='INR')
    lead_time_days = models.IntegerField(default=0)

    # Performance
    rating = models.DecimalField(max_digits=3, decimal_places=2, default=0,
                                 help_text="Vendor performance rating 0.00–5.00")

    # Status
    status           = models.CharField(max_length=20, choices=VendorStatus.choices, default=VendorStatus.ACTIVE)
    blacklist_reason = models.TextField(blank=True)

    # Approval
    is_approved  = models.BooleanField(default=False)
    approved_by  = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                     related_name='approved_vendors')
    approved_at  = models.DateTimeField(null=True, blank=True)

    # Audit
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='created_vendors')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']
        constraints = [
            models.UniqueConstraint(fields=['tenant', 'vendor_code'], name='unique_vendor_code_per_tenant'),
        ]
        indexes = [
            models.Index(fields=['tenant', 'status']),
            models.Index(fields=['tenant', 'category']),
            models.Index(fields=['tenant', 'vendor_type']),
        ]

    def save(self, *args, **kwargs):
        if not self.vendor_code:
            # Tenant-scoped sequential code to avoid cross-tenant collisions
            last = (Vendor.objects
                    .filter(tenant=self.tenant, vendor_code__startswith='VND')
                    .order_by('-created_at')
                    .first())
            if last and last.vendor_code:
                try:
                    n = int(last.vendor_code[3:]) + 1
                except (ValueError, IndexError):
                    n = 1
            else:
                n = 1
            self.vendor_code = f'VND{n:05d}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.vendor_code} — {self.name}'


# ─────────────────────────────────────────────────────────────────────────────
# Vendor sub-records (no TenantModelMixin — access is always via vendor FK)
# ─────────────────────────────────────────────────────────────────────────────

class VendorContact(models.Model):
    """
    One or more contacts per vendor. Mark is_primary=True for the default contact
    used in emails (RFQ, PO dispatch). Only one primary contact per vendor is
    enforced in save().
    """
    vendor      = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='contacts')
    name        = models.CharField(max_length=150)
    designation = models.CharField(max_length=100, blank=True)
    email       = models.EmailField(blank=True)
    phone       = models.CharField(max_length=20, blank=True)
    is_primary  = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_primary', 'name']

    def save(self, *args, **kwargs):
        # If this contact is being set as primary, demote others
        if self.is_primary:
            VendorContact.objects.filter(vendor=self.vendor, is_primary=True).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.name} ({self.vendor.vendor_code})'


class VendorBankDetail(models.Model):
    vendor         = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='bank_details')
    bank_name      = models.CharField(max_length=150)
    account_number = models.CharField(max_length=30)
    ifsc           = models.CharField(max_length=15)
    branch         = models.CharField(max_length=150, blank=True)
    account_type   = models.CharField(max_length=10, choices=AccountType.choices, default=AccountType.CURRENT)
    is_primary     = models.BooleanField(default=False)

    class Meta:
        ordering = ['-is_primary', 'bank_name']

    def save(self, *args, **kwargs):
        if self.is_primary:
            VendorBankDetail.objects.filter(vendor=self.vendor, is_primary=True).exclude(pk=self.pk).update(is_primary=False)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.bank_name} — {self.account_number[:4]}**** ({self.vendor.vendor_code})'


class VendorAddress(models.Model):
    vendor       = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='addresses')
    address_type = models.CharField(max_length=10, choices=AddressType.choices, default=AddressType.BILLING)
    line1        = models.CharField(max_length=255)
    line2        = models.CharField(max_length=255, blank=True)
    city         = models.CharField(max_length=100)
    state        = models.CharField(max_length=100)
    pincode      = models.CharField(max_length=10)
    country      = models.CharField(max_length=100, default='India')
    # Some vendors have different GSTINs for different branch addresses
    gstin_for_address = models.CharField(max_length=20, blank=True,
                                         help_text="Branch GSTIN if different from main vendor GSTIN")

    class Meta:
        ordering = ['address_type']

    def __str__(self):
        return f'{self.address_type}: {self.line1}, {self.city} ({self.vendor.vendor_code})'


class VendorDocument(models.Model):
    vendor      = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='documents')
    doc_type    = models.CharField(max_length=20, choices=VendorDocType.choices, default=VendorDocType.OTHER)
    file        = models.FileField(upload_to='vendor_docs/%Y/%m/')
    description = models.CharField(max_length=255, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-uploaded_at']

    def __str__(self):
        return f'{self.doc_type} — {self.vendor.vendor_code}'


# ─────────────────────────────────────────────────────────────────────────────
# Approved Vendor List (AVL)
# ─────────────────────────────────────────────────────────────────────────────

class ApprovedVendorList(TenantModelMixin):
    """
    Controls which vendors appear in the RFQ vendor picker for a given item
    or item category.

    - item_code blank  → category-level approval (vendor can supply anything in that category)
    - item_code filled → item-level approval (vendor approved for that specific item code only)

    The GET /vendors/avl/?category=&item_code= endpoint queries this model.
    """

    id            = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    vendor        = models.ForeignKey(Vendor, on_delete=models.CASCADE, related_name='avl_entries')
    item_category = models.CharField(max_length=20, choices=VendorCategory.choices)
    item_code     = models.CharField(max_length=50, blank=True,
                                     help_text="Specific item code. Leave blank for category-level approval.")
    approved_by   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                      related_name='avl_approvals')
    approved_at   = models.DateTimeField(null=True, blank=True)
    valid_until   = models.DateField(null=True, blank=True)
    remarks       = models.TextField(blank=True)

    class Meta:
        ordering = ['item_category', 'item_code']
        constraints = [
            models.UniqueConstraint(
                fields=['tenant', 'vendor', 'item_category', 'item_code'],
                name='unique_avl_entry_per_tenant'
            )
        ]
        indexes = [
            models.Index(fields=['tenant', 'item_category']),
            models.Index(fields=['tenant', 'item_code']),
        ]

    def __str__(self):
        scope = self.item_code if self.item_code else f'{self.item_category} (category)'
        return f'AVL: {self.vendor.vendor_code} → {scope}'


# ─────────────────────────────────────────────────────────────────────────────
# Purchase Settings (per-tenant config)
# ─────────────────────────────────────────────────────────────────────────────

class PurchaseSettings(models.Model):
    """
    One row per tenant. Use get_or_create in all code that reads this.
    
    po_approval_threshold:
        All POs require GM approval regardless of value (as decided).
        This field is kept for forward-compatibility — set to 0 = always approve.
    
    rfq_min_vendors:
        Minimum vendors that must be invited before an RFQ can be marked SENT.
        Default 3 (standard procurement best practice).
    
    grn_auto_qc:
        If True, saving a GRN automatically creates QCInspectionOrder records
        for each line item. Set False only for tenants with external QC systems.
    """
    # OneToOneField to Tenant — using string ref to avoid circular import
    tenant = models.OneToOneField(
        'tenants.Tenant',
        on_delete=models.CASCADE,
        related_name='purchase_settings'
    )
    po_approval_threshold = models.DecimalField(
        max_digits=15, decimal_places=2, default=0,
        help_text="0 = all POs require GM approval regardless of value."
    )
    rfq_min_vendors  = models.IntegerField(default=3)
    grn_auto_qc      = models.BooleanField(default=True)

    class Meta:
        verbose_name = 'Purchase Settings'
        verbose_name_plural = 'Purchase Settings'

    def __str__(self):
        return f'Purchase Settings — {self.tenant}'