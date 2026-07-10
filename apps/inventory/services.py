# apps/inventory/services.py

from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, F
from django.utils import timezone

from .models import StockBatch, StockLedger, BarcodeLabel


# ─────────────────────────────────────────────────────────────────────────────
# Ledger write  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def write_ledger(*, item, batch=None, warehouse, transaction_type,
                 reference_type='', reference_id=None,
                 qty_in=None, qty_out=None, unit_cost=Decimal('0'),
                 remarks='', user=None):
    balance = (
        StockBatch.objects
        .filter(item=item, warehouse=warehouse)
        .aggregate(total=Sum('quantity_on_hand'))['total'] or Decimal('0')
    )
    return StockLedger.objects.create(
        item=item, batch=batch, warehouse=warehouse,
        transaction_type=transaction_type,
        reference_type=reference_type, reference_id=reference_id,
        qty_in=qty_in, qty_out=qty_out,
        balance_after=balance, unit_cost=unit_cost,
        remarks=remarks, created_by=user,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Stock receipt  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def receive_stock(*, grn_item, batch_number, storage_location,
                  accepted_qty, unit_cost, user):
    item      = grn_item.item
    warehouse = grn_item.grn.warehouse
    tenant    = grn_item.grn.tenant
    valuation = item.valuation_method

    if valuation == 'WEIGHTED_AVG':
        batch, created = StockBatch.objects.get_or_create(
            tenant=tenant, item=item, batch_number=batch_number, warehouse=warehouse,
            defaults={
                'storage_location': storage_location,
                'quantity_on_hand': Decimal('0'),
                'quantity_reserved': Decimal('0'),
                'unit_cost': unit_cost,
                'received_date': timezone.now().date(),
                'qc_status': 'PASSED',
                'grn': grn_item.grn,
            }
        )
        if not created:
            old_qty  = batch.quantity_on_hand
            old_cost = batch.unit_cost
            new_qty  = old_qty + Decimal(str(accepted_qty))
            if new_qty > 0:
                batch.unit_cost = (
                    (old_qty * old_cost) +
                    (Decimal(str(accepted_qty)) * Decimal(str(unit_cost)))
                ) / new_qty
            batch.quantity_on_hand = new_qty
            batch.qc_status = 'PASSED'
            batch.save()
    else:
        batch = StockBatch.objects.create(
            tenant=tenant, item=item, batch_number=batch_number,
            warehouse=warehouse, storage_location=storage_location,
            quantity_on_hand=Decimal(str(accepted_qty)),
            quantity_reserved=Decimal('0'),
            unit_cost=Decimal(str(unit_cost)),
            received_date=timezone.now().date(),
            qc_status='PASSED', grn=grn_item.grn,
        )

    write_ledger(
        item=item, batch=batch, warehouse=warehouse,
        transaction_type='GRN_RECEIPT',
        reference_type='GRN', reference_id=grn_item.grn.id,
        qty_in=Decimal(str(accepted_qty)), unit_cost=Decimal(str(unit_cost)),
        remarks=f'GRN {grn_item.grn.grn_number} — batch {batch_number}',
        user=user,
    )
    return batch


# ─────────────────────────────────────────────────────────────────────────────
# Stock availability  (updated — subtracts approved reservations)
# ─────────────────────────────────────────────────────────────────────────────

def check_stock_availability(item, required_qty, warehouse=None, exclude_project=None):
    """
    Returns dict: {available: bool, available_qty, shortfall_qty}

    available_qty = on_hand - issued(quantity_reserved on batches)
                            - approved reservations not yet issued

    exclude_project: if provided, that project's own approved reservations are
    NOT subtracted (used by run_mrp so a re-run sees its own prior reservation
    as part of its own entitlement rather than a competing lock).
    """
    from .models import StockReservation

    qs = StockBatch.objects.filter(item=item, qc_status='PASSED', tenant=item.tenant)
    if warehouse:
        qs = qs.filter(warehouse=warehouse)

    result = qs.aggregate(
        total_on_hand=Sum('quantity_on_hand'),
        total_issued=Sum('quantity_reserved'),
    )
    on_hand = result['total_on_hand'] or Decimal('0')
    issued  = result['total_issued']  or Decimal('0')

    # Approved soft-locks from other projects
    rsv_qs = StockReservation.objects.filter(
        item=item, tenant=item.tenant,
        status__in=['APPROVED', 'PARTIALLY_ISSUED'],
    )
    if warehouse:
        rsv_qs = rsv_qs.filter(warehouse=warehouse)
    if exclude_project:
        rsv_qs = rsv_qs.exclude(project=exclude_project)

    approved_locked = Decimal('0')
    for rsv in rsv_qs:
        approved_locked += rsv.remaining_qty

    available = on_hand - issued - approved_locked
    needed    = Decimal(str(required_qty))

    return {
        'available':      available >= needed,
        'available_qty':  available,
        'shortfall_qty':  max(Decimal('0'), needed - available),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Stock issue  (updated — consumes reservation if one exists for the project)
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def reserve_stock(*, slip_item, user):
    """
    FIFO batch selection for a MaterialIssueSlipItem.

    If an APPROVED StockReservation exists for this slip's project + item,
    this issue consumes against it (updates reservation.issued_qty).

    Either way, StockBatch.quantity_reserved is bumped here (marks stock as
    physically issued, removing it from available qty for everyone else).
    """
    from .models import StockReservation

    item      = slip_item.item
    project   = getattr(slip_item.slip, 'project', None)
    warehouse = _get_warehouse_for_issue(slip_item)
    needed    = Decimal(str(slip_item.issued_qty or slip_item.requested_qty))

    # Find an approved reservation for this project + item
    reservation = None
    if project:
        reservation = (
            StockReservation.objects
            .filter(
                project=project, item=item, warehouse=warehouse,
                status__in=['APPROVED', 'PARTIALLY_ISSUED'],
                tenant=item.tenant,
            )
            .first()
        )

    if reservation and reservation.remaining_qty < needed:
        raise ValueError(
            f"Approved reservation for {item.item_code} on project "
            f"{project.project_number} only covers {reservation.remaining_qty} "
            f"{item.uom} but {needed} requested. "
            f"Request a supplementary reservation or adjust qty."
        )

    # Standard FIFO batch selection
    batches = (
        StockBatch.objects
        .filter(item=item, warehouse=warehouse, qc_status='PASSED', tenant=item.tenant)
        .annotate(available=F('quantity_on_hand') - F('quantity_reserved'))
        .filter(available__gt=0)
        .order_by('received_date', 'batch_number')
        .select_for_update()
    )

    remaining   = needed
    first_batch = None

    for batch in batches:
        if remaining <= 0:
            break
        take = min(batch.available, remaining)
        batch.quantity_reserved += take
        batch.save(update_fields=['quantity_reserved'])
        write_ledger(
            item=item, batch=batch, warehouse=warehouse,
            transaction_type='ISSUE_TO_PRODUCTION',
            reference_type='ISSUE_SLIP', reference_id=slip_item.slip.id,
            qty_out=take, unit_cost=batch.unit_cost,
            remarks=f'Issue slip {slip_item.slip.slip_number}',
            user=user,
        )
        remaining -= take
        if first_batch is None:
            first_batch = batch

    if remaining > 0:
        raise ValueError(
            f"Insufficient stock for {item.item_code}. "
            f"Requested {needed}, available {needed - remaining}."
        )

    # Consume against the reservation if one exists
    if reservation:
        reservation.issued_qty += needed
        if reservation.issued_qty >= reservation.approved_qty:
            reservation.status = 'FULLY_ISSUED'
        else:
            reservation.status = 'PARTIALLY_ISSUED'
        reservation.save(update_fields=['issued_qty', 'status'])

    slip_item.batch     = first_batch
    slip_item.issued_qty = needed
    slip_item.save(update_fields=['batch', 'issued_qty'])

    return first_batch


@transaction.atomic
def release_reservation(*, slip_item):
    """Called when IssueSlip is CANCELLED. Reverses quantity_reserved on batches."""
    ledger_entries = StockLedger.objects.filter(
        reference_type='ISSUE_SLIP',
        reference_id=slip_item.slip.id,
        item=slip_item.item,
    )
    for entry in ledger_entries:
        if entry.batch and entry.qty_out:
            batch = entry.batch
            batch.quantity_reserved = max(
                Decimal('0'), batch.quantity_reserved - entry.qty_out
            )
            batch.save(update_fields=['quantity_reserved'])


# ─────────────────────────────────────────────────────────────────────────────
# Reservation management  (new)
# ─────────────────────────────────────────────────────────────────────────────

@transaction.atomic
def approve_reservation(reservation, approved_qty, approved_by):
    """
    Store manager approves a StockReservation.
    Bumps StockBatch.quantity_reserved (soft lock) so available qty
    immediately drops for everyone else.
    """
    from .models import StockReservation

    if reservation.status != 'PENDING':
        raise ValueError(f"Cannot approve a reservation in '{reservation.status}' status.")

    approved_qty = Decimal(str(approved_qty))
    if approved_qty <= 0:
        raise ValueError("Approved qty must be greater than zero.")
    if approved_qty > reservation.requested_qty:
        raise ValueError(
            f"Approved qty ({approved_qty}) cannot exceed "
            f"requested qty ({reservation.requested_qty})."
        )

    # Check truly available stock (on_hand - already issued - other approved locks)
    availability = check_stock_availability(
        item=reservation.item,
        required_qty=approved_qty,
        warehouse=reservation.warehouse,
        exclude_project=reservation.project,
    )
    if not availability['available']:
        raise ValueError(
            f"Cannot approve {approved_qty} {reservation.item.uom} — "
            f"only {availability['available_qty']} available "
            f"after other approved reservations."
        )

    # Soft-lock the stock on the batch level (FIFO — oldest batches first)
    batches = (
        StockBatch.objects
        .filter(
            item=reservation.item,
            warehouse=reservation.warehouse,
            qc_status='PASSED',
            tenant=reservation.tenant,
        )
        .annotate(available=F('quantity_on_hand') - F('quantity_reserved'))
        .filter(available__gt=0)
        .order_by('received_date', 'batch_number')
        .select_for_update()
    )

    remaining = approved_qty
    for batch in batches:
        if remaining <= 0:
            break
        lock = min(batch.available, remaining)
        batch.quantity_reserved += lock
        batch.save(update_fields=['quantity_reserved'])
        remaining -= lock

    reservation.approved_qty = approved_qty
    reservation.approved_by  = approved_by
    reservation.status       = 'APPROVED'
    reservation.save()
    return reservation



def reject_reservation(reservation, actioned_by, reason):
    """Store manager rejects. No stock change."""
    if reservation.status != 'PENDING':
        raise ValueError(f"Cannot reject a reservation in '{reservation.status}' status.")
    if not reason:
        raise ValueError("Rejection reason is required.")
    reservation.status           = 'REJECTED'
    reservation.approved_by      = actioned_by
    reservation.rejection_reason = reason
    reservation.save()
    return reservation



@transaction.atomic
def cancel_reservation(reservation, cancelled_by=None):
    """
    Cancel from any non-terminal state.
    If APPROVED or PARTIALLY_ISSUED, releases the soft lock on StockBatch.
    """
    terminal = ('FULLY_ISSUED', 'CANCELLED')
    if reservation.status in terminal:
        raise ValueError(f"Cannot cancel a reservation in '{reservation.status}' status.")

    # Release soft lock if it was already approved
    if reservation.status in ('APPROVED', 'PARTIALLY_ISSUED'):
        qty_to_release = reservation.remaining_qty
        batches = (
            StockBatch.objects
            .filter(
                item=reservation.item,
                warehouse=reservation.warehouse,
                qc_status='PASSED',
                tenant=reservation.tenant,
            )
            .filter(quantity_reserved__gt=0)
            .order_by('-received_date')  # reverse FIFO — release newest first
            .select_for_update()
        )
        remaining = qty_to_release
        for batch in batches:
            if remaining <= 0:
                break
            release = min(batch.quantity_reserved, remaining)
            batch.quantity_reserved = max(Decimal('0'), batch.quantity_reserved - release)
            batch.save(update_fields=['quantity_reserved'])
            remaining -= release

    reservation.status      = 'CANCELLED'
    reservation.approved_by = cancelled_by or reservation.approved_by
    reservation.save()
    return reservation


@transaction.atomic
def issue_stock(*, slip_item, user):
    """
    Physical stock issue — called when MaterialIssueSlip → ISSUED.
    Store manager is performing the physical handover.

    Stock accounting:
        quantity_on_hand  -= issued_qty   (stock leaves the shelf)
        quantity_reserved -= issued_qty   (soft lock consumed)
        net available is unchanged (was already blocked by reservation)

    Writes a StockLedger ISSUE_TO_PRODUCTION entry.
    Consumes the project's StockReservation.

    Raises ValueError if:
      - No approved reservation exists for this project + item
      - Reservation doesn't cover the requested qty
    """
    from .models import StockReservation

    item      = slip_item.item
    project   = getattr(slip_item.slip, 'project', None)
    warehouse = _get_warehouse_for_issue(slip_item)
    needed    = Decimal(str(slip_item.issued_qty or slip_item.requested_qty))

    # Must have an approved reservation
    reservation = None
    if project:
        reservation = (
            StockReservation.objects
            .filter(
                project=project, item=item, warehouse=warehouse,
                status__in=['APPROVED', 'PARTIALLY_ISSUED'],
                tenant=item.tenant,
            )
            .select_for_update()
            .first()
        )

    if not reservation:
        raise ValueError(
            f"No approved reservation found for {item.item_code} on project "
            f"{project.project_number if project else 'N/A'}. "
            f"Get store manager approval before issuing."
        )

    if reservation.remaining_qty < needed:
        raise ValueError(
            f"Approved reservation for {item.item_code} covers only "
            f"{reservation.remaining_qty} {item.uom} but {needed} requested."
        )

    # FIFO — consume from oldest batches that have quantity_reserved
    batches = (
        StockBatch.objects
        .filter(
            item=item, warehouse=warehouse,
            qc_status='PASSED', tenant=item.tenant,
            quantity_reserved__gt=0,
        )
        .order_by('received_date', 'batch_number')
        .select_for_update()
    )

    remaining   = needed
    first_batch = None

    for batch in batches:
        if remaining <= 0:
            break

        # Take up to what's reserved on this batch
        take = min(batch.quantity_reserved, remaining)

        # Both on_hand and reserved drop — available stays same
        batch.quantity_on_hand   -= take
        batch.quantity_reserved  -= take
        batch.save(update_fields=['quantity_on_hand', 'quantity_reserved'])

        write_ledger(
            item=item, batch=batch, warehouse=warehouse,
            transaction_type='ISSUE_TO_PRODUCTION',
            reference_type='ISSUE_SLIP', reference_id=slip_item.slip.id,
            qty_out=take, unit_cost=batch.unit_cost,
            remarks=f'Issue slip {slip_item.slip.slip_number} — project '
                    f'{project.project_number if project else "N/A"}',
            user=user,
        )

        remaining -= take
        if first_batch is None:
            first_batch = batch

    if remaining > 0:
        raise ValueError(
            f"Insufficient reserved stock for {item.item_code}. "
            f"Batch quantities don't add up — contact store manager."
        )

    # Update reservation consumed qty
    reservation.issued_qty += needed
    if reservation.issued_qty >= reservation.approved_qty:
        reservation.status = 'FULLY_ISSUED'
    else:
        reservation.status = 'PARTIALLY_ISSUED'
    reservation.save(update_fields=['issued_qty', 'status'])

    slip_item.batch      = first_batch
    slip_item.issued_qty = needed
    slip_item.save(update_fields=['batch', 'issued_qty'])

    return first_batch


@transaction.atomic
def release_issue(*, slip_item):
    """
    Called when an ISSUED MaterialIssueSlip is CANCELLED.
    Reverses quantity_on_hand and quantity_reserved on batches.
    Also rolls back reservation.issued_qty.
    """
    from .models import StockReservation, StockLedger

    project = getattr(slip_item.slip, 'project', None)

    ledger_entries = StockLedger.objects.filter(
        reference_type='ISSUE_SLIP',
        reference_id=slip_item.slip.id,
        item=slip_item.item,
    )
    total_reversed = Decimal('0')
    for entry in ledger_entries:
        if entry.batch and entry.qty_out:
            batch = entry.batch
            batch.quantity_on_hand  += entry.qty_out
            batch.quantity_reserved += entry.qty_out
            batch.save(update_fields=['quantity_on_hand', 'quantity_reserved'])
            total_reversed += entry.qty_out

    # Roll back reservation
    if project and total_reversed > 0:
        reservation = StockReservation.objects.filter(
            project=project, item=slip_item.item,
            status__in=['PARTIALLY_ISSUED', 'FULLY_ISSUED'],
            tenant=slip_item.item.tenant,
        ).first()
        if reservation:
            reservation.issued_qty = max(Decimal('0'), reservation.issued_qty - total_reversed)
            reservation.status = 'APPROVED' if reservation.issued_qty == 0 else 'PARTIALLY_ISSUED'
            reservation.save(update_fields=['issued_qty', 'status'])

def get_reservation_conflict_info(reservation):
    """
    Returns the full stock picture for a pending reservation so the
    store manager can make an informed approval/rejection decision.
    """
    from .models import StockReservation

    item      = reservation.item
    warehouse = reservation.warehouse
    tenant    = reservation.tenant

    stock = StockBatch.objects.filter(
        item=item, warehouse=warehouse, qc_status='PASSED', tenant=tenant
    ).aggregate(on_hand=Sum('quantity_on_hand'), issued=Sum('quantity_reserved'))

    on_hand = stock['on_hand'] or Decimal('0')
    issued  = stock['issued']  or Decimal('0')

    # All competing reservations for this item (excluding this one)
    competing = (
        StockReservation.objects
        .filter(item=item, warehouse=warehouse, tenant=tenant)
        .exclude(id=reservation.id)
        .filter(status__in=['PENDING', 'APPROVED', 'PARTIALLY_ISSUED'])
        .select_related('project')
        .order_by('required_by_date')
    )

    approved_locked = sum(r.remaining_qty for r in competing if r.status != 'PENDING')

    return {
        'item_code':          item.item_code,
        'item_name':          item.name,
        'uom':                item.uom,
        'on_hand':            on_hand,
        'physically_issued':  issued,
        'approved_locked_by_others': approved_locked,
        'truly_available':    on_hand - issued - approved_locked,
        'this_request':       reservation.requested_qty,
        'competing_reservations': [
            {
                'reservation_id':   str(r.id),
                'project_number':   r.project.project_number,
                'project_name':     r.project.name,
                'requested_qty':    r.requested_qty,
                'approved_qty':     r.approved_qty,
                'remaining_qty':    r.remaining_qty,
                'required_by_date': r.required_by_date,
                'status':           r.status,
            }
            for r in competing
        ],
    }


# ─────────────────────────────────────────────────────────────────────────────
# Barcode  (unchanged)
# ─────────────────────────────────────────────────────────────────────────────

def generate_barcode_string(tenant_code, item_code, batch_number, seq):
    safe_batch = str(batch_number).replace(' ', '_')
    return f'{tenant_code}-{item_code}-{safe_batch}-{seq:04d}'


def create_barcode_labels(*, item, batch, label_type, reference_id, tenant_code, count=1):
    existing = BarcodeLabel.objects.filter(item=item, batch=batch).count()
    labels = []
    for i in range(count):
        seq = existing + i + 1
        barcode_data = generate_barcode_string(
            tenant_code, item.item_code, batch.batch_number, seq
        )
        label = BarcodeLabel.objects.create(
            item=item, batch=batch, label_type=label_type,
            reference_id=reference_id, barcode_data=barcode_data,
        )
        labels.append(label)
    return labels


def resolve_barcode(barcode_data, tenant):
    try:
        label = BarcodeLabel.objects.select_related(
            'item', 'batch__storage_location', 'batch__warehouse'
        ).get(barcode_data=barcode_data, item__tenant=tenant)
    except BarcodeLabel.DoesNotExist:
        raise ValueError(f"No label found for barcode: {barcode_data}")

    batch = label.batch
    return {
        'barcode_data': barcode_data,
        'item': {
            'id': str(label.item.id), 'item_code': label.item.item_code,
            'name': label.item.name, 'uom': label.item.uom,
        },
        'batch': {
            'id': str(batch.id), 'batch_number': batch.batch_number,
            'qc_status': batch.qc_status,
            'quantity_on_hand': str(batch.quantity_on_hand),
            'quantity_available': str(batch.quantity_available),
        } if batch else None,
        'storage_location': {
            'id': batch.storage_location.id if batch and batch.storage_location else None,
            'bin_code': batch.storage_location.bin_code if batch and batch.storage_location else None,
            'warehouse': batch.warehouse.code if batch else None,
        },
        'label_type': label.label_type,
        'generated_at': label.generated_at.isoformat(),
    }


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _get_warehouse_for_issue(slip_item):
    if slip_item.batch:
        return slip_item.batch.warehouse
    from .models import Warehouse
    wh = Warehouse.objects.filter(tenant=slip_item.slip.tenant, is_active=True).first()
    if not wh:
        raise ValueError("No active warehouse found for this tenant.")
    return wh