from django.contrib import admin
from .models import (
    OrderAcknowledgement,
    OALineItem,
    OACommercialTerms,
    Order
)

@admin.register(OrderAcknowledgement)
class OrderAcknowledgementAdmin(admin.ModelAdmin):
    list_display = (
        'oa_number',
        'quotation',
        'customer_name',
        'status',
        'currency',
        'total_value',
        'created_at'
    )
    list_filter = ('status', 'currency', 'created_at')
    search_fields = (
        'oa_number',
        'quotation__quotation_number',
        'quotation__enquiry__customer__name'
    )

    def customer_name(self, obj):
        return obj.customer.name if obj.customer else ""
    customer_name.short_description = "Customer"


@admin.register(OALineItem)
class OALineItemAdmin(admin.ModelAdmin):
    list_display = (
        'oa',
        'part_no',
        'description',
        'quantity',
        'unit_price',
        'tax_amount',
        'total'
    )
    search_fields = ('part_no', 'description')


@admin.register(OACommercialTerms)
class OACommercialTermsAdmin(admin.ModelAdmin):
    list_display = (
        'oa',
        'payment_terms',
        'net_amount',
        'igst',
        'cgst',
        'sgst',
        'total_amount'
    )


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = (
        'order_number',
        'oa',
        'status',
        'stage',
        'order_category',
        'invoice_status',
        'currency',
        'total_value',
        'created_at'
    )

    list_filter = (
        'status',
        'stage',
        'order_category',
        'invoice_status'
    )

    search_fields = (
        'order_number',
        'oa__oa_number',
        'oa__quotation__quotation_number'
    )