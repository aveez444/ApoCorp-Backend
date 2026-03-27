import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


class ProductCategory(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=255)
    code = models.CharField(max_length=50, null=True, blank=True)

    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="children"
    )

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant", "name"]),
        ]

    def __str__(self):
        return self.name


class UnitOfMeasure(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name = models.CharField(max_length=100)
    symbol = models.CharField(max_length=20)

    description = models.CharField(max_length=255, blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]
        indexes = [
            models.Index(fields=["tenant", "name"]),
        ]

    def __str__(self):
        return f"{self.name} ({self.symbol})"


class ProductType(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    code = models.CharField(max_length=50)
    name = models.CharField(max_length=100)

    description = models.TextField(blank=True)

    is_active = models.BooleanField(default=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "code"],
                name="unique_product_type_per_tenant"
            )
        ]

    def __str__(self):
        return self.name

class Product(TenantModelMixin):

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    part_no = models.CharField(max_length=100, blank=True)

    name = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    category = models.ForeignKey(
        ProductCategory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products"
    )

    product_type = models.ForeignKey(
        ProductType,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    unit = models.ForeignKey(
        UnitOfMeasure,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    hsn_code = models.CharField(max_length=50, blank=True)

    brand = models.CharField(max_length=100, blank=True)
    make = models.CharField(max_length=100, blank=True)

    barcode = models.CharField(max_length=100, blank=True)

    weight = models.DecimalField(
        max_digits=10,
        decimal_places=3,
        null=True,
        blank=True
    )

    default_purchase_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True
    )

    default_sale_price = models.DecimalField(
        max_digits=15,
        decimal_places=2,
        null=True,
        blank=True
    )

    lead_time_days = models.IntegerField(null=True, blank=True)

    # ✅ NEW FIELDS
    is_mktg_part = models.BooleanField(default=False, db_index=True)
    is_eng_part  = models.BooleanField(default=False, db_index=True)

    is_active = models.BooleanField(default=True)
    is_locked = models.BooleanField(default=False)

    created_by = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True,
        blank=True
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

        constraints = [
            models.UniqueConstraint(
                fields=["tenant", "part_no"],
                name="unique_part_per_tenant"
            )
        ]

        indexes = [
            models.Index(fields=["tenant", "part_no"]),
            models.Index(fields=["tenant", "name"]),
        ]

    def __str__(self):
        return f"{self.part_no} - {self.name}"

    def generate_part_no(self):

        last_product = (
            Product.objects
            .filter(tenant=self.tenant, part_no__startswith="PRD-")
            .order_by("-part_no")
            .first()
        )

        if last_product and last_product.part_no:
            try:
                last_number = int(last_product.part_no.split("-")[1])
            except (IndexError, ValueError):
                last_number = 0
        else:
            last_number = 0

        new_number = last_number + 1
        return f"PRD-{new_number:05d}"

    def save(self, *args, **kwargs):

        if not self.part_no:
            self.part_no = self.generate_part_no()

        super().save(*args, **kwargs)