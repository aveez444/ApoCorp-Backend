from django.contrib import admin
from .models import Enquiry


@admin.register(Enquiry)
class EnquiryAdmin(admin.ModelAdmin):
    list_display = (
        'enquiry_number',
        'customer',
        'assigned_to',
        'priority',
        'status',
        'created_at',
    )
    search_fields = (
        'enquiry_number',
        'customer__company_name',
    )
    list_filter = (
        'status',
        'priority',
        'created_at',
    )
    autocomplete_fields = ('customer', 'assigned_to')

