# apps/logistics/urls.py

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    SalesInvoiceViewSet,
    PackagingSlipViewSet,
    DeliveryChallanViewSet,
    BackOrderViewSet,
    InvoicePDFView,
)
from .einvoice_views import (
    EInvoiceGenerateView,
    EInvoiceCancelView,
    EInvoicePDFView,
)

router = DefaultRouter()
router.register(r'invoices',          SalesInvoiceViewSet,    basename='invoices')
router.register(r'packaging-slips',   PackagingSlipViewSet,   basename='packaging-slips')
router.register(r'delivery-challans', DeliveryChallanViewSet, basename='delivery-challans')
router.register(r'back-orders',       BackOrderViewSet,       basename='back-orders')

urlpatterns = [
    path('', include(router.urls)),

    # ── Regular invoice PDF (existing, unchanged) ─────────────────────────────
    path(
        'invoices/<uuid:pk>/pdf/',
        InvoicePDFView.as_view(),
        name='invoice-pdf',
    ),

    # ── E-Invoice: generate IRN via GSP / IRP ─────────────────────────────────
    # POST  → submits to IRP, stores IRN + QR, returns EInvoiceRecord JSON
    path(
        'invoices/<uuid:pk>/einvoice/generate/',
        EInvoiceGenerateView.as_view(),
        name='einvoice-generate',
    ),

    # ── E-Invoice: cancel IRN (within 24 h of acknowledgement) ───────────────
    # POST  body: {"reason": "1", "remarks": "..."}
    path(
        'invoices/<uuid:pk>/einvoice/cancel/',
        EInvoiceCancelView.as_view(),
        name='einvoice-cancel',
    ),

    # ── E-Invoice PDF: invoice with IRN + QR code printed ────────────────────
    # GET   → PDF; ?download=true for attachment
    # Requires the invoice to already have an ACTIVE EInvoiceRecord.
    path(
        'invoices/<uuid:pk>/einvoice/pdf/',
        EInvoicePDFView.as_view(),
        name='einvoice-pdf',
    ),
]