"""
Logistics Dashboard View
────────────────────────
GET /api/logistics/dashboard/ → Unified logistics dashboard

All keys in the response match exactly what the frontend destructures.
"""

from datetime import date, timedelta
from collections import defaultdict
from decimal import Decimal

from django.db.models import Sum, Count, Q, Avg, F, Value, IntegerField
from django.db.models.functions import Coalesce, ExtractDay, TruncDate
from rest_framework.views import APIView
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.accounts.models import TenantUser
from apps.logistics.models import BackOrder, BackOrderLineItem, SalesInvoice
from apps.orders.models import Order, OALineItem


MONTH_ABBR = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

WEEKDAY_ABBR = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def _fmt_user(user):
    if not user:
        return "—"
    return user.get_full_name() or user.username


def _days_remaining(target_date):
    if not target_date:
        return None
    return (target_date - date.today()).days


def _days_overdue(target_date):
    if not target_date:
        return None
    days = (date.today() - target_date).days
    return max(0, days)


def _get_status_label(status_code):
    """Convert status code to user-friendly label"""
    status_map = {
        'PENDING': 'Pending',
        'INVOICED': 'Invoiced',
        'IN_TRANSIT': 'In Transit',
        'OUT_FOR_DELIVERY': 'Out for Delivery',
        'DELIVERED': 'Delivered',
        'DELAYED': 'Delayed',
        'RETURNED': 'Returned',
        'CANCELLED': 'Cancelled',
        'COMPLETED': 'Completed',
    }
    return status_map.get(status_code, status_code)


def _get_invoice_field(backorder, field_name, default="—"):
    """Safely get a field from a BackOrder's invoice if it exists"""
    try:
        if backorder.invoice:
            return getattr(backorder.invoice, field_name, default)
    except BackOrder.invoice.RelatedObjectDoesNotExist:
        pass
    return default


