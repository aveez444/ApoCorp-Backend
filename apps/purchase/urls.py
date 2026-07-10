# apps/purchase/urls.py

from rest_framework.routers import DefaultRouter
from .views import (
    PurchaseIndentViewSet, RFQViewSet,
    VendorQuotationViewSet, PurchaseOrderViewSet,
    GRNViewSet, VendorInvoiceViewSet,
)

router = DefaultRouter()
router.register(r'indents',          PurchaseIndentViewSet,   basename='indent')
router.register(r'rfqs',             RFQViewSet,              basename='rfq')
router.register(r'quotations',       VendorQuotationViewSet,  basename='vendor-quotation')
router.register(r'purchase-orders',  PurchaseOrderViewSet,    basename='purchase-order')
router.register(r'grns',              GRNViewSet,              basename='grn')
router.register(r'vendor-invoices',  VendorInvoiceViewSet,    basename='vendor-invoice')

urlpatterns = router.urls