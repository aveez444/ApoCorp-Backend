from django.db import models
from apps.tenants.models import Tenant


class TenantModelMixin(models.Model):
    tenant = models.ForeignKey(
        Tenant,
        on_delete=models.CASCADE,
        related_name="%(class)s_objects"
    )

    class Meta:
        abstract = True


from rest_framework.exceptions import PermissionDenied

class ModelPermissionMixin:

    permission_map = {
        'GET': 'view',
        'POST': 'add',
        'PUT': 'change',
        'PATCH': 'change',
        'DELETE': 'delete',
    }

    def check_permissions(self, request):
        super().check_permissions(request)

        action = self.permission_map.get(request.method)
        if not action:
            return

        model_name = self.queryset.model._meta.model_name
        app_label = self.queryset.model._meta.app_label

        required_permission = f"{app_label}.{action}_{model_name}"

        if not request.user.has_perm(required_permission):
            raise PermissionDenied(
                f"You do not have permission: {required_permission}"
            )
        
from rest_framework.exceptions import ValidationError


class CustomerLockValidationMixin:

    def validate_customer_not_locked(self, customer):
        if customer and customer.is_locked:
            raise ValidationError(
                f"Customer '{customer.company_name}' is locked. Transactions are not allowed."
            )
        