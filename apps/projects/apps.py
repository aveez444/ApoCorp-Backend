from django.apps import AppConfig


class ProjectsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.projects'
    label = 'projects'

    def ready(self):
        from django.apps import apps
        from django.db.models.signals import post_save
        from . import signals

        # auto_create_project_from_order intentionally removed.
        # Projects are created manually via POST /projects/create-from-oa/

        PurchaseOrder     = apps.get_model('purchase',   'PurchaseOrder')
        MaterialIssueSlip = apps.get_model('inventory',  'MaterialIssueSlip')

        post_save.connect(
            signals.update_project_procurement_cost,
            sender=PurchaseOrder,
            dispatch_uid='projects_update_procurement_cost',
        )
        post_save.connect(
            signals.update_project_inventory_cost,
            sender=MaterialIssueSlip,
            dispatch_uid='projects_update_inventory_cost',
        )