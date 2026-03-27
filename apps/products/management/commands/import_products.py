import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from apps.tenants.models import Tenant
from apps.products.models import Product, ProductCategory, UnitOfMeasure, ProductType


def clean_int(value):
    if pd.isna(value) or str(value).strip() in ["", "-", "NULL"]:
        return None
    try:
        return int(float(value))
    except:
        return None


def clean_decimal(value):
    if pd.isna(value) or str(value).strip() in ["", "-", "NULL"]:
        return None
    try:
        return float(value)
    except:
        return None


def clean_str(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


# ✅ FIXED BOOLEAN HANDLER (IMPORTANT)
def clean_bool(value):
    if pd.isna(value):
        return False

    # Handles Excel numeric values: 1, 0, 1.0, 0.0
    if isinstance(value, (int, float)):
        return int(value) == 1

    val = str(value).strip().lower()

    return val in ["1", "1.0", "true", "yes", "y", "t"]


class Command(BaseCommand):

    help = "Import products from Excel file"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)

    def handle(self, *args, **kwargs):

        file_path = kwargs["file_path"]

        df = pd.read_excel(file_path)

        # ✅ normalize column names (prevents silent bugs)
        df.columns = df.columns.str.strip()

        # OPTIONAL DEBUG (run once if unsure)
        print("Columns detected:", df.columns.tolist())

        tenant = Tenant.objects.first()
        user = User.objects.first()

        if not tenant:
            self.stdout.write(self.style.ERROR("No tenant found"))
            return

        created = 0
        skipped = 0

        for index, row in df.iterrows():

            part_no = clean_str(row.get("PartNo"))
            description = clean_str(row.get("Description"))

            category_id = clean_int(row.get("CategoryID"))
            unit_name = clean_str(row.get("Unit"))
            item_type = clean_str(row.get("ItemType"))

            purchase_price = clean_decimal(row.get("PurchasePrice"))
            sale_price = clean_decimal(row.get("SalePrice"))
            lead_time = clean_int(row.get("LeadTime"))

            make = clean_str(row.get("Make"))

            # ✅ BOOLEAN FIELDS (FIXED)
            raw_mktg = row.get("IsMktgPart")
            raw_eng  = row.get("IsEngPart")

            is_mktg_part = clean_bool(raw_mktg)
            is_eng_part  = clean_bool(raw_eng)

            # DEBUG (optional - remove after first run)
            # print(f"{part_no} -> RAW: {raw_mktg}, {raw_eng} -> CLEAN: {is_mktg_part}, {is_eng_part}")

            if not part_no:
                skipped += 1
                continue

            if Product.objects.filter(tenant=tenant, part_no=part_no).exists():
                skipped += 1
                continue

            # CATEGORY
            category = None
            if category_id:
                category = ProductCategory.objects.filter(
                    tenant=tenant,
                    code=str(category_id)
                ).first()

            # UNIT
            unit = None
            if unit_name:
                unit, _ = UnitOfMeasure.objects.get_or_create(
                    tenant=tenant,
                    name=unit_name,
                    defaults={"symbol": unit_name}
                )

            # PRODUCT TYPE
            product_type = None
            if item_type:
                product_type, _ = ProductType.objects.get_or_create(
                    tenant=tenant,
                    code=item_type,
                    defaults={"name": item_type}
                )

            try:
                Product.objects.create(
                    tenant=tenant,
                    part_no=part_no,
                    name=description[:255] if description else part_no,
                    description=description,
                    category=category,
                    product_type=product_type,
                    unit=unit,
                    default_purchase_price=purchase_price,
                    default_sale_price=sale_price,
                    lead_time_days=lead_time,
                    make=make,
                    created_by=user,

                    # ✅ FINAL BOOLEAN VALUES
                    is_mktg_part=is_mktg_part,
                    is_eng_part=is_eng_part,
                )

                created += 1

            except Exception as e:
                skipped += 1
                self.stdout.write(f"Skipped row {index} due to error: {e}")

            if created % 500 == 0 and created != 0:
                self.stdout.write(f"{created} products imported...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. Created: {created}, Skipped: {skipped}"
            )
        )
        