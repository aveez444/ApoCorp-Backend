from django.db import models
from django.contrib.auth.models import User
from apps.tenants.models import Tenant


class SalesTarget(models.Model):

    PERIOD_CHOICES = (
        ("MONTHLY", "Monthly"),
        ("YEARLY", "Yearly"),
    )

    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)

    period_type = models.CharField(max_length=20, choices=PERIOD_CHOICES)
    year = models.IntegerField()
    month = models.IntegerField(null=True, blank=True)

    target_amount = models.DecimalField(max_digits=15, decimal_places=2)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ("tenant", "user", "period_type", "year", "month")