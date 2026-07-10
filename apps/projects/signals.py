# apps/projects/signals.py
"""
Cross-app signal handlers for the Projects module.

Connected manually in ProjectsConfig.ready() — see apps.py.

NOTE: auto_create_project_from_order has been intentionally removed.
Projects are now created manually by a Project Manager from the
Projects page, selecting a CONVERTED OA. This matches SAP PS behaviour.
"""

from django.db.models import Sum


def update_project_procurement_cost(sender, instance, **kwargs):
    """Recalculates Project.procurement_cost whenever a linked PO is saved."""
    project = getattr(instance, 'project', None)
    if not project:
        return
    total = sender.objects.filter(project=project).aggregate(
        s=Sum('total_value')
    )['s'] or 0
    project.procurement_cost = total
    project.save(update_fields=['procurement_cost'])


def update_project_inventory_cost(sender, instance, **kwargs):
    """Recalculates Project.inventory_cost whenever a linked MaterialIssueSlip is saved."""
    project = getattr(instance, 'project', None)
    if not project:
        return
    total = sender.objects.filter(project=project).aggregate(
        s=Sum('total_cost')
    )['s'] or 0
    project.inventory_cost = total
    project.save(update_fields=['inventory_cost'])