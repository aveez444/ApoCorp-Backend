import uuid
from django.db import models, transaction
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin
from apps.enquiries.models import Enquiry


class Quotation(TenantModelMixin):

    REVIEW_STATUS = [
        ('UNDER_REVIEW', 'Under Review'),
        ('APPROVED', 'Approved'),
        ('REJECTED', 'Rejected'),
    ]

    VISIBILITY = [
        ('INTERNAL', 'Internal'),
        ('EXTERNAL', 'External'),
    ]

    CLIENT_STATUS = [
        ('DRAFT', 'Draft'),
        ('SENT', 'Sent'),
        ('UNDER_NEGOTIATION', 'Under Negotiation'),
        ('ACCEPTED', 'Accepted'),
        ('REJECTED_BY_CLIENT', 'Rejected By Client'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    quotation_number = models.CharField(max_length=50, unique=True, blank=True)

    # ── Live FK. Customer/enquiry info is read via enquiry.customer ──
    enquiry = models.OneToOneField(
        Enquiry, on_delete=models.CASCADE, related_name="quotation"
    )

    po_number = models.CharField(max_length=100, blank=True)
    valid_till_date = models.DateField(null=True, blank=True)
    expires_at = models.DateField(null=True, blank=True)

    review_status = models.CharField(max_length=20, choices=REVIEW_STATUS, default='UNDER_REVIEW')
    visibility = models.CharField(max_length=20, choices=VISIBILITY, default='INTERNAL')
    client_status = models.CharField(max_length=30, choices=CLIENT_STATUS, default='DRAFT')

    manager_remark = models.TextField(blank=True)
    rejected_at = models.DateTimeField(null=True, blank=True)

    currency = models.CharField(max_length=10)
    exchange_rate = models.DecimalField(max_digits=10, decimal_places=4, default=1)

    total_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    grand_total = models.DecimalField(max_digits=15, decimal_places=2, default=0)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def save(self, *args, **kwargs):
        if not self.quotation_number:
            with transaction.atomic():
                last = (
                    Quotation.objects
                    .select_for_update()
                    .order_by('-created_at')
                    .first()
                )
                number = 1 if not last else int(last.quotation_number[2:6]) + 1
                self.quotation_number = f"QT{number:04d}IND"
        super().save(*args, **kwargs)

    def __str__(self):
        return self.quotation_number

class QuotationLineItem(models.Model):

    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name="line_items"
    )

    product = models.ForeignKey(
        "products.Product",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_items"
    )

    job_code = models.CharField(max_length=100, blank=True)
    customer_part_no = models.CharField(max_length=100, blank=True)
    part_no = models.CharField(max_length=100, blank=True)

    product_name_snapshot = models.CharField(max_length=255)
    description_snapshot = models.TextField(blank=True)
    hsn_snapshot = models.CharField(max_length=50, blank=True)
    unit_snapshot = models.CharField(max_length=50, blank=True)

    quantity = models.DecimalField(max_digits=10, decimal_places=2)
    unit_price = models.DecimalField(max_digits=15, decimal_places=2)

    tax_percent = models.DecimalField(max_digits=5, decimal_places=2, default=0)
    tax_group_code = models.CharField(max_length=50, blank=True)

    tax_amount = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    line_total = models.DecimalField(max_digits=15, decimal_places=2)

    def __str__(self):
        return f"{self.product_name_snapshot} - {self.quotation.quotation_number}"
    
    
class QuotationTerms(models.Model):
    quotation = models.OneToOneField(
        Quotation, on_delete=models.CASCADE, related_name="terms"
    )

    payment_terms = models.TextField(blank=True)
    sales_tax = models.CharField(max_length=100, blank=True)
    excise_duty = models.CharField(max_length=100, blank=True)
    warranty = models.TextField(blank=True)
    packing_forwarding = models.CharField(max_length=100, blank=True)
    price_basis = models.CharField(max_length=100, blank=True)
    insurance = models.CharField(max_length=100, blank=True)
    freight = models.CharField(max_length=100, blank=True)
    delivery = models.TextField(blank=True)
    validity = models.TextField(blank=True)
    decision_expected = models.CharField(max_length=100, blank=True)
    remarks = models.TextField(blank=True)


class QuotationFollowUp(models.Model):

    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name="follow_ups"
    )

    follow_up_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    follow_up_date = models.DateField()
    contact_person = models.CharField(max_length=255, blank=True)
    contact_phone = models.CharField(max_length=20, blank=True)
    contact_email = models.EmailField(blank=True)
    remarks = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)


class QuotationAttachment(models.Model):
    quotation = models.ForeignKey(
        Quotation, on_delete=models.CASCADE, related_name="attachments"
    )
    file = models.FileField(upload_to="quotation_files/")
    uploaded_at = models.DateTimeField(auto_now_add=True)