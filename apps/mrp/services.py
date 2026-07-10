# apps/mrp/services.py
from decimal import Decimal
from django.db import transaction
from django.db.models import Sum, Q
from django.utils import timezone
from django.core.exceptions import ValidationError


def run_mrp(project, triggered_by):
    """
    Execute an MRP run for a given project.
    
    Args:
        project: Project instance with an approved BOM
        triggered_by: User triggering the run
    
    Returns:
        MRPRun instance
    
    Raises:
        ValidationError: If BOM is not approved or other issues
    """
    from .models import MRPRun, MRPLine
    from apps.engineering.services import explode_bom
    from apps.inventory.models import StockBatch
    from apps.purchase.models import PurchaseOrderItem
    
    bom = project.bom
    
    if not bom:
        raise ValidationError("Project has no BOM assigned.")
    
    if bom.status != 'APPROVED':
        raise ValidationError(
            f"BOM is not approved (status: {bom.status}). Please approve the BOM first."
        )
    
    # Create MRP run
    mrp_run = MRPRun.objects.create(
        project=project,
        bom=bom,
        status='RUNNING',
        run_by=triggered_by,
    )
    
    try:
        # Explode BOM to get all required items
        explosion = explode_bom(bom)
        
        total_items = 0
        items_with_shortage = 0
        total_shortage_value = Decimal('0')
        
        # Process each item in the explosion
        for item_data in explosion:
            eng_item = item_data['item']
            inv_item = eng_item.inventory_item
            
            # Get available stock (QC PASSED, not reserved)
            available_qty = Decimal('0')
            if inv_item:
                stock_agg = StockBatch.objects.filter(
                    item=inv_item,
                    qc_status='PASSED',
                    tenant=project.tenant
                ).aggregate(
                    total_on_hand=Sum('quantity_on_hand'),
                    total_reserved=Sum('quantity_reserved')
                )
                
                total_on_hand = stock_agg['total_on_hand'] or Decimal('0')
                total_reserved = stock_agg['total_reserved'] or Decimal('0')
                available_qty = total_on_hand - total_reserved
            
        # ── FIXED: separate aggregates (was: Sum('quantity') - Sum('received_qty')) ──
            on_order_qty = Decimal('0')
            if inv_item:
                po_agg = PurchaseOrderItem.objects.filter(
                    item=inv_item,
                    po__status__in=['APPROVED', 'SENT'],
                    po__project=project,
                ).aggregate(
                    total_qty=Sum('quantity'),
                    total_received=Sum('received_qty'),
                )
                total_ordered  = po_agg['total_qty']      or Decimal('0')
                total_received = po_agg['total_received']  or Decimal('0')
                on_order_qty   = max(total_ordered - total_received, Decimal('0'))
            
            required_qty = item_data['quantity']
            shortage_qty = max(
                required_qty - available_qty - on_order_qty,
                Decimal('0')
            )
            
            has_shortage = shortage_qty > 0
            
            # Generate recommendation
            recommendation = _get_recommendation(
                eng_item, 
                shortage_qty, 
                available_qty,
                on_order_qty
            )
            
            # Create MRP line
            mrp_line = MRPLine.objects.create(
                mrp_run=mrp_run,
                engineering_item=eng_item,
                inventory_item=inv_item,
                item_class=item_data['item_class'],
                required_qty=required_qty,
                uom=item_data['uom'],
                available_qty=available_qty,
                on_order_qty=on_order_qty,
                shortage_qty=shortage_qty,
                has_shortage=has_shortage,
                recommendation=recommendation,
                bom_path=item_data.get('path', ''),
                depth=item_data.get('depth', 0),
            )
            
            total_items += 1
            if has_shortage:
                items_with_shortage += 1
                # Estimate shortage value (if inventory item has standard cost)
                if inv_item and inv_item.standard_cost:
                    total_shortage_value += shortage_qty * inv_item.standard_cost
        
        # Update run with summary statistics
        mrp_run.status = 'COMPLETED'
        mrp_run.completed_at = timezone.now()
        mrp_run.total_items = total_items
        mrp_run.items_with_shortage = items_with_shortage
        mrp_run.total_shortage_value = round(total_shortage_value, 2)
        mrp_run.save()
        _create_mrp_reservations(mrp_run, project)
        
        
        return mrp_run
    
    except Exception as e:
        mrp_run.status = 'FAILED'
        mrp_run.notes = str(e)
        mrp_run.save()
        raise


def _get_recommendation(eng_item, shortage, available, on_order):
    """Generate procurement recommendation based on item class and quantities"""
    if shortage <= 0:
        return "Sufficient stock available. No action required."
    
    cls = eng_item.item_class
    
    # Show availability context
    context = f"Required: {shortage}, Available: {available}, On Order: {on_order}"
    
    if cls == 'A':
        return (
            f"Class A item - shortage of {shortage}. {context}\n"
            "Raise RFQ immediately. Route through Comparative Statement and GM approval.\n"
            "Critical item - prioritize procurement."
        )
    elif cls == 'B':
        return (
            f"Class B item - shortage of {shortage}. {context}\n"
            "Raise PO directly if approved vendor exists, otherwise initiate RFQ.\n"
            "Standard procurement process."
        )
    else:  # Class C
        return (
            f"Class C item - shortage of {shortage}. {context}\n"
            "Consider blanket PO or petty cash purchase.\n"
            "Low value item - quick procurement allowed."
        )


