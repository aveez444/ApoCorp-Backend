# apps/qc/services.py
"""
QC business logic.
Called by GRN model (auto QC creation) and QCInspectionOrder viewset (close action).
Kept separate so GRN model can import without circular dependency.
"""

from django.db import transaction
from django.utils import timezone


# ─────────────────────────────────────────────────────────────────────────────
# Plan resolution
# ─────────────────────────────────────────────────────────────────────────────

def resolve_inspection_plan(item, qc_type):
    """
    Find the best matching InspectionPlan for an item and QC type.
    Priority: item-level > category-level > None.
    """
    from .models import InspectionPlan

    # 1. Exact item match
    plan = InspectionPlan.objects.filter(
        item=item, qc_type=qc_type, is_active=True, item__tenant=item.tenant
    ).first()
    if plan:
        return plan

    # 2. Category match (item.category must map to item_category on the plan)
    if item.category:
        plan = InspectionPlan.objects.filter(
            item__isnull=True,
            item_category__iexact=item.category,
            qc_type=qc_type,
            is_active=True,
            tenant=item.tenant,
        ).first()
    return plan  # may be None — inspector fills in manually


# ─────────────────────────────────────────────────────────────────────────────
# Auto-create inspection orders from a GRN
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def create_inspection_from_grn(grn):
    """
    Called from GRN.save() when status → QC_PENDING.
    Creates one QCInspectionOrder per GRN item.
    Resolves InspectionPlan automatically; leaves plan=null if none found.
    """
    from .models import QCInspectionOrder

    for grn_item in grn.items.select_related('item').all():
        item = grn_item.item
        plan = resolve_inspection_plan(item, 'INWARD')

        # Determine sample qty based on sampling type
        sample_qty = grn_item.received_qty  # default: 100%
        if plan and plan.sampling_type == 'AQL':
            # Simple AQL table approximation — expand to full table later
            sample_qty = _aql_sample_size(grn_item.received_qty, plan.aql_level)
        elif plan and plan.sampling_type == 'SKIP_LOT':
            sample_qty = 1  # one unit for skip-lot

        QCInspectionOrder.objects.create(
            tenant=grn.tenant,
            qc_type='INWARD',
            reference_type='GRN',
            reference_id=grn.id,
            item=item,
            grn_item=grn_item,
            plan=plan,
            status='PENDING',
            sample_qty=sample_qty,
        )


def _aql_sample_size(lot_qty, aql_level):
    """
    Simplified AQL sample size lookup (General Inspection Level II).
    Extend to a full ISO 2859-1 table as needed.
    """
    from decimal import Decimal
    lot = int(lot_qty)
    # Code letter → sample size (General Level II)
    table = [
        (2,    2),
        (8,    3),
        (15,   5),
        (25,   8),
        (50,   13),
        (90,   20),
        (150,  32),
        (280,  50),
        (500,  80),
        (1200, 125),
        (3200, 200),
        (10000, 315),
    ]
    for threshold, n in table:
        if lot <= threshold:
            return Decimal(str(n))
    return Decimal('500')  # max


