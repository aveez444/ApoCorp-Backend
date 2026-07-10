# apps/purchase/services.py
"""
Business logic for the Purchase module.
Views call these — never inline in the viewset.
"""

from decimal import Decimal
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# PO Approval routing
# ─────────────────────────────────────────────────────────────────────────────

def get_approval_required(po):
    """
    All POs require GM approval (flat rule — no value threshold tiers).
    PurchaseSettings.po_approval_threshold is stored for future use but
    currently always returns True.
    """
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Three-way match
# ─────────────────────────────────────────────────────────────────────────────

def three_way_match(invoice):
    """
    Compare PO value  vs  GRN accepted-quantity value  vs  Invoice amount.
    Tolerance: ±1 rupee (covers rounding differences).

    Updates invoice.match_status and invoice.mismatch_notes.
    Returns a result dict the view passes to the frontend.
    """
    TOLERANCE = Decimal('1.00')

    po  = invoice.po
    grn = invoice.grn

    po_value = po.total_value if po else Decimal('0')

    grn_value = Decimal('0')
    if grn:
        for gi in grn.items.select_related('po_item').all():
            cost = gi.unit_cost or (gi.po_item.unit_price if gi.po_item else Decimal('0'))
            grn_value += Decimal(str(gi.accepted_qty)) * Decimal(str(cost))
    grn_value = round(grn_value, 2)

    invoice_amount = invoice.total_amount
    discrepancies  = []

    if po and abs(invoice_amount - po_value) > TOLERANCE:
        discrepancies.append(
            f"Invoice amount {invoice_amount} differs from PO value {po_value} "
            f"(diff: {abs(invoice_amount - po_value)})"
        )

    if grn and abs(invoice_amount - grn_value) > TOLERANCE:
        discrepancies.append(
            f"Invoice amount {invoice_amount} differs from GRN accepted value {grn_value} "
            f"(diff: {abs(invoice_amount - grn_value)})"
        )

    matched = len(discrepancies) == 0

    invoice.match_status   = 'MATCHED' if matched else 'MISMATCH'
    invoice.mismatch_notes = '\n'.join(discrepancies)
    invoice.save(update_fields=['match_status', 'mismatch_notes'])

    return {
        'matched':        matched,
        'po_value':       po_value,
        'grn_value':      grn_value,
        'invoice_amount': invoice_amount,
        'discrepancies':  discrepancies,
        'match_status':   invoice.match_status,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Comparative statement
# ─────────────────────────────────────────────────────────────────────────────

def get_comparative_statement(rfq):
    """
    Returns all vendor quotations for an RFQ side-by-side with L1/L2/L3
    ranking per line item (ranked by unit_price ASC).

    Structure:
    {
      rfq_number: "RFQ00001",
      vendors: [{vendor_code, vendor_name, quotation_id, delivery_days, total_value, is_selected}],
      items: [
        {
          item_code, item_name, required_qty, uom,
          quotes: [
            {vendor_code, unit_price, total_price, delivery_days, brand, rank, is_l1}
          ]
        }
      ],
      recommended_vendor: {vendor_code, vendor_name, reason}
    }
    """
    from .models import VendorQuotationItem, VendorQuotation

    # All vendors who responded
    vendors = (
        VendorQuotation.objects
        .filter(rfq=rfq)
        .select_related('vendor')
        .order_by('total_value')
    )

    vendor_list = [
        {
            'vendor_code':   v.vendor.vendor_code,
            'vendor_name':   v.vendor.name,
            'quotation_id':  str(v.id),
            'delivery_days': v.delivery_days,
            'total_value':   v.total_value,
            'is_selected':   v.is_selected,
            'status':        v.status,
        }
        for v in vendors
    ]

    # Per-item ranking
    item_rows = []
    for rfq_item in rfq.items.select_related('item').all():
        quotes = (
            VendorQuotationItem.objects
            .filter(rfq_item=rfq_item)
            .select_related('quotation__vendor')
            .order_by('unit_price')
        )
        quote_list = []
        for rank, q in enumerate(quotes, start=1):
            quote_list.append({
                'vendor_code':  q.quotation.vendor.vendor_code,
                'vendor_name':  q.quotation.vendor.name,
                'unit_price':   q.unit_price,
                'discount_pct': q.discount_pct,
                'tax_pct':      q.tax_pct,
                'total_price':  q.total_price,
                'delivery_days': q.delivery_days,
                'brand':        q.brand,
                'make':         q.make,
                'country_of_origin': q.country_of_origin,
                'rank':         rank,
                'is_l1':        rank == 1,
            })

        item_rows.append({
            'rfq_item_id':   rfq_item.id,
            'item_code':     rfq_item.item.item_code,
            'item_name':     rfq_item.item.name,
            'required_qty':  rfq_item.quantity,
            'uom':           rfq_item.uom,
            'specifications': rfq_item.specifications,
            'drawing_ref':   rfq_item.drawing_ref,
            'quotes':        quote_list,
        })

    # Recommend the L1 vendor (lowest total value across all items)
    recommended = None
    if vendor_list:
        l1 = vendor_list[0]  # already sorted by total_value ASC
        recommended = {
            'vendor_code': l1['vendor_code'],
            'vendor_name': l1['vendor_name'],
            'reason':      'Lowest total quoted value (L1)',
        }

    return {
        'rfq_number':         rfq.rfq_number,
        'rfq_status':         rfq.status,
        'required_delivery_date': rfq.required_delivery_date,
        'vendors':            vendor_list,
        'items':              item_rows,
        'recommended_vendor': recommended,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Update PO received quantities after GRN QC pass
# ─────────────────────────────────────────────────────────────────────────────

def update_po_receipt_status(grn):
    """
    Called from QC close (PASS) after stock is updated.
    Updates PurchaseOrderItem.received_qty and PO.status.
    """
    po = grn.po
    if not po:
        return

    for grn_item in grn.items.filter(accepted_qty__gt=0).select_related('po_item'):
        if grn_item.po_item:
            grn_item.po_item.received_qty = (
                grn_item.po_item.received_qty + grn_item.accepted_qty
            )
            grn_item.po_item.save(update_fields=['received_qty'])

    # Recalculate PO status
    all_items    = po.items.all()
    fully_recvd  = all(i.received_qty >= i.quantity for i in all_items)
    partly_recvd = any(i.received_qty > 0 for i in all_items)

    if fully_recvd:
        po.status = 'RECEIVED'
    elif partly_recvd:
        po.status = 'PARTIALLY_RECEIVED'
    po.save(update_fields=['status'])