from django.contrib import admin
from .models import Quotation, QuotationLineItem


class QuotationLineItemInline(admin.TabularInline):
    model = QuotationLineItem
    extra = 0


@admin.register(Quotation)
class QuotationAdmin(admin.ModelAdmin):

    list_display = (
        'quotation_number',
        'get_customer_name',
        'review_status',
        'visibility',
        'grand_total',
        'created_at',
    )

    search_fields = (
        'quotation_number',
        'enquiry__customer__company_name',  # Updated to search through customer
    )

    list_filter = (
        'review_status',
        'visibility',
        'created_at',
    )

    inlines = [QuotationLineItemInline]

    # 🔹 Display customer via enquiry's customer relationship
    def get_customer_name(self, obj):
        if obj.enquiry and obj.enquiry.customer:
            return obj.enquiry.customer.company_name  # or whatever field you want to display
        return None

    get_customer_name.short_description = "Customer"