from rest_framework.permissions import BasePermission
from apps.accounts.models import TenantUser


class IsManager(BasePermission):

    def has_permission(self, request, view):
        if not request.user.is_authenticated:
            return False

        tenant_user = TenantUser.objects.filter(
            user=request.user,
            tenant=request.tenant
        ).first()

        if not tenant_user:
            return False

        return tenant_user.role == 'manager'