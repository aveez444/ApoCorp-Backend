# apps/qc/urls.py

from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    InspectionPlanViewSet,
    QCInspectionOrderViewSet,
    NCRViewSet,
    QCAnalyticsView,
)

router = DefaultRouter()
router.register(r'inspection-plans',  InspectionPlanViewSet,      basename='inspection-plan')
router.register(r'inspection-orders', QCInspectionOrderViewSet,   basename='inspection-order')
router.register(r'ncr',               NCRViewSet,                  basename='ncr')

urlpatterns = router.urls + [
    path('analytics/', QCAnalyticsView.as_view(), name='qc-analytics'),
]