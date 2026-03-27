import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User

from apps.tenants.models import Tenant
from apps.customers.models import Customer, CustomerAddress, CustomerPOC


# ---------- CLEANERS ----------
def clean_str(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


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


def clean_bool(value):
    if pd.isna(value):
        return False
    if isinstance(value, (int, float)):
        return int(value) == 1
    return str(value).strip().lower() in ["1", "true", "yes", "y"]


def map_tier(value):
    val = str(value).strip().upper()
    if val in ["A", "B", "C"]:
        return val
    return "C"


# ---------- COMMAND ----------
class Command(BaseCommand):

    help = "Import customers from Excel"

    def add_arguments(self, parser):
        parser.add_argument("file_path", type=str)

    def handle(self, *args, **kwargs):

        file_path = kwargs["file_path"]

        df = pd.read_excel(file_path, header=1)
        df.columns = df.columns.str.strip()

        print("Columns detected:", df.columns.tolist())

        tenant = Tenant.objects.first()
        user = User.objects.first()

        if not tenant:
            self.stdout.write(self.style.ERROR("No tenant found"))
            return

        created = 0
        skipped = 0

        for index, row in df.iterrows():

            company_name = clean_str(row.get("NAME"))

            if not company_name:
                skipped += 1
                continue

            # Prevent duplicates
            if Customer.objects.filter(
                tenant=tenant,
                company_name=company_name
            ).exists():
                skipped += 1
                continue

            try:
                customer = Customer.objects.create(
                    tenant=tenant,
                    company_name=company_name,
                    tier=map_tier(row.get("Category")),
                    country=clean_str(row.get("COUNTRY")),
                    state=clean_str(row.get("STATE")),
                    city=clean_str(row.get("CITY")),
                    telephone_primary=clean_str(row.get("MobileNo"))[:20],
                    email=clean_str(row.get("Email")),
                    website=clean_str(row.get("www")),
                    default_currency=clean_str(row.get("Currency")),
                    pan_number=clean_str(row.get("PanNo"))[:20],
                    gst_number=clean_str(row.get("TaxID"))[:20],
                    credit_period_days=clean_int(row.get("CrPeriod")),
                    tds_percentage=clean_decimal(row.get("TDS_PER")),
                    
                )

                # ---------- BILLING ADDRESS ----------
                if clean_str(row.get("ADDRESS")):
                    CustomerAddress.objects.create(
                        customer=customer,
                        address_type="BILLING",
                        entity_name=company_name,
                        address_line=clean_str(row.get("ADDRESS")),
                        country=clean_str(row.get("COUNTRY")),
                        state=clean_str(row.get("STATE")),
                        city=clean_str(row.get("CITY")),
                        is_default=True,
                    )

                # ---------- SHIPPING ADDRESS ----------
                if clean_str(row.get("ShipToAddress")):
                    CustomerAddress.objects.create(
                        customer=customer,
                        address_type="SHIPPING",
                        entity_name=clean_str(row.get("ShipToName")) or company_name,
                        address_line=clean_str(row.get("ShipToAddress")),
                        country=clean_str(row.get("ShipToCountry")),
                        state=clean_str(row.get("ShipToState")),
                        city=clean_str(row.get("ShipToCity")),
                    )

                # ---------- POC ----------
                if clean_str(row.get("ContactPerson")):
                    CustomerPOC.objects.create(
                        customer=customer,
                        name=clean_str(row.get("ContactPerson")),
                        email=clean_str(row.get("Email")),
                        phone=clean_str(row.get("MobileNo")) or clean_str(row.get("TeleNo")),
                        is_primary=True,
                    )

                created += 1

            except Exception as e:
                skipped += 1
                self.stdout.write(f"Skipped row {index}: {e}")

            if created % 500 == 0 and created != 0:
                self.stdout.write(f"{created} customers imported...")

        self.stdout.write(
            self.style.SUCCESS(
                f"Import complete. Created: {created}, Skipped: {skipped}"
            )
        )