@transaction.atomic
def convert_shortages_to_indent(mrp_run, line_ids=None, indent_type='PRODUCTION', 
                               notes='', raised_by=None):
    """
    Convert MRP shortages to a Purchase Indent.
    
    Args:
        mrp_run: MRPRun instance
        line_ids: List of specific MRP line IDs to convert (None = all shortages)
        indent_type: Type of indent to create
        notes: Notes to add to the indent
        raised_by: User creating the indent
    
    Returns:
        PurchaseIndent instance
    """
    from apps.purchase.models import PurchaseIndent, PurchaseIndentItem
    
    # Get lines with shortages
    shortage_lines = mrp_run.lines.filter(
        has_shortage=True,
        indent_raised=False
    )
    
    if line_ids:
        shortage_lines = shortage_lines.filter(id__in=line_ids)
    
    # Filter to only lines with inventory items
    shortage_lines = shortage_lines.filter(inventory_item__isnull=False)
    
    if not shortage_lines.exists():
        raise ValidationError("No open shortages to convert.")
    
    # Create the indent
    indent = PurchaseIndent.objects.create(
        tenant=mrp_run.tenant,
        project=mrp_run.project,
        raised_by=raised_by,
        indent_type=indent_type,
        department='Production',
        notes=notes or f"Auto-created from MRP Run {mrp_run.run_number}",
        status='DRAFT',  # Starts as draft for PM review
    )
    
    # Create indent items
    for mrp_line in shortage_lines:
        PurchaseIndentItem.objects.create(
            indent=indent,
            item=mrp_line.inventory_item,
            required_qty=mrp_line.shortage_qty,
            uom=mrp_line.uom,
            specifications=mrp_line.engineering_item.specification,
            # Store reference to MRP line
            # Could add mrp_line_id field to PurchaseIndentItem if needed
        )
        
        # Mark MRP line as converted
        mrp_line.indent_raised = True
        mrp_line.indent = indent
        mrp_line.status = 'CONVERTED'
        mrp_line.save()
    
    return indent


def get_mrp_summary(mrp_run):
    """
    Get summary statistics for an MRP run grouped by item class.
    """
    lines = mrp_run.lines.all()
    
    class_a = lines.filter(item_class='A')
    class_b = lines.filter(item_class='B')
    class_c = lines.filter(item_class='C')
    
    return {
        'total_items': lines.count(),
        'items_with_shortage': lines.filter(has_shortage=True).count(),
        'total_shortage_value': mrp_run.total_shortage_value,
        'shortage_by_class': {
            'A': class_a.filter(has_shortage=True).count(),
            'B': class_b.filter(has_shortage=True).count(),
            'C': class_c.filter(has_shortage=True).count(),
        },
        'total_by_class': {
            'A': class_a.count(),
            'B': class_b.count(),
            'C': class_c.count(),
        },
        'shortage_qty_by_class': {
            'A': class_a.aggregate(total=Sum('shortage_qty'))['total'] or 0,
            'B': class_b.aggregate(total=Sum('shortage_qty'))['total'] or 0,
            'C': class_c.aggregate(total=Sum('shortage_qty'))['total'] or 0,
        }
    }


def get_project_mrp_history(project):
    """
    Get MRP run history for a project with summary stats.
    """
    runs = project.mrp_runs.all().order_by('-run_at')
    return [
        {
            'run_number': run.run_number,
            'status': run.status,
            'run_at': run.run_at,
            'items_with_shortage': run.items_with_shortage,
            'total_items': run.total_items,
            'total_shortage_value': run.total_shortage_value,
            'bom_version': run.bom.version,
        }
        for run in runs
    ]

def _create_mrp_reservations(mrp_run, project):
    """
    For each MRP line where available_qty > 0, create a PENDING StockReservation
    so the store manager can soft-lock that stock for this project.

    Skips items with no inventory link, and skips if an unresolved reservation
    already exists for this project + item (avoids duplicate requests from re-runs).
    """
    from apps.inventory.models import StockReservation, Warehouse

    # Use the first active warehouse for this tenant as the default
    # (same fallback used in _get_warehouse_for_issue)
    warehouse = Warehouse.objects.filter(
        tenant=project.tenant, is_active=True
    ).first()
    if not warehouse:
        return

    for line in mrp_run.lines.filter(available_qty__gt=0, inventory_item__isnull=False):
        # Don't create a duplicate if one already exists (re-run scenario)
        already_exists = StockReservation.objects.filter(
            project=project,
            item=line.inventory_item,
            tenant=project.tenant,
            status__in=['PENDING', 'APPROVED', 'PARTIALLY_ISSUED'],
        ).exists()
        if already_exists:
            continue

        qty_to_reserve = min(line.available_qty, line.required_qty)

        StockReservation.objects.create(
            tenant=project.tenant,
            project=project,
            mrp_run=mrp_run,
            item=line.inventory_item,
            warehouse=warehouse,
            requested_qty=qty_to_reserve,
            required_by_date=project.end_date,
            status='PENDING',
            notes=f"Auto-created from MRP run {mrp_run.run_number}",
        )