class LogisticsDashboardView(APIView):
    """
    GET /api/logistics/dashboard/
    
    Unified dashboard for logistics - same view for employees and managers.
    Employees see only backorders linked to their assigned enquiries.
    Managers see all backorders in the tenant.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        tenant = request.tenant
        user = request.user
        today = date.today()

        # ── Determine user role and scope ────────────────────────────────────
        tenant_user = TenantUser.objects.filter(user=user, tenant=tenant).first()
        role = tenant_user.role if tenant_user else "employee"
        
        # Base queryset for BackOrders (with role-based filtering)
        backorders = BackOrder.objects.filter(tenant=tenant)
        
        if role == "employee":
            # Employee: only backorders for orders where they are assigned
            backorders = backorders.filter(
                order__oa__quotation__enquiry__assigned_to=user
            )
        
        # Prefetch related data for efficiency
        backorders = backorders.select_related(
            'order',
            'order__oa__quotation__enquiry__customer',
            'invoice'  # This will cause RelatedObjectDoesNotExist if no invoice, but we handle it safely
        ).prefetch_related('line_items')
        
        # ── 1. SUMMARY CARDS ────────────────────────────────────────────────
        
        # Pending Dispatch
        pending_count = backorders.filter(status='PENDING').count()
        
        # In-Transit (IN_TRANSIT + OUT_FOR_DELIVERY)
        in_transit_count = backorders.filter(
            status__in=['IN_TRANSIT', 'OUT_FOR_DELIVERY']
        ).count()
        
        # Delayed shipments
        delayed_count = backorders.filter(status='DELAYED').count()
        
        # Completed this month
        month_start = today.replace(day=1)
        completed_this_month = backorders.filter(
            status__in=['DELIVERED', 'COMPLETED'],
            updated_at__date__gte=month_start
        ).count()
        
        # On-Time Delivery Rate (using filter on fields that exist without relation)
        delivered_backorders = backorders.filter(
            status__in=['DELIVERED', 'COMPLETED']
        )
        on_time_count = 0
        for bo in delivered_backorders:
            if bo.expected_dispatch_date and bo.updated_at:
                if bo.updated_at.date() <= bo.expected_dispatch_date:
                    on_time_count += 1
        
        total_delivered = delivered_backorders.count()
        on_time_rate = round(on_time_count / total_delivered * 100, 1) if total_delivered else 0
        
        summary_cards = {
            "pending_dispatch": pending_count,
            "in_transit": in_transit_count,
            "delayed_shipments": delayed_count,
            "completed_this_month": completed_this_month,
            "on_time_delivery_rate": on_time_rate,
        }
        
        # ── 2. STATUS DISTRIBUTION (Donut Chart) ────────────────────────────
        status_counts = {}
        for status_code, _ in BackOrder.STATUS_CHOICES:
            count = backorders.filter(status=status_code).count()
            if count > 0:
                status_counts[status_code] = count
        
        status_distribution = [
            {"status": _get_status_label(status), "count": count}
            for status, count in status_counts.items()
        ]
        
        # ── 3. WEEKLY DISPATCH TREND (Last 7 days) ──────────────────────────
        last_7_days = [today - timedelta(days=i) for i in range(6, -1, -1)]
        last_7_days_set = set(last_7_days)
        
        # Get invoice dates for dispatched backorders (only those with invoices)
        dispatched_backorders = []
        for bo in backorders.filter(
            status__in=['INVOICED', 'IN_TRANSIT', 'OUT_FOR_DELIVERY', 'DELIVERED', 'COMPLETED']
        ):
            try:
                if bo.invoice and bo.invoice.invoice_date:
                    dispatched_backorders.append(bo)
            except BackOrder.invoice.RelatedObjectDoesNotExist:
                pass
        
        # Aggregate by invoice date
        daily_dispatch = defaultdict(float)
        for bo in dispatched_backorders:
            try:
                if bo.invoice and bo.invoice.invoice_date:
                    inv_date = bo.invoice.invoice_date
                    if inv_date in last_7_days_set:
                        qty = sum(float(item.quantity_dispatching) for item in bo.line_items.all())
                        daily_dispatch[inv_date] += qty
            except BackOrder.invoice.RelatedObjectDoesNotExist:
                pass
        
        weekly_dispatch_trend = []
        for day in last_7_days:
            weekday_name = WEEKDAY_ABBR[day.weekday()]
            weekly_dispatch_trend.append({
                "date": day.isoformat(),
                "day": weekday_name,
                "quantity": daily_dispatch.get(day, 0)
            })
        
        # ── 4. RECENT BACKORDERS TABLE ──────────────────────────────────────
        recent_backorders = []
        for bo in backorders.order_by('-created_at')[:15]:
            order = bo.order
            customer = None
            if order and order.oa and order.oa.quotation and order.oa.quotation.enquiry:
                customer = order.oa.quotation.enquiry.customer
            
            expected_date = bo.expected_dispatch_date
            days_status = None
            if expected_date:
                if bo.status in ['DELIVERED', 'COMPLETED']:
                    actual_date = bo.updated_at.date() if bo.updated_at else None
                    if actual_date and actual_date > expected_date:
                        days_status = f"{_days_overdue(expected_date)} days late"
                    else:
                        days_status = "On time"
                elif bo.status in ['CANCELLED']:
                    days_status = "Cancelled"
                else:
                    overdue = _days_overdue(expected_date)
                    if overdue > 0:
                        days_status = f"{overdue} days overdue"
                    else:
                        days_rem = _days_remaining(expected_date)
                        days_status = f"Due in {days_rem} days" if days_rem else "Due today"
            
            # Safely get transporter from invoice
            transporter = _get_invoice_field(bo, 'transporter', "—")
            
            recent_backorders.append({
                "id": str(bo.id),
                "back_order_number": bo.back_order_number,
                "order_number": order.order_number if order else "—",
                "customer_name": customer.company_name if customer else "—",
                "status": bo.status,
                "status_label": _get_status_label(bo.status),
                "expected_dispatch_date": expected_date.isoformat() if expected_date else None,
                "days_status": days_status,
                "transporter": transporter,
                "created_at": bo.created_at.isoformat(),
            })
        
        # ── 5. CATEGORY BREAKDOWN ──────────────────────────────────────────
        domestic_count = backorders.filter(order__order_category='DOMESTIC').count()
        international_count = backorders.filter(order__order_category='INTERNATIONAL').count()
        
        # Sum quantities by category
        domestic_qty = 0
        international_qty = 0
        for bo in backorders:
            qty = sum(float(item.quantity_dispatching) for item in bo.line_items.all())
            if bo.order and bo.order.order_category == 'DOMESTIC':
                domestic_qty += qty
            else:
                international_qty += qty
        
        category_breakdown = {
            "domestic": {
                "count": domestic_count,
                "quantity": round(domestic_qty, 2)
            },
            "international": {
                "count": international_count,
                "quantity": round(international_qty, 2)
            }
        }
        
        # ── 6. OVERDUE ALERTS ───────────────────────────────────────────────
        overdue_backorders = backorders.filter(
            expected_dispatch_date__isnull=False,
            expected_dispatch_date__lt=today
        ).exclude(
            status__in=['DELIVERED', 'COMPLETED', 'CANCELLED']
        ).order_by('expected_dispatch_date')[:20]
        
        overdue_alerts = []
        for bo in overdue_backorders:
            order = bo.order
            customer = None
            if order and order.oa and order.oa.quotation and order.oa.quotation.enquiry:
                customer = order.oa.quotation.enquiry.customer
            
            overdue_days = _days_overdue(bo.expected_dispatch_date)
            
            overdue_alerts.append({
                "id": str(bo.id),
                "back_order_number": bo.back_order_number,
                "order_number": order.order_number if order else "—",
                "customer_name": customer.company_name if customer else "—",
                "expected_dispatch_date": bo.expected_dispatch_date.isoformat(),
                "overdue_days": overdue_days,
                "status": bo.status,
                "status_label": _get_status_label(bo.status),
                "reason": bo.reason or "—",
            })
        
        # ── 7. TOP TRANSPORTERS ────────────────────────────────────────────
        transporter_data = defaultdict(lambda: {
            "total": 0,
            "on_time": 0,
            "delivered": 0
        })
        
        # Iterate through delivered backorders and safely check for invoice
        for bo in backorders.filter(status__in=['DELIVERED', 'COMPLETED']):
            try:
                if not bo.invoice or not bo.invoice.transporter:
                    continue
                
                transporter = bo.invoice.transporter
                if not transporter:
                    continue
                
                transporter_data[transporter]["total"] += 1
                transporter_data[transporter]["delivered"] += 1
                
                # Check if on time
                if (bo.expected_dispatch_date and 
                    bo.updated_at and 
                    bo.updated_at.date() <= bo.expected_dispatch_date):
                    transporter_data[transporter]["on_time"] += 1
            except BackOrder.invoice.RelatedObjectDoesNotExist:
                continue
        
        top_transporters = []
        for name, data in transporter_data.items():
            on_time_pct = round(data["on_time"] / data["total"] * 100, 1) if data["total"] else 0
            top_transporters.append({
                "name": name,
                "total_shipments": data["total"],
                "on_time_deliveries": data["on_time"],
                "on_time_percentage": on_time_pct,
            })
        
        top_transporters.sort(key=lambda x: x["total_shipments"], reverse=True)
        top_transporters = top_transporters[:5]
        
        # ── Final response ──────────────────────────────────────────────────
        return Response({
            "role": role,
            "summary_cards": summary_cards,
            "status_distribution": status_distribution,
            "weekly_dispatch_trend": weekly_dispatch_trend,
            "recent_backorders": recent_backorders,
            "category_breakdown": category_breakdown,
            "overdue_alerts": overdue_alerts,
            "top_transporters": top_transporters,
        })