# customers/models.py - Simplified version without custom indexes
import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


class Customer(TenantModelMixin):

    TIER_CHOICES = [
        ("A", "Tier A"),
        ("B", "Tier B"),
        ("C", "Tier C"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    customer_code = models.CharField(max_length=20, unique=True, blank=True)

    company_name = models.CharField(max_length=255)
    tier = models.CharField(max_length=1, choices=TIER_CHOICES, default="C")

    region = models.CharField(max_length=100, blank=True)
    country = models.CharField(max_length=100, blank=True)
    state = models.CharField(max_length=100, blank=True)
    city = models.CharField(max_length=100, blank=True)

    is_customer = models.BooleanField(default=True)
    is_supplier = models.BooleanField(default=False)

    default_currency = models.CharField(max_length=10, blank=True)

    telephone_primary = models.CharField(max_length=20, blank=True)
    telephone_secondary = models.CharField(max_length=20, blank=True)
    email = models.EmailField(blank=True)
    website = models.CharField(max_length=255, blank=True)

    pan_number = models.CharField(max_length=20, blank=True)
    gst_number = models.CharField(max_length=20, blank=True)

    credit_period_days = models.IntegerField(null=True, blank=True)
    tds_percentage = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True)

    probable_products = models.TextField(blank=True)

    lifetime_value = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    avg_order_size = models.DecimalField(max_digits=15, decimal_places=2, default=0)
    current_projects = models.IntegerField(default=0)

    account_manager = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True
    )

    is_locked = models.BooleanField(default=False)
    locked_at = models.DateTimeField(null=True, blank=True)
    locked_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True,
        related_name="locked_customers"
    )

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["company_name"]

    def save(self, *args, **kwargs):
        if not self.customer_code:
            last = Customer.objects.order_by("-customer_code").first()

            if last and last.customer_code:
                try:
                    number = int(last.customer_code.replace("CUS", "")) + 1
                except:
                    number = 1
            else:
                number = 1

            self.customer_code = f"CUS{number:05d}"

        super().save(*args, **kwargs)

    def __str__(self):
        return self.company_name


class CustomerPOC(models.Model):
    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="pocs")
    name = models.CharField(max_length=255)
    email = models.EmailField(blank=True)
    phone = models.CharField(max_length=20, blank=True)
    designation = models.CharField(max_length=255, blank=True)
    is_primary = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)


class CustomerAddress(models.Model):

    ADDRESS_TYPE = (
        ("BILLING", "Billing"),
        ("SHIPPING", "Shipping"),
    )

    customer = models.ForeignKey(Customer, on_delete=models.CASCADE, related_name="addresses")
    address_type = models.CharField(max_length=20, choices=ADDRESS_TYPE)
    entity_name = models.CharField(max_length=255)
    country = models.CharField(max_length=100)
    state = models.CharField(max_length=100)
    city = models.CharField(max_length=100)
    address_line = models.TextField()
    contact_person = models.CharField(max_length=255, blank=True)
    contact_email = models.EmailField(blank=True)
    contact_number = models.CharField(max_length=20, blank=True)
    is_default = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)