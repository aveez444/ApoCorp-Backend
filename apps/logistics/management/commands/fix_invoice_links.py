# apps/logistics/management/commands/fix_invoice_links.py

from django.core.management.base import BaseCommand
from django.db import transaction
from apps.logistics.models import SalesInvoiceLineItem, BackOrder
from decimal import Decimal

class Command(BaseCommand):
    help = 'Link existing invoice line items to OALineItem via BackOrders'
    
    def handle(self, *args, **options):
        self.stdout.write("Fixing invoice line item links...")
        
        # Find invoice line items without oa_line_item
        orphan_items = SalesInvoiceLineItem.objects.filter(oa_line_item__isnull=True)
        
        fixed = 0
        for item in orphan_items:
            invoice = item.invoice
            
            # Try to find via backorder
            if invoice.back_order:
                # Match by description and quantity
                bo_item = invoice.back_order.line_items.filter(
                    quantity_dispatching=item.quantity,
                    description=item.description
                ).first()
                
                if bo_item:
                    item.oa_line_item = bo_item.oa_line_item
                    item.save()
                    fixed += 1
                    self.stdout.write(f"✓ Fixed {item.id}")
        
        self.stdout.write(self.style.SUCCESS(f"Fixed {fixed} invoice line items"))
        
        # Report remaining orphans
        remaining = SalesInvoiceLineItem.objects.filter(oa_line_item__isnull=True).count()
        if remaining > 0:
            self.stdout.write(self.style.WARNING(f"⚠️ {remaining} invoice line items still unlinked"))