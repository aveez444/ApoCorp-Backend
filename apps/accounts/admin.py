from django.contrib import admin
from .models import TenantUser


@admin.register(TenantUser)
class TenantUserAdmin(admin.ModelAdmin):
    list_display = ('user', 'tenant', 'role', 'is_active')
    list_filter = ('role', 'tenant')
    search_fields = ('user__username',)