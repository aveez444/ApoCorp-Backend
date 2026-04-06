# apps/reports/models.py

import uuid
from django.db import models
from django.contrib.auth.models import User
from core.mixins import TenantModelMixin


class SavedReport(TenantModelMixin):
    """
    Stores a user-defined report configuration.

    config schema (stored as JSON):
    {
        "modules":  ["enquiry", "customer", "quotation"],   # which tables to join
        "columns":  [                                        # ordered list
            { "module": "customer", "field": "company_name", "label": "Company" },
            { "module": "enquiry",  "field": "status",       "label": "Status"  },
        ],
        "filters":  [                                        # AND-ed together
            { "module": "enquiry",  "field": "status",
              "operator": "in",     "value": ["NEW", "NEGOTIATION"] },
            { "module": "enquiry",  "field": "created_at",
              "operator": "gte",    "value": "2025-01-01" },
        ],
        "order_by": "-enquiry__created_at"                  # ORM order_by string
    }

    Supported filter operators:
        eq   – exact match
        neq  – exclude exact
        in   – value is a list
        gte  – greater-than-or-equal  (dates / decimals)
        lte  – less-than-or-equal
        contains – case-insensitive substring
        isnull   – value is true/false
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    name        = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    # The full report definition — modules, columns, filters, ordering
    config = models.JSONField(default=dict)

    # Visibility
    is_shared = models.BooleanField(
        default=True,
        help_text="If True, all tenant users can view and run this report."
    )

    created_by  = models.ForeignKey(
        User,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="created_reports",
    )
    created_at  = models.DateTimeField(auto_now_add=True)
    updated_at  = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name