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
    """
    Mixin to check Django's built-in permissions (view, add, change, delete)
    based on HTTP method.
    
    Requires either:
        - self.queryset (with model attribute)
        - self.serializer_class (with Meta.model)
        - self.model class attribute
    """

    permission_map = {
        'GET': 'view',
        'POST': 'add',
        'PUT': 'change',
        'PATCH': 'change',
        'DELETE': 'delete',
    }

    def _get_model(self):
        """Safely get the model class from queryset, serializer, or model attribute."""
        # Try from queryset
        if hasattr(self, 'queryset') and self.queryset is not None:
            return self.queryset.model
        
        # Try from serializer_class
        if hasattr(self, 'serializer_class') and self.serializer_class:
            if hasattr(self.serializer_class.Meta, 'model'):
                return self.serializer_class.Meta.model
        
        # Try from direct model attribute
        if hasattr(self, 'model') and self.model:
            return self.model
        
        # Try calling get_queryset() - but only if it doesn't need request
        # (this is a fallback, may not work if get_queryset uses request)
        if hasattr(self, 'get_queryset'):
            try:
                qs = self.get_queryset()
                if qs is not None:
                    return qs.model
            except Exception:
                pass
        
        return None

    def check_permissions(self, request):
        """Override DRF's check_permissions to add model-level permission checks."""
        # Call parent first (handles IsAuthenticated etc.)
        super().check_permissions(request)

        action = self.permission_map.get(request.method)
        if not action:
            return

        model = self._get_model()
        if model is None:
            # No model found - skip permission check (or log warning)
            return

        model_name = model._meta.model_name
        app_label = model._meta.app_label

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