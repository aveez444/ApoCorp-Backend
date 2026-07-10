from django.contrib import admin
from .einvoice_models import TenantGSPConfig, EInvoiceRecord

@admin.register(TenantGSPConfig)
class TenantGSPConfigAdmin(admin.ModelAdmin):
          list_display = ['tenant', 'gsp_provider', 'gstin', 'is_active']

@admin.register(EInvoiceRecord)
class EInvoiceRecordAdmin(admin.ModelAdmin):
          list_display  = ['invoice', 'irn', 'status', 'ack_date']
          readonly_fields = ['irn', 'ack_no', 'ack_date', 'signed_qr_data',
                             'request_payload', 'raw_response']