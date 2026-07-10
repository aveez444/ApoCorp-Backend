# apps/projects/services.py
"""
Business logic for the Projects module.
Views call these — never inline in the viewset.
"""

from django.utils import timezone


def create_project_from_oa(oa, project_manager=None, start_date=None):
    """
    Create a Project from a CONVERTED OrderAcknowledgement.
    Called manually from ProjectViewSet when PM creates a project via the UI.

    Idempotent: if the OA's order already has a project, returns the existing one.
    Validates:
      - OA is CONVERTED
      - OA has an associated Order
      - No project already exists for this OA
    """
    from .models import Project

    if oa.status != 'CONVERTED':
        raise ValueError(
            f"OA {oa.oa_number} is not CONVERTED (current status: {oa.status}). "
            "Only confirmed orders can have a project."
        )

    order = getattr(oa, 'order', None)
    if not order:
        raise ValueError(
            f"OA {oa.oa_number} has no associated Order yet."
        )

    existing = getattr(order, 'project', None)
    if existing:
        return existing

    customer = oa.customer
    if not customer:
        raise ValueError(
            f"Cannot create a Project — OA {oa.oa_number} has no resolvable customer."
        )

    name = order.order_number
    if oa.quotation and oa.quotation.enquiry:
        name = (
            f"{oa.quotation.enquiry.enquiry_number} — {customer.company_name}"
        )

    project = Project.objects.create(
        customer=customer,
        sales_order=order,
        name=name,
        contract_value=order.total_value,
        currency=order.currency,
        start_date=start_date or timezone.now().date(),
        status='DRAFT',
        project_manager=project_manager,
        tenant=order.tenant,
    )
    return project


def accept_engineering_package(package, accepted_by, notes=''):
    """
    Project Manager accepts the engineering package.
    This freezes the BOM and documents for the project.
    """
    # Import here to avoid circular imports
    from apps.engineering.services import accept_package as engineering_accept_package
    
    return engineering_accept_package(package, accepted_by, notes)


def record_cost_entry(project, cost_type, amount, reference_type='', reference_id=None,
                       description='', recorded_by=None):
    """
    Adds a manual line to the project's cost ledger — used for LABOUR, VENDOR
    and OTHER cost types. (PROCUREMENT and INVENTORY are kept in sync
    automatically by the signals in signals.py, so don't call this for those
    two types — it would double-count alongside the signal-driven roll-up.)
    """
    from .models import ProjectCostEntry
    from django.db.models import Sum

    entry = ProjectCostEntry.objects.create(
        project=project,
        cost_type=cost_type,
        amount=amount,
        reference_type=reference_type,
        reference_id=reference_id,
        description=description,
        recorded_by=recorded_by,
    )

    field_map = {'LABOUR': 'labour_cost', 'VENDOR': 'vendor_cost', 'OTHER': 'other_cost'}
    field = field_map.get(cost_type)
    if field:
        total = ProjectCostEntry.objects.filter(
            project=project, cost_type=cost_type
        ).aggregate(s=Sum('amount'))['s'] or 0
        setattr(project, field, total)
        project.save(update_fields=[field])

    return entry


def get_dashboard_data(project):
    """Builds the dashboard response structure from the design doc (1.9)."""
    from apps.purchase.models import PurchaseIndent, PurchaseOrder

    mrp_shortages = 0
    latest_run = project.mrp_runs.order_by('-run_at').first()
    if latest_run:
        mrp_shortages = latest_run.lines.filter(has_shortage=True, indent_raised=False).count()

    open_indents = PurchaseIndent.objects.filter(
        project=project
    ).exclude(status__in=['CLOSED', 'CANCELLED']).count()

    open_pos = PurchaseOrder.objects.filter(
        project=project
    ).exclude(status__in=['RECEIVED', 'CANCELLED', 'CLOSED']).count()

    return {
        'project_number': project.project_number,
        'name': project.name,
        'customer': project.customer.company_name,
        'status': project.status,
        'contract_value': project.contract_value,
        'cost_summary': {
            'procurement_cost': project.procurement_cost,
            'inventory_cost': project.inventory_cost,
            'labour_cost': project.labour_cost,
            'vendor_cost': project.vendor_cost,
            'other_cost': project.other_cost,
            'total_cost': project.total_cost,
        },
        'profitability': {
            'gross_profit': project.gross_profit,
            'margin_pct': round(project.margin_pct, 2),
        },
        'milestones': [
            {
                'id': str(m.id),
                'title': m.title,
                'due_date': m.due_date,
                'completed_date': m.completed_date,
                'is_completed': m.is_completed,
            }
            for m in project.milestones.all()
        ],
        'mrp_shortages': mrp_shortages,
        'open_indents': open_indents,
        'open_pos': open_pos,
    }


def create_material_requirements_from_package(package):
    """
    Wrapper function that calls engineering service.
    Kept here for backward compatibility.
    """
    from apps.engineering.services import create_material_requirements_from_package as eng_create_materials
    return eng_create_materials(package)