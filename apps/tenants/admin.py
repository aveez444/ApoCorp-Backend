from django.contrib import admin
from .models import Tenant


@admin.register(Tenant)
class TenantAdmin(admin.ModelAdmin):
    list_display = ('company_name', 'subdomain', 'plan_type', 'is_active', 'created_at')
    search_fields = ('company_name', 'subdomain')
    list_filter = ('plan_type', 'is_active')