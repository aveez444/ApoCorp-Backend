"""
Dashboard Views
───────────────
GET /api/dashboard/           → EmployeeDashboardView  (role=employee)
GET /api/dashboard/manager/   → ManagerDashboardView   (role=manager only)

All keys in the response match exactly what the frontend destructures.
"""

from datetime import date, timedelta
from collections import defaultdict

from django.db.models import Sum, Count, Avg, F, Q, ExpressionWrapper, fields as djfields
from django.contrib.auth.models import User
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.exceptions import PermissionDenied

from apps.accounts.models import TenantUser
from apps.enquiries.models import Enquiry
from apps.quotations.models import Quotation, QuotationFollowUp, QuotationLineItem
from apps.proforma.models import ProformaInvoice   # adjust path if needed


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def _days_remaining(d):
    if not d:
        return None
    return (d - date.today()).days


def _fmt_user(user):
    if not user:
        return "—"
    return user.get_full_name() or user.username


# ─────────────────────────────────────────────────────────────────────────────
# Employee Dashboard
# ─────────────────────────────────────────────────────────────────────────────

class EmployeeDashboardView(APIView):
    """
    GET /api/dashboard/
    Scoped to: request.tenant + request.user (assigned_to).

    Column mappings (Figma → backend):
      "Enquiry Name"    → customer.company_name  (Enquiry has no name field)
      "POC / Contact"   → CustomerPOC.is_primary=True
      "Quote Expiry"    → enquiry.due_date        (Quotation.expires_at is never populated)
      "Revenue"         → ProformaInvoice.total_amount (PAID/PARTIAL)
      "Pipeline Value"  → SUM(Enquiry.prospective_value) on open statuses
      "Product Revenue" → QuotationLineItem.product_name_snapshot grouped
      "Dom vs Export"   → customer.country == "India" → domestic, else export
      "MoM Sales"       → ProformaInvoice grouped by invoice_date month
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = request.tenant
        user   = request.user
        today  = date.today()

        tenant_user = TenantUser.objects.filter(user=user, tenant=tenant).first()

        # ── Base querysets ────────────────────────────────────
        enquiries = Enquiry.objects.filter(
            tenant=tenant, assigned_to=user, is_active=True
        ).select_related("customer")

        quotations = Quotation.objects.filter(
            tenant=tenant, enquiry__assigned_to=user, is_active=True
        ).select_related("enquiry__customer", "enquiry")

        proformas = ProformaInvoice.objects.filter(
            tenant=tenant,
            order__oa__quotation__enquiry__assigned_to=user,
        )

        # ── 1. Summary cards ──────────────────────────────────
        revenue = proformas.filter(
            status__in=["PAID", "PARTIAL"]
        ).aggregate(total=Sum("total_amount"))["total"] or 0

        target = 0
        achievement_percent = round(float(revenue) / float(target) * 100, 1) if target else 0

        pipeline_value = enquiries.filter(
            status__in=["NEW", "NEGOTIATION"]
        ).aggregate(total=Sum("prospective_value"))["total"] or 0

        coverage_ratio = round(float(pipeline_value) / float(target), 2) if target else 0

        # ── 2. Conversion ─────────────────────────────────────
        total_enqs = enquiries.count()
        enqs_with_qt = enquiries.filter(quotation__isnull=False).count()
        enquiry_to_quotation_pct = (
            round(enqs_with_qt / total_enqs * 100, 1) if total_enqs else 0
        )

        # ── 3. Recent enquiries ───────────────────────────────
        recent_enquiries = []
        for enq in enquiries.order_by("-created_at")[:20]:
            cust = enq.customer
            poc  = cust.pocs.filter(is_primary=True).first() if cust else None
            recent_enquiries.append({
                "id":               str(enq.id),
                "enquiry_number":   enq.enquiry_number,
                "customer_name":    cust.company_name if cust else "—",
                "city":             cust.city         if cust else "—",
                "poc_name":         poc.name          if poc else "—",
                "contact_number":   poc.phone         if poc else "—",
                "due_date":         enq.due_date.isoformat() if enq.due_date else None,
                "days_remaining":   _days_remaining(enq.due_date),
                "status":           enq.status,
                "priority":         enq.priority,
                "prospective_value": float(enq.prospective_value) if enq.prospective_value else 0,
                "currency":         enq.currency or "INR",
                "enquiry_type":     enq.enquiry_type or "—",
            })

        # ── 4. Expiring quotations ────────────────────────────
        # No expires_at on Quotation → use enquiry.due_date as proxy
        expiry_window = today + timedelta(days=14)
        expiring_quotations = []
        for q in quotations.filter(
            enquiry__due_date__isnull=False,
            enquiry__due_date__lte=expiry_window,
            enquiry__due_date__gte=today,
        ).order_by("enquiry__due_date")[:15]:
            enq  = q.enquiry
            cust = enq.customer if enq else None
            days = _days_remaining(enq.due_date) if enq else None
            expiring_quotations.append({
                "quotation_number": q.quotation_number,
                "customer_name":    cust.company_name if cust else "—",
                "grand_total":      float(q.grand_total),
                "currency":         q.currency or "INR",
                "days_remaining":   days,
                "risk_level":       "HIGH" if (days is not None and days <= 2) else "NORMAL",
                "review_status":    q.review_status,
                "client_status":    q.client_status,
                "due_date":         enq.due_date.isoformat() if enq and enq.due_date else None,
            })

        # ── 5. MoM monthly revenue ────────────────────────────
        monthly_map = defaultdict(float)
        for pf in proformas.filter(
            invoice_date__year=today.year,
            status__in=["PAID", "PARTIAL", "SENT"],
        ).values("invoice_date__month", "total_amount"):
            monthly_map[pf["invoice_date__month"] - 1] += float(pf["total_amount"] or 0)

        monthly_revenue = [
            {"month": MONTH_ABBR[i], "revenue": monthly_map.get(i, 0)}
            for i in range(12)
        ]

        # ── 6. Revenue by product ─────────────────────────────
        product_map = defaultdict(float)
        for li in QuotationLineItem.objects.filter(
            quotation__tenant=tenant,
            quotation__enquiry__assigned_to=user,
        ).values("product_name_snapshot", "line_total"):
            product_map[li["product_name_snapshot"] or "Other"] += float(li["line_total"] or 0)

        product_wise_revenue = sorted(
            [{"product": k, "revenue": v} for k, v in product_map.items()],
            key=lambda x: x["revenue"], reverse=True,
        )[:6]

        # ── 7. Domestic vs Export ─────────────────────────────
        domestic = export = 0.0
        for pf in proformas.filter(status__in=["PAID", "PARTIAL"]).select_related(
            "order__oa__quotation__enquiry__customer"
        ):
            try:
                country = (pf.order.oa.quotation.enquiry.customer.country or "").strip().lower()
                amt = float(pf.total_amount or 0)
                if country in ("india", "in"):
                    domestic += amt
                else:
                    export += amt
            except AttributeError:
                pass

        return Response({
            "role": tenant_user.role if tenant_user else "employee",
            "summary_cards": {
                "revenue":             float(revenue),
                "target":              float(target),
                "achievement_percent": achievement_percent,
                "pipeline_value":      float(pipeline_value),
                "coverage_ratio":      coverage_ratio,
            },
            "sales_funnel": {
                "conversion": {
                    "enquiry_to_quotation": enquiry_to_quotation_pct,
                }
            },
            "recent_enquiries":     recent_enquiries,
            "expiring_quotations":  expiring_quotations,
            "monthly_revenue":      monthly_revenue,
            "product_wise_revenue": product_wise_revenue,
            "domestic_vs_export":   {"domestic": domestic, "export": export},
        })


# ─────────────────────────────────────────────────────────────────────────────
# Manager Dashboard
# ─────────────────────────────────────────────────────────────────────────────

class ManagerDashboardView(APIView):
    """
    GET /api/dashboard/manager/
    Manager-only. Response keys match exactly what ManagerDashboard.jsx destructures:

        const {
            summary_cards, sales_pipeline, quotations_table,
            mom_sales_target, leaderboard, revenue_by_product_line,
            sales_funnel, sales_metrics, date_range
        } = data || {}

    summary_cards keys:
        achieved_target  → { achieved, target, change_pct }
        at_risk_enquiries → { count, change_pct }
        stalled_deals     → { count, change_pct }
        pending_followups → int

    quotations_table row keys:
        number, customer, city, sales_rep, due_date,
        prospective_value, coverage

    mom_sales_target keys (for MoMSalesTargetChart):
        { months: [...], year: int, sales_reps: { name: { revenue: [], target: [] } } }

    leaderboard row keys:
        { username, achieved, target, achievement_pct }

    sales_funnel keys:
        { enquiry, quotation, orders, dispatch, invoicing,
          percentages: { quotation_pct, orders_pct, dispatch_pct, invoicing_pct } }

    sales_metrics keys:
        { sales_cycle_days, win_rate, repeat_customer_ratio,
          cycle_change_pct, win_change_pct }
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = request.tenant
        user   = request.user
        today  = date.today()

        # ── Auth guard ────────────────────────────────────────
        tenant_user = TenantUser.objects.filter(user=user, tenant=tenant).first()
        if not tenant_user or (tenant_user.role != "manager" and not user.is_superuser):
            raise PermissionDenied("Only managers can access this dashboard.")

        # ── date_range (current month) ────────────────────────
        # Use %-d on Linux, %#d on Windows — avoid both by formatting manually
        month_start = today.replace(day=1)
        date_range = {
            "start": f"{month_start.day} {month_start.strftime('%b')}",
            "end":   f"{today.day} {today.strftime('%b %Y')}",
        }

        # ── Base querysets (all employees, this tenant) ───────
        all_enquiries = Enquiry.objects.filter(
            tenant=tenant, is_active=True
        ).select_related("customer", "assigned_to")

        all_quotations = Quotation.objects.filter(
            tenant=tenant, is_active=True
        ).select_related("enquiry__customer", "enquiry__assigned_to")

        all_proformas = ProformaInvoice.objects.filter(tenant=tenant)

        # ── Helper: last-week comparison for change_pct ───────
        last_week = today - timedelta(days=7)

        # ── 1. SUMMARY CARDS ─────────────────────────────────

        # Achieved (PAID + PARTIAL proformas)
        achieved = all_proformas.filter(
            status__in=["PAID", "PARTIAL"]
        ).aggregate(t=Sum("total_amount"))["t"] or 0

        # Change pct vs same period last week (rough proxy)
        achieved_prev = all_proformas.filter(
            status__in=["PAID", "PARTIAL"],
            created_at__date__lt=last_week,
        ).aggregate(t=Sum("total_amount"))["t"] or 0
        achieved_change = round(
            (float(achieved) - float(achieved_prev)) / float(achieved_prev) * 100, 1
        ) if achieved_prev else 0

        team_target = 0  # extend with a Target model when available

        # At-risk: NEW enquiries with due_date within 7 days (has deadline, no progress)
        at_risk_now  = all_enquiries.filter(
            status="NEW",
            due_date__isnull=False,
            due_date__lte=today + timedelta(days=7),
            due_date__gte=today,
        ).count()
        at_risk_prev = all_enquiries.filter(
            status="NEW",
            due_date__isnull=False,
            due_date__lte=last_week + timedelta(days=7),
            due_date__gte=last_week,
        ).count()
        at_risk_change = round(
            (at_risk_now - at_risk_prev) / at_risk_prev * 100, 1
        ) if at_risk_prev else 0

        # Stalled: open enquiries with no activity for 14+ days
        stall_threshold = today - timedelta(days=14)
        stalled_now = all_enquiries.filter(
            status__in=["NEW", "NEGOTIATION"],
        ).filter(
            Q(last_activity_at__isnull=True) |
            Q(last_activity_at__date__lte=stall_threshold)
        ).count()
        stall_prev_threshold = last_week - timedelta(days=14)
        stalled_prev = all_enquiries.filter(
            status__in=["NEW", "NEGOTIATION"],
        ).filter(
            Q(last_activity_at__isnull=True) |
            Q(last_activity_at__date__lte=stall_prev_threshold)
        ).count()
        stalled_change = round(
            (stalled_now - stalled_prev) / stalled_prev * 100, 1
        ) if stalled_prev else 0

        # Pending follow-ups: follow_up_date <= today
        pending_followups = QuotationFollowUp.objects.filter(
            quotation__tenant=tenant,
            follow_up_date__lte=today,
        ).count()

        summary_cards = {
            "achieved_target": {
                "achieved":   float(achieved),
                "target":     float(team_target),
                "change_pct": achieved_change,
            },
            "at_risk_enquiries": {
                "count":      at_risk_now,
                "change_pct": at_risk_change,
            },
            "stalled_deals": {
                "count":      stalled_now,
                "change_pct": stalled_change,
            },
            "pending_followups": pending_followups,
        }

        # ── 2. SALES PIPELINE ────────────────────────────────
        total_pipeline = all_enquiries.filter(
            status__in=["NEW", "NEGOTIATION"],
            prospective_value__isnull=False,
        ).aggregate(t=Sum("prospective_value"))["t"] or 0

        coverage_ratio = round(
            float(total_pipeline) / float(achieved), 2
        ) if achieved else 0

        sales_pipeline = {
            "total_pipeline_value": float(total_pipeline),
            "coverage_ratio":       coverage_ratio,
        }

        # ── 3. QUOTATIONS TABLE ───────────────────────────────
        # Figma columns: Quotation#, Customer+city, Sales Rep, Due Date,
        #                Prospective Value, Coverage (pipeline/revenue per rep)
        quotations_table = []
        for q in all_quotations.order_by("-created_at")[:15]:
            enq  = q.enquiry
            cust = enq.customer if enq else None
            rep  = enq.assigned_to if enq else None

            # Per-rep coverage: rep's pipeline / rep's revenue
            rep_pipeline = 0.0
            rep_revenue  = 0.0
            if rep:
                rep_pipeline = float(
                    all_enquiries.filter(
                        assigned_to=rep, status__in=["NEW", "NEGOTIATION"],
                        prospective_value__isnull=False,
                    ).aggregate(t=Sum("prospective_value"))["t"] or 0
                )
                rep_revenue = float(
                    all_proformas.filter(
                        status__in=["PAID", "PARTIAL"],
                        order__oa__quotation__enquiry__assigned_to=rep,
                    ).aggregate(t=Sum("total_amount"))["t"] or 0
                )
            rep_coverage = round(rep_pipeline / rep_revenue * 100, 1) if rep_revenue else 0

            quotations_table.append({
                "id":               str(q.id),
                "number":           q.quotation_number,
                "customer":         cust.company_name if cust else "—",
                "city":             cust.city         if cust else "—",
                "country":          cust.country      if cust else "—",
                "sales_rep":        _fmt_user(rep),
                # due_date → enquiry.due_date (no deadline field on Quotation itself)
                "due_date":         enq.due_date.isoformat() if enq and enq.due_date else None,
                "days_remaining":   _days_remaining(enq.due_date) if enq else None,
                # prospective_value from Enquiry (Quotation stores grand_total separately)
                "prospective_value": float(enq.prospective_value) if enq and enq.prospective_value else 0,
                "currency":          enq.currency if enq else "INR",
                "grand_total":       float(q.grand_total),
                "review_status":     q.review_status,
                "client_status":     q.client_status,
                # coverage = this rep's pipeline / revenue %
                "coverage":          rep_coverage,
            })

        # ── 4. MOM SALES TARGET ───────────────────────────────
        # Shape: { months: [...12], year: int,
        #          sales_reps: { "Name": { revenue: [...12], target: [...12] } } }
        current_year = today.year

        mom_raw = (
            all_proformas.filter(
                invoice_date__year=current_year,
                status__in=["PAID", "PARTIAL", "SENT"],
                order__oa__quotation__enquiry__assigned_to__isnull=False,
            )
            .values(
                "invoice_date__month",
                "order__oa__quotation__enquiry__assigned_to__id",
                "order__oa__quotation__enquiry__assigned_to__first_name",
                "order__oa__quotation__enquiry__assigned_to__last_name",
                "order__oa__quotation__enquiry__assigned_to__username",
            )
            .annotate(monthly_total=Sum("total_amount"))
        )

        # Build { user_id: { "name": str, "monthly": {idx: float} } }
        emp_data = {}
        for row in mom_raw:
            uid  = row["order__oa__quotation__enquiry__assigned_to__id"]
            fn   = row["order__oa__quotation__enquiry__assigned_to__first_name"] or ""
            ln   = row["order__oa__quotation__enquiry__assigned_to__last_name"]  or ""
            un   = row["order__oa__quotation__enquiry__assigned_to__username"]   or ""
            name = f"{fn} {ln}".strip() or un
            midx = (row["invoice_date__month"] or 1) - 1

            if uid not in emp_data:
                emp_data[uid] = {"name": name, "monthly": defaultdict(float)}
            emp_data[uid]["monthly"][midx] += float(row["monthly_total"] or 0)

        # Convert to { name: { revenue: [...12], target: [...12] } }
        sales_reps_mom = {}
        for uid, info in emp_data.items():
            sales_reps_mom[info["name"]] = {
                "revenue": [info["monthly"].get(i, 0) for i in range(12)],
                "target":  [0] * 12,  # no target model yet — all zeros
            }

        mom_sales_target = {
            "year":       current_year,
            "months":     MONTH_ABBR,
            "sales_reps": sales_reps_mom,
        }

        # ── 5. LEADERBOARD ────────────────────────────────────
        # Shape: [{ username, achieved, target, achievement_pct }]
        emp_tenant_users = TenantUser.objects.filter(
            tenant=tenant, role="employee", is_active=True
        ).select_related("user")

        leaderboard = []
        for tu in emp_tenant_users:
            emp_rev = float(
                all_proformas.filter(
                    status__in=["PAID", "PARTIAL"],
                    order__oa__quotation__enquiry__assigned_to=tu.user,
                ).aggregate(t=Sum("total_amount"))["t"] or 0
            )
            emp_target = 0  # extend with Target model
            achievement_pct = round(emp_rev / emp_target * 100, 1) if emp_target else 0

            leaderboard.append({
                "username":        _fmt_user(tu.user),
                "achieved":        emp_rev,
                "target":          emp_target,
                "achievement_pct": achievement_pct,
            })

        leaderboard.sort(key=lambda x: x["achieved"], reverse=True)

        # ── 6. REVENUE BY PRODUCT LINE ────────────────────────
        product_map = defaultdict(float)
        for li in QuotationLineItem.objects.filter(
            quotation__tenant=tenant
        ).values("product_name_snapshot", "line_total"):
            product_map[li["product_name_snapshot"] or "Other"] += float(li["line_total"] or 0)

        revenue_by_product_line = sorted(
            [{"product": k, "revenue": v} for k, v in product_map.items()],
            key=lambda x: x["revenue"], reverse=True,
        )[:6]

        # ── 7. SALES FUNNEL ───────────────────────────────────
        # Stages: Enquiry → Quotation → Orders → Dispatch → Invoicing
        # No OA/Dispatch model yet → use client_status=ACCEPTED as proxy for Orders
        # and distinct order count via proforma chain for Dispatch
        enq_count  = all_enquiries.count()
        qt_count   = all_quotations.count()
        orders_cnt = all_quotations.filter(client_status="ACCEPTED").count()
        dispatch   = all_proformas.values("order").distinct().count()
        invoicing  = all_proformas.count()

        def _pct(num, denom):
            return round(num / denom * 100, 1) if denom else 0

        sales_funnel = {
            "enquiry":   enq_count,
            "quotation": qt_count,
            "orders":    orders_cnt,
            "dispatch":  dispatch,
            "invoicing": invoicing,
            "percentages": {
                "quotation_pct": _pct(qt_count,   enq_count),
                "orders_pct":    _pct(orders_cnt,  qt_count),
                "dispatch_pct":  _pct(dispatch,    orders_cnt),
                "invoicing_pct": _pct(invoicing,   dispatch),
            },
        }

        # ── 8. SALES METRICS ─────────────────────────────────
        # Sales Cycle: avg days from Enquiry.created_at → updated_at for PO_RECEIVED
        avg_cycle_days = 0
        closed_enqs = all_enquiries.filter(status="PO_RECEIVED")
        if closed_enqs.exists():
            dur_expr = ExpressionWrapper(
                F("updated_at") - F("created_at"),
                output_field=djfields.DurationField()
            )
            result = closed_enqs.annotate(dur=dur_expr).aggregate(avg=Avg("dur"))["avg"]
            if result:
                avg_cycle_days = round(result.days, 0)

        # Win Rate: PO_RECEIVED / (PO_RECEIVED + LOST + REGRET)
        won  = all_enquiries.filter(status="PO_RECEIVED").count()
        lost = all_enquiries.filter(status__in=["LOST", "REGRET"]).count()
        win_rate = round(won / (won + lost) * 100, 1) if (won + lost) else 0

        # Repeat Customer Ratio
        cust_counts    = all_enquiries.values("customer").annotate(n=Count("id"))
        total_custs    = cust_counts.count()
        repeat_custs   = cust_counts.filter(n__gte=2).count()
        repeat_ratio   = round(repeat_custs / total_custs * 100, 1) if total_custs else 0

        sales_metrics = {
            "sales_cycle_days":       int(avg_cycle_days),
            "win_rate":               win_rate,
            "repeat_customer_ratio":  repeat_ratio,
            "cycle_change_pct":       0,   # extend with historical comparison
            "win_change_pct":         0,
        }

        # ── Final response ────────────────────────────────────
        return Response({
            "role":                   "manager",
            "date_range":             date_range,
            "summary_cards":          summary_cards,
            "sales_pipeline":         sales_pipeline,
            "quotations_table":       quotations_table,
            "mom_sales_target":       mom_sales_target,
            "leaderboard":            leaderboard,
            "revenue_by_product_line": revenue_by_product_line,
            "sales_funnel":           sales_funnel,
            "sales_metrics":          sales_metrics,
        })