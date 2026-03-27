from django.contrib import admin
from .models import Customer, CustomerAddress


@admin.register(Customer)
class CustomerAdmin(admin.ModelAdmin):
    list_display = (
        'customer_code',
        'company_name',
        'default_currency',
        'is_locked',
        'is_active',
        'created_at'
    )

    list_filter = (
        'is_locked',
        'is_active',
        'default_currency'
    )

    search_fields = (
        'customer_code',
        'company_name',
        'email',
        'gst_number'
    )


@admin.register(CustomerAddress)
class CustomerAddressAdmin(admin.ModelAdmin):
    list_display = (
        'customer',
        'address_type',
        'city',
        'state',
        'country',
        'is_default'
    )

    list_filter = (
        'address_type',
        'country',
        'state'
    )