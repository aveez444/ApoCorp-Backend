# apps/reports/field_registry.py
#
# Single source of truth for every column that can appear in a custom report.
#
# Structure:
#   MODULE_REGISTRY  – human-facing module list (order matters for UI)
#   FIELD_REGISTRY   – per-module field definitions
#
# Each field definition:
#   path      – ORM path relative to Enquiry queryset (used in .values())
#   label     – default column header shown to the user
#   type      – used by the frontend to render filter widgets
#               str | int | decimal | date | datetime | bool | choice
#   choices   – (optional) list of (value, label) for choice fields
#   filterable– whether this field can be used as a filter (default True)
#   sortable  – whether ORDER BY is allowed on this field (default True)

MODULE_REGISTRY = [
    {"key": "enquiry",  "label": "Enquiries"},
    {"key": "customer", "label": "Customers"},
    {"key": "quotation","label": "Quotations"},
    {"key": "oa",       "label": "Order Acknowledgements"},
    {"key": "proforma", "label": "Proforma Invoices"},
    {"key": "logistics", "label": "Logistics"},  # ← ADD THIS LINE
]

# These select_related paths are added automatically when a module is included.
MODULE_JOINS = {
    "customer":  ["customer"],
    "enquiry":   [],                                          # root model
    "quotation": ["quotation"],
    "oa":        ["quotation", "quotation__oa"],
    "proforma":  ["quotation", "quotation__oa",
                  "quotation__oa__order",
                  "quotation__oa__order__proforma"],
    "logistics": ["quotation", "quotation__oa", "quotation__oa__order"],  # ← ADD THIS LINE              
}

