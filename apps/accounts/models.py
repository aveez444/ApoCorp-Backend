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
    
    
# Add to existing imports
import secrets
from django.utils import timezone
from datetime import timedelta

class PasswordResetToken(models.Model):
    """
    Model to store password reset tokens with expiry
    """
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    token = models.CharField(max_length=100, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    def save(self, *args, **kwargs):
        if not self.token:
            # Generate a secure random token
            self.token = secrets.token_urlsafe(32)
        if not self.expires_at:
            # Token expires in 1 hour
            self.expires_at = timezone.now() + timedelta(hours=1)
        super().save(*args, **kwargs)

    def is_valid(self):
        """Check if token is still valid (not expired and not used)"""
        return not self.is_used and timezone.now() < self.expires_at

    def __str__(self):
        return f"Reset token for {self.user.email} - {self.created_at}"