# ─────────────────────────────────────────────────────────────────────────────
# Close an inspection order
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def close_inspection(inspection_order, outcome, results_data, remarks, user):
    """
    Called from POST /qc/inspection-orders/{id}/close/

    outcome: 'PASS' | 'FAIL' | 'HOLD'
    results_data: list of {parameter_id, measured_value, status, remarks}

    PASS flow:
        1. Save QCResult rows.
        2. Update grn_item.accepted_qty / rejected_qty.
        3. Call inventory.services.receive_stock() → creates StockBatch + StockLedger.
        4. Update grn_item batch qc_status → PASSED.
        5. Update GRN.status → QC_DONE (if all items done) or STOCK_UPDATED (if all received).
        6. Call purchase.services.update_po_receipt_status().

    FAIL flow:
        1. Save QCResult rows.
        2. Set grn_item.rejected_qty = received_qty.
        3. Create NCR automatically.
        4. If batch exists, set batch.qc_status = FAILED.

    HOLD flow:
        1. Save QCResult rows.
        2. If batch exists, set batch.qc_status = ON_HOLD.
    """
    from .models import QCResult, NCR
    from apps.purchase.models import GRNItem

    # ── 1. Record results ────────────────────────────────────────────────────
    for r in results_data:
        QCResult.objects.update_or_create(
            inspection_order=inspection_order,
            parameter_id=r['parameter_id'],
            defaults={
                'measured_value': r.get('measured_value', ''),
                'status':         r.get('status', 'PASS'),
                'remarks':        r.get('remarks', ''),
            }
        )

    # ── 2. Update inspection order ───────────────────────────────────────────
    inspection_order.outcome      = outcome
    inspection_order.status       = 'COMPLETED'
    inspection_order.completed_at = timezone.now()
    inspection_order.remarks      = remarks

    # Calculate pass/fail counts from results
    all_results  = inspection_order.results.all()
    passed_count = all_results.filter(status='PASS').count()
    failed_count = all_results.filter(status='FAIL').count()
    inspection_order.passed_qty = passed_count
    inspection_order.failed_qty = failed_count
    inspection_order.save()

    grn_item = inspection_order.grn_item

    # ── PASS ─────────────────────────────────────────────────────────────────
    if outcome == 'PASS':
        if grn_item:
            accepted_qty = grn_item.received_qty  # full lot accepted
            rejected_qty = 0

            # Allow partial acceptance if inspector specified
            # (grn_item.accepted_qty already updated if inspector pre-filled)
            if grn_item.accepted_qty > 0:
                accepted_qty = grn_item.accepted_qty
                rejected_qty = grn_item.received_qty - accepted_qty
            else:
                grn_item.accepted_qty = accepted_qty
                grn_item.rejected_qty = rejected_qty
                grn_item.save(update_fields=['accepted_qty', 'rejected_qty'])

            # Receive into stock
            from apps.inventory.services import receive_stock
            batch = receive_stock(
                grn_item=grn_item,
                batch_number=grn_item.batch_number,
                storage_location=grn_item.storage_location,
                accepted_qty=accepted_qty,
                unit_cost=grn_item.unit_cost or (
                    grn_item.po_item.unit_price if grn_item.po_item else 0
                ),
                user=user,
            )

            # Link batch to inspection order
            inspection_order.batch = batch
            inspection_order.save(update_fields=['batch'])

            # Update GRN status
            _update_grn_status(grn_item.grn)

            # Update PO received qtys
            from apps.purchase.services import update_po_receipt_status
            update_po_receipt_status(grn_item.grn)

    # ── FAIL ─────────────────────────────────────────────────────────────────
    elif outcome == 'FAIL':
        if grn_item:
            grn_item.rejected_qty = grn_item.received_qty
            grn_item.accepted_qty = 0
            grn_item.save(update_fields=['rejected_qty', 'accepted_qty'])

        if inspection_order.batch:
            inspection_order.batch.qc_status = 'FAILED'
            inspection_order.batch.save(update_fields=['qc_status'])

        # Auto-create NCR
        ncr_desc = (
            f"QC failed for {inspection_order.item.item_code} "
            f"({inspection_order.qc_number}). "
            f"Failed parameters: {failed_count}."
        )
        NCR.objects.create(
            tenant=inspection_order.tenant,
            inspection_order=inspection_order,
            raised_by=user,
            description=ncr_desc,
            status='OPEN',
        )

    # ── HOLD ─────────────────────────────────────────────────────────────────
    elif outcome == 'HOLD':
        if inspection_order.batch:
            inspection_order.batch.qc_status = 'ON_HOLD'
            inspection_order.batch.save(update_fields=['qc_status'])


# ─────────────────────────────────────────────────────────────────────────────
# GRN status updater
# ─────────────────────────────────────────────────────────────────────────────

def _update_grn_status(grn):
    """
    After each QC close, recalculate GRN.status.
    QC_DONE     → all items have been inspected (regardless of outcome)
    STOCK_UPDATED → all items passed QC and stock has been updated
    """
    items = grn.items.all()

    all_inspected = all(
        item.inspection_orders.filter(status='COMPLETED').exists()
        for item in items
    )
    all_stock_updated = all(item.accepted_qty > 0 for item in items)

    if all_stock_updated:
        grn.status = 'STOCK_UPDATED'
    elif all_inspected:
        grn.status = 'QC_DONE'

    grn.save(update_fields=['status'])