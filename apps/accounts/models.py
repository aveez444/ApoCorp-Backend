from django.db import models
from django.contrib.auth.models import User
from apps.tenants.models import Tenant


class TenantUser(models.Model):

    ROLE_CHOICES = (
        ('manager', 'Manager'),
        ('employee', 'Employee'),
    )

    user = models.OneToOneField(User, on_delete=models.CASCADE)
    tenant = models.ForeignKey(Tenant, on_delete=models.CASCADE)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES)

    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.username} - {self.tenant.company_name}"