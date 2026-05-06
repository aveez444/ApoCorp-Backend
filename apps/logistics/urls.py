# apps/logistics/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SalesInvoiceViewSet,
    PackagingSlipViewSet,
    DeliveryChallanViewSet,
    BackOrderViewSet
)

router = DefaultRouter()
router.register(r'invoices', SalesInvoiceViewSet, basename='invoices')
router.register(r'packaging-slips', PackagingSlipViewSet, basename='packaging-slips')
router.register(r'delivery-challans', DeliveryChallanViewSet, basename='delivery-challans')
router.register(r'back-orders', BackOrderViewSet, basename='back-orders')


urlpatterns = [
    path('', include(router.urls)),
]