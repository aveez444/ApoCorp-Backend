import pandas as pd
from django.core.management.base import BaseCommand

from apps.tenants.models import Tenant
from apps.products.models import ProductCategory


class Command(BaseCommand):

    help = "Import product categories"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)

    def handle(self, *args, **kwargs):

        file_path = kwargs["file_path"]

        df = pd.read_excel(file_path)

        # Clean column names
        df.columns = df.columns.str.strip()

        tenant = Tenant.objects.first()

        if not tenant:
            self.stdout.write(self.style.ERROR("No tenant found"))
            return

        created = 0
        skipped = 0

        for _, row in df.iterrows():

            category_id = row.get("CategoryID")
            category_name = row.get("CategoryName")
            remark = row.get("Remark")

            # Handle NaN values properly
            if pd.isna(category_id) or pd.isna(category_name):
                skipped += 1
                continue

            category_id = str(int(category_id))
            category_name = str(category_name).strip()
            remark = "" if pd.isna(remark) else str(remark).strip()

            obj, created_flag = ProductCategory.objects.get_or_create(
                tenant=tenant,
                code=category_id,
                defaults={
                    "name": category_name,
                    "description": remark
                }
            )

            if created_flag:
                created += 1
            else:
                skipped += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Categories imported: {created}, Skipped: {skipped}"
            )
        )