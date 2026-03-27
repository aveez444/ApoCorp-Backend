from django.urls import path
from .views import EmployeeDashboardView, ManagerDashboardView

urlpatterns = [
    # Main urls.py already has: path('api/dashboard/', include('apps.dashboard.urls'))
    # So these resolve to:
    #   /api/dashboard/          → EmployeeDashboardView
    #   /api/dashboard/manager/  → ManagerDashboardView
    path('',          EmployeeDashboardView.as_view(), name='employee-dashboard'),
    path('manager/',  ManagerDashboardView.as_view(),  name='manager-dashboard'),
]

# ── Notes ─────────────────────────────────────────────────────────────────────
# 1. Proforma import — confirm your app name:
#       from apps.proforma.models import ProformaInvoice
#
# 2. When you add a real OA model, replace the orders_cnt proxy:
#       from apps.oa.models import OA
#       orders_cnt = OA.objects.filter(tenant=tenant).count()
#
# 3. When you add a Target model, replace all `team_target = 0` lines with
#    a DB query and pass real targets per employee to leaderboard[*]["target"]
#    and mom_sales_target.sales_reps[name]["target"]