FIELD_REGISTRY = {

    # ─────────────────────────────────────────────────────────────────────────
    # ENQUIRY  (root – always present)
    # ─────────────────────────────────────────────────────────────────────────
    "enquiry": {
        "enquiry_number": {
            "path":  "enquiry_number",
            "label": "Enquiry No.",
            "type":  "str",
        },
        "enquiry_date": {
            "path":  "enquiry_date",
            "label": "Enquiry Date",
            "type":  "date",
        },
        "subject": {
            "path":  "subject",
            "label": "Subject",
            "type":  "str",
        },
        "product_name": {
            "path":  "product_name",
            "label": "Product Name",
            "type":  "str",
        },
        "status": {
            "path":  "status",
            "label": "Enquiry Status",
            "type":  "choice",
            "choices": [
                ("NEW",         "New"),
                ("NEGOTIATION", "Under Negotiation"),
                ("PO_RECEIVED", "PO Received"),
                ("LOST",        "Lost"),
                ("REGRET",      "Regret"),
            ],
        },
        "priority": {
            "path":  "priority",
            "label": "Priority",
            "type":  "choice",
            "choices": [
                ("LOW",    "Low"),
                ("MEDIUM", "Medium"),
                ("HIGH",   "High"),
            ],
        },
        "enquiry_type": {
            "path":  "enquiry_type",
            "label": "Enquiry Type",
            "type":  "choice",
            "choices": [
                ("BUDGETARY",   "Budgetary"),
                ("FIRM",        "Firm"),
                ("BID",         "Bid"),
                ("PURCHASE",    "Purchase"),
                ("NEGOTIATION", "Negotiation"),
                ("TENDER",      "Tender"),
            ],
        },
        "source_of_enquiry": {
            "path":  "source_of_enquiry",
            "label": "Source",
            "type":  "str",
        },
        "region": {
            "path":  "region",
            "label": "Region",
            "type":  "choice",
            "choices": [
                ("NORTH",   "North"),
                ("SOUTH",   "South"),
                ("EAST",    "East"),
                ("WEST",    "West"),
                ("CENTRAL", "Central"),
            ],
        },
        "prospective_value": {
            "path":  "prospective_value",
            "label": "Prospective Value",
            "type":  "decimal",
        },
        "currency": {
            "path":  "currency",
            "label": "Currency",
            "type":  "str",
        },
        "due_date": {
            "path":  "due_date",
            "label": "Due Date",
            "type":  "date",
        },
        "target_submission_date": {
            "path":  "target_submission_date",
            "label": "Target Submission",
            "type":  "date",
        },
        "assigned_to": {
            "path":      "assigned_to__username",
            "label":     "Assigned To",
            "type":      "str",
            "filterable": False,   # FK user lookup – filter by name not practical
        },
        "assigned_to_name": {
            "path":      "assigned_to__first_name",
            "label":     "Assigned To (First Name)",
            "type":      "str",
            "filterable": False,
        },
        "created_by": {
            "path":      "created_by__username",
            "label":     "Created By",
            "type":      "str",
            "filterable": False,
        },
        "created_at": {
            "path":  "created_at",
            "label": "Created At",
            "type":  "datetime",
        },
        "last_activity_at": {
            "path":  "last_activity_at",
            "label": "Last Activity",
            "type":  "datetime",
        },
        # Tender-specific
        "tender_number": {
            "path":  "tender_number",
            "label": "Tender No.",
            "type":  "str",
        },
        "emd_amount": {
            "path":  "emd_amount",
            "label": "EMD Amount",
            "type":  "decimal",
        },
        "emd_due_date": {
            "path":  "emd_due_date",
            "label": "EMD Due Date",
            "type":  "date",
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # CUSTOMER
    # ─────────────────────────────────────────────────────────────────────────
    "customer": {
        "customer_code": {
            "path":  "customer__customer_code",
            "label": "Customer Code",
            "type":  "str",
        },
        "company_name": {
            "path":  "customer__company_name",
            "label": "Company Name",
            "type":  "str",
        },
        "tier": {
            "path":  "customer__tier",
            "label": "Tier",
            "type":  "choice",
            "choices": [("A", "Tier A"), ("B", "Tier B"), ("C", "Tier C")],
        },
        "region": {
            "path":  "customer__region",
            "label": "Customer Region",
            "type":  "str",
        },
        "country": {
            "path":  "customer__country",
            "label": "Country",
            "type":  "str",
        },
        "state": {
            "path":  "customer__state",
            "label": "State",
            "type":  "str",
        },
        "city": {
            "path":  "customer__city",
            "label": "City",
            "type":  "str",
        },
        "email": {
            "path":  "customer__email",
            "label": "Email",
            "type":  "str",
        },
        "telephone_primary": {
            "path":  "customer__telephone_primary",
            "label": "Phone",
            "type":  "str",
        },
        "gst_number": {
            "path":  "customer__gst_number",
            "label": "GST No.",
            "type":  "str",
        },
        "pan_number": {
            "path":  "customer__pan_number",
            "label": "PAN No.",
            "type":  "str",
        },
        "default_currency": {
            "path":  "customer__default_currency",
            "label": "Default Currency",
            "type":  "str",
        },
        "account_manager": {
            "path":      "customer__account_manager__username",
            "label":     "Account Manager",
            "type":      "str",
            "filterable": False,
        },
        "is_active": {
            "path":  "customer__is_active",
            "label": "Customer Active",
            "type":  "bool",
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # QUOTATION
    # ─────────────────────────────────────────────────────────────────────────
    "quotation": {
        "quotation_number": {
            "path":  "quotation__quotation_number",
            "label": "Quotation No.",
            "type":  "str",
        },
        "po_number": {
            "path":  "quotation__po_number",
            "label": "PO No.",
            "type":  "str",
        },
        "review_status": {
            "path":  "quotation__review_status",
            "label": "Review Status",
            "type":  "choice",
            "choices": [
                ("UNDER_REVIEW", "Under Review"),
                ("APPROVED",     "Approved"),
                ("REJECTED",     "Rejected"),
            ],
        },
        "client_status": {
            "path":  "quotation__client_status",
            "label": "Client Status",
            "type":  "choice",
            "choices": [
                ("DRAFT",              "Draft"),
                ("SENT",               "Sent"),
                ("UNDER_NEGOTIATION",  "Under Negotiation"),
                ("ACCEPTED",           "Accepted"),
                ("REJECTED_BY_CLIENT", "Rejected by Client"),
            ],
        },
        "visibility": {
            "path":  "quotation__visibility",
            "label": "Visibility",
            "type":  "choice",
            "choices": [("INTERNAL", "Internal"), ("EXTERNAL", "External")],
        },
        "currency": {
            "path":  "quotation__currency",
            "label": "Quote Currency",
            "type":  "str",
        },
        "total_amount": {
            "path":  "quotation__total_amount",
            "label": "Subtotal",
            "type":  "decimal",
        },
        "tax_amount": {
            "path":  "quotation__tax_amount",
            "label": "Tax Amount",
            "type":  "decimal",
        },
        "grand_total": {
            "path":  "quotation__grand_total",
            "label": "Grand Total",
            "type":  "decimal",
        },
        "valid_till_date": {
            "path":  "quotation__valid_till_date",
            "label": "Valid Till",
            "type":  "date",
        },
        "created_at": {
            "path":  "quotation__created_at",
            "label": "Quote Created At",
            "type":  "datetime",
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # ORDER ACKNOWLEDGEMENT
    # ─────────────────────────────────────────────────────────────────────────
    "oa": {
        "oa_number": {
            "path":  "quotation__oa__oa_number",
            "label": "OA No.",
            "type":  "str",
        },
        "status": {
            "path":  "quotation__oa__status",
            "label": "OA Status",
            "type":  "choice",
            "choices": [
                ("PENDING",   "Pending"),
                ("DRAFT",     "Draft"),
                ("CONVERTED", "Converted"),
                ("CANCELLED", "Cancelled"),
            ],
        },
        "currency": {
            "path":  "quotation__oa__currency",
            "label": "OA Currency",
            "type":  "str",
        },
        "total_value": {
            "path":  "quotation__oa__total_value",
            "label": "OA Total Value",
            "type":  "decimal",
        },
        "is_cancelled": {
            "path":  "quotation__oa__is_cancelled",
            "label": "OA Cancelled",
            "type":  "bool",
        },
        "created_at": {
            "path":  "quotation__oa__created_at",
            "label": "OA Created At",
            "type":  "datetime",
        },
        "last_activity_at": {
            "path":  "quotation__oa__last_activity_at",
            "label": "OA Last Activity",
            "type":  "datetime",
        },
    },

    # ─────────────────────────────────────────────────────────────────────────
    # PROFORMA INVOICE
    # ─────────────────────────────────────────────────────────────────────────
    "proforma": {
        "proforma_number": {
            "path":  "quotation__oa__order__proforma__proforma_number",
            "label": "Proforma No.",
            "type":  "str",
        },
        "status": {
            "path":  "quotation__oa__order__proforma__status",
            "label": "Proforma Status",
            "type":  "choice",
            "choices": [
                ("DRAFT",     "Draft"),
                ("SENT",      "Sent"),
                ("PARTIAL",   "Partial"),
                ("PAID",      "Paid"),
                ("CANCELLED", "Cancelled"),
            ],
        },
        "invoice_date": {
            "path":  "quotation__oa__order__proforma__invoice_date",
            "label": "Invoice Date",
            "type":  "date",
        },
        "sub_total": {
            "path":  "quotation__oa__order__proforma__sub_total",
            "label": "Proforma Subtotal",
            "type":  "decimal",
        },
        "total_tax": {
            "path":  "quotation__oa__order__proforma__total_tax",
            "label": "Proforma Tax",
            "type":  "decimal",
        },
        "total_amount": {
            "path":  "quotation__oa__order__proforma__total_amount",
            "label": "Proforma Total",
            "type":  "decimal",
        },
        "total_paid": {
            "path":  "quotation__oa__order__proforma__total_paid",
            "label": "Amount Paid",
            "type":  "decimal",
        },
        "total_receivable": {
            "path":  "quotation__oa__order__proforma__total_receivable",
            "label": "Amount Receivable",
            "type":  "decimal",
        },
        "advance_amount": {
            "path":  "quotation__oa__order__proforma__advance_amount",
            "label": "Advance Amount",
            "type":  "decimal",
        },
        "ff_percentage": {
            "path":  "quotation__oa__order__proforma__ff_percentage",
            "label": "FF %",
            "type":  "decimal",
        },
        "discount_percentage": {
            "path":  "quotation__oa__order__proforma__discount_percentage",
            "label": "Discount %",
            "type":  "decimal",
        },
        "currency": {
            "path":  "quotation__oa__order__proforma__currency",
            "label": "Proforma Currency",
            "type":  "str",
        },
        "created_at": {
            "path":  "quotation__oa__order__proforma__created_at",
            "label": "Proforma Created",
            "type":  "datetime",
        },
    },

        # ─────────────────────────────────────────────────────────────────────────
    # LOGISTICS (BackOrders, Invoices, Delivery Challans, Packaging Slips)
    # ─────────────────────────────────────────────────────────────────────────
    "logistics": {
        # ========== BACK ORDER FIELDS ==========
        "back_order_number": {
            "path":  "quotation__oa__order__back_orders__back_order_number",
            "label": "Back Order No.",
            "type":  "str",
        },
        "back_order_status": {
            "path":  "quotation__oa__order__back_orders__status",
            "label": "Back Order Status",
            "type":  "choice",
            "choices": [
                ("PENDING", "Pending"),
                ("INVOICED", "Invoiced"),
                ("IN_TRANSIT", "In Transit"),
                ("OUT_FOR_DELIVERY", "Out for Delivery"),
                ("DELIVERED", "Delivered"),
                ("DELAYED", "Delayed"),
                ("RETURNED", "Returned"),
                ("CANCELLED", "Cancelled"),
            ],
        },
        "back_order_reason": {
            "path":  "quotation__oa__order__back_orders__reason",
            "label": "Back Order Reason",
            "type":  "str",
        },
        "expected_dispatch_date": {
            "path":  "quotation__oa__order__back_orders__expected_dispatch_date",
            "label": "Expected Dispatch Date",
            "type":  "date",
        },
        "back_order_created_at": {
            "path":  "quotation__oa__order__back_orders__created_at",
            "label": "Back Order Created",
            "type":  "datetime",
        },
        
        # ========== TRACKING FIELDS ==========
        "tracking_status": {
            "path":  "quotation__oa__order__back_orders__tracking_status",
            "label": "Tracking Status",
            "type":  "str",
        },
        "current_location": {
            "path":  "quotation__oa__order__back_orders__current_location",
            "label": "Current Location",
            "type":  "str",
        },
        "estimated_delivery_date": {
            "path":  "quotation__oa__order__back_orders__etd",
            "label": "Estimated Delivery Date",
            "type":  "date",
        },
        "tracking_remark": {
            "path":  "quotation__oa__order__back_orders__tracking_remark",
            "label": "Tracking Remark",
            "type":  "str",
        },
        
        # ========== INVOICE FIELDS ==========
        "invoice_number": {
            "path":  "quotation__oa__order__invoices__invoice_number",
            "label": "Invoice No.",
            "type":  "str",
        },
        "invoice_status": {
            "path":  "quotation__oa__order__invoices__status",
            "label": "Invoice Status",
            "type":  "choice",
            "choices": [
                ("DRAFT", "Draft"),
                ("CONFIRMED", "Confirmed"),
                ("CANCELLED", "Cancelled"),
            ],
        },
        "invoice_date": {
            "path":  "quotation__oa__order__invoices__invoice_date",
            "label": "Invoice Date",
            "type":  "date",
        },
        "invoice_po_number": {
            "path":  "quotation__oa__order__invoices__po_number",
            "label": "PO Number (Invoice)",
            "type":  "str",
        },
        "invoice_po_date": {
            "path":  "quotation__oa__order__invoices__po_date",
            "label": "PO Date",
            "type":  "date",
        },
        "amd_number": {
            "path":  "quotation__oa__order__invoices__amd_number",
            "label": "AMD Number",
            "type":  "str",
        },
        "amd_date": {
            "path":  "quotation__oa__order__invoices__amd_date",
            "label": "AMD Date",
            "type":  "date",
        },
        "invoice_location": {
            "path":  "quotation__oa__order__invoices__location",
            "label": "Location",
            "type":  "str",
        },
        "invoice_type": {
            "path":  "quotation__oa__order__invoices__invoice_type",
            "label": "Invoice Type",
            "type":  "str",
        },
        
        # ========== FINANCIAL FIELDS ==========
        "net_amount": {
            "path":  "quotation__oa__order__invoices__net_amount",
            "label": "Net Amount",
            "type":  "decimal",
        },
        "tax_amount": {
            "path":  "quotation__oa__order__invoices__tax_amount",
            "label": "Tax Amount",
            "type":  "decimal",
        },
        "grand_total": {
            "path":  "quotation__oa__order__invoices__grand_total",
            "label": "Grand Total",
            "type":  "decimal",
        },
        "payment_due_date": {
            "path":  "quotation__oa__order__invoices__payment_due_date",
            "label": "Payment Due Date",
            "type":  "date",
        },
        
        # ========== TRANSPORT & LOGISTICS ==========
        "date_of_removal": {
            "path":  "quotation__oa__order__invoices__date_of_removal",
            "label": "Date of Removal",
            "type":  "date",
        },
        "time_of_removal": {
            "path":  "quotation__oa__order__invoices__time_of_removal",
            "label": "Time of Removal",
            "type":  "str",
        },
        "mode_of_transport": {
            "path":  "quotation__oa__order__invoices__mode_of_transport",
            "label": "Mode of Transport",
            "type":  "str",
        },
        "transporter": {
            "path":  "quotation__oa__order__invoices__transporter",
            "label": "Transporter",
            "type":  "str",
        },
        "vehicle_number": {
            "path":  "quotation__oa__order__invoices__vehicle_number",
            "label": "Vehicle Number",
            "type":  "str",
        },
        "lr_number": {
            "path":  "quotation__oa__order__invoices__lr_number",
            "label": "LR Number",
            "type":  "str",
        },
        
        # ========== CONTACT & GST ==========
        "contact_name": {
            "path":  "quotation__oa__order__invoices__contact_name",
            "label": "Contact Person",
            "type":  "str",
        },
        "contact_number": {
            "path":  "quotation__oa__order__invoices__contact_number",
            "label": "Contact Number",
            "type":  "str",
        },
        "contact_email": {
            "path":  "quotation__oa__order__invoices__contact_email",
            "label": "Contact Email",
            "type":  "str",
        },
        "consignee_gst": {
            "path":  "quotation__oa__order__invoices__consignee_gst",
            "label": "Consignee GST",
            "type":  "str",
        },
        "consignor_gst": {
            "path":  "quotation__oa__order__invoices__consignor_gst",
            "label": "Consignor GST",
            "type":  "str",
        },
        "state_code": {
            "path":  "quotation__oa__order__invoices__state_code",
            "label": "State Code",
            "type":  "str",
        },
        
        # ========== DELIVERY CHALLAN ==========
        "challan_number": {
            "path":  "quotation__oa__order__invoices__delivery_challan__challan_number",
            "label": "Delivery Challan No.",
            "type":  "str",
        },
        "challan_date": {
            "path":  "quotation__oa__order__invoices__delivery_challan__challan_date",
            "label": "Challan Date",
            "type":  "date",
        },
        "challan_remark": {
            "path":  "quotation__oa__order__invoices__delivery_challan__remark",
            "label": "Challan Remark",
            "type":  "str",
        },
        
        # ========== PACKAGING SLIP ==========
        "packing_list_number": {
            "path":  "quotation__oa__order__invoices__packaging_slip__packing_list_number",
            "label": "Packing List No.",
            "type":  "str",
        },
        "no_of_packages": {
            "path":  "quotation__oa__order__invoices__packaging_slip__no_of_packages",
            "label": "Number of Packages",
            "type":  "int",
        },
        "consignee_name": {
            "path":  "quotation__oa__order__invoices__packaging_slip__consignee_name",
            "label": "Consignee Name",
            "type":  "str",
        },
        "consignee_address": {
            "path":  "quotation__oa__order__invoices__packaging_slip__consignee_address",
            "label": "Consignee Address",
            "type":  "str",
        },
        
        # ========== LINE ITEM DETAILS (Dispatched quantities) ==========
        "dispatched_quantity": {
            "path":  "quotation__oa__order__back_orders__line_items__quantity_dispatching",
            "label": "Dispatched Quantity",
            "type":  "decimal",
        },
        "dispatched_unit_price": {
            "path":  "quotation__oa__order__back_orders__line_items__unit_price",
            "label": "Unit Price (Dispatched)",
            "type":  "decimal",
        },
        "dispatched_description": {
            "path":  "quotation__oa__order__back_orders__line_items__description",
            "label": "Item Description",
            "type":  "str",
        },
        "dispatched_part_no": {
            "path":  "quotation__oa__order__back_orders__line_items__part_no",
            "label": "Part No.",
            "type":  "str",
        },
        "dispatched_hsn_code": {
            "path":  "quotation__oa__order__back_orders__line_items__hsn_code",
            "label": "HSN Code",
            "type":  "str",
        },
        "dispatched_tax_percent": {
            "path":  "quotation__oa__order__back_orders__line_items__tax_percent",
            "label": "Tax %",
            "type":  "decimal",
        },
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper utilities used by views.py
# ─────────────────────────────────────────────────────────────────────────────

def get_field_def(module: str, field: str) -> dict | None:
    """Return field definition dict or None if not found."""
    return FIELD_REGISTRY.get(module, {}).get(field)


def get_all_paths_for_modules(modules: list[str]) -> list[str]:
    """Return all ORM paths for a list of module keys."""
    paths = []
    for mod in modules:
        for field_def in FIELD_REGISTRY.get(mod, {}).values():
            paths.append(field_def["path"])
    return paths


def registry_for_api() -> dict:
    """
    Serialise the full registry into a structure the frontend can consume:
    {
      modules: [{ key, label }],
      fields: {
        enquiry: [{ key, label, type, choices?, filterable, sortable }],
        ...
      }
    }
    """
    fields_out = {}
    for module, field_map in FIELD_REGISTRY.items():
        fields_out[module] = []
        for field_key, defn in field_map.items():
            fields_out[module].append({
                "key":        field_key,
                "label":      defn["label"],
                "type":       defn["type"],
                "choices":    defn.get("choices", []),
                "filterable": defn.get("filterable", True),
                "sortable":   defn.get("sortable", True),
            })

    return {
        "modules": MODULE_REGISTRY,
        "fields":  fields_out,
    }