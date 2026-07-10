from django.urls import path
from rest_framework.routers import DefaultRouter
from .views import (
    ItemMasterViewSet, WarehouseViewSet, StorageLocationViewSet,
    StockViewSet, StockLedgerViewSet,
    MaterialIssueSlipViewSet, BarcodeViewSet,
    StockReservationViewSet, StockAlertView,
)

router = DefaultRouter()
router.register(r'items',        ItemMasterViewSet,        basename='item')
router.register(r'warehouses',   WarehouseViewSet,         basename='warehouse')
router.register(r'bins',         StorageLocationViewSet,   basename='bin')
router.register(r'stock',        StockViewSet,             basename='stock')
router.register(r'stock-ledger', StockLedgerViewSet,       basename='stock-ledger')
router.register(r'issue-slips',  MaterialIssueSlipViewSet, basename='issue-slip')
router.register(r'labels',       BarcodeViewSet,           basename='label')
router.register(r'reservations', StockReservationViewSet,  basename='reservation')

urlpatterns = router.urls + [
    path('stock-alerts/', StockAlertView.as_view(), name='stock-alerts'),
    path('barcode/scan/', BarcodeViewSet.as_view({'get': 'scan'}), name='barcode-scan'),
]