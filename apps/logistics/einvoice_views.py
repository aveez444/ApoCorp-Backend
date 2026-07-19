# apps/logistics/einvoice_views.py
#
# Three new API views, all wired into apps/logistics/urls.py:
#
#   POST   /api/logistics/invoices/{id}/einvoice/generate/
#       → Submits invoice to IRP via GSP, stores IRN + QR, returns the record.
#
#   POST   /api/logistics/invoices/{id}/einvoice/cancel/
#       → Cancels the IRN within the 24-hour window.
#
#   GET    /api/logistics/invoices/{id}/einvoice/pdf/
#       → Renders invoice.html (e-invoice variant) with QR + IRN printed,
#         then runs through the same pdf_engine pipeline as the regular invoice PDF.
#
# The regular InvoicePDFView (existing) is unchanged; this is an additive layer.
#
# ── CHANGED from the previous draft ────────────────────────────────────────
#  1. invoice_currency / amount_in_words no longer read invoice.currency
#     (SalesInvoice has no such field — it lives on Order) — now read
#     invoice.order.currency, same as the regular InvoicePDFView does.
#  2. The CGST/SGST vs IGST split for the PDF now resolves invoice.state_code
#     through the shared resolve_state_code() (apps.logistics.state_codes)
#     before comparing it to the resolved company state code, instead of
#     comparing a possibly-unresolved raw value against a resolved one.

from __future__ import annotations

import logging

from django.http import Http404, StreamingHttpResponse
from django.template.loader import render_to_string
from django.utils import timezone
from decimal import Decimal

from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework import status

from .gsp_client import GSPClient, GSPError
from .einvoice_models import EInvoiceRecord
from .state_codes import resolve_state_code
from apps.documents.models import TenantLetterhead
from apps.documents.pdf_engine import generate_quotation_pdf, split_gst, amount_in_words

logger = logging.getLogger(__name__)


# ── helper: fetch invoice with all needed relations ─────────────────────────

def _get_invoice(pk, tenant):
    """
    Return a SalesInvoice with all related data pre-fetched.
    Raises Http404 if not found for this tenant.
    """
    from .models import SalesInvoice
    try:
        return (
            SalesInvoice.objects
            .select_related(
                'order',
                'order__oa',
                'order__oa__quotation',
                'back_order',
                'tenant',
                'tenant__letterhead',
            )
            .prefetch_related(
                'line_items',
            )
            .get(pk=pk, tenant=tenant)
        )
    except SalesInvoice.DoesNotExist:
        raise Http404


# ─────────────────────────────────────────────────────────────────────────────
# Generate IRN
# ─────────────────────────────────────────────────────────────────────────────

class EInvoiceGenerateView(APIView):
    """
    POST /api/logistics/invoices/{id}/einvoice/generate/

    Submits the SalesInvoice to the IRP via the tenant's GSP and stores the
    returned IRN + signed QR code in an EInvoiceRecord.

    Idempotent: if an ACTIVE EInvoiceRecord already exists, it is returned
    as-is without re-submitting.

    Response (200 OK):
    {
        "id": 12,
        "invoice_id": "...",
        "invoice_number": "INV-2024-001",
        "status": "ACTIVE",
        "irn": "64-char-hash",
        "ack_no": "112400000...",
        "ack_date": "2024-01-01T06:30:00Z",
        "signed_qr_data": "eyJhb...",   ← render this as a QR code
        "created_at": "..."
    }
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        invoice = _get_invoice(pk, request.tenant)

        # ── 1. Idempotency — return existing active IRN ───────────────────
        existing = EInvoiceRecord.objects.filter(
            invoice=invoice,
            status=EInvoiceRecord.STATUS_ACTIVE,
        ).first()
        if existing:
            return Response(_serialize_einvoice(existing))

        # ── 2. Create a PENDING record (prevents duplicate submissions) ───
        record, _ = EInvoiceRecord.objects.get_or_create(
            invoice=invoice,
            tenant=request.tenant,
            defaults={'status': EInvoiceRecord.STATUS_PENDING},
        )

        if record.status == EInvoiceRecord.STATUS_ACTIVE:
            return Response(_serialize_einvoice(record))

        # ── 3. Call GSP ───────────────────────────────────────────────────
        try:
            client  = GSPClient.for_tenant(request.tenant)
            irn_data = client.generate_irn(invoice)
        except GSPError as exc:
            record.status       = EInvoiceRecord.STATUS_FAILED
            record.error_message = str(exc)
            record.raw_response  = exc.raw_response
            record.save(update_fields=['status', 'error_message', 'raw_response', 'updated_at'])
            return Response(
                {'error': str(exc), 'details': exc.raw_response},
                status=status.HTTP_502_BAD_GATEWAY,
            )
        except Exception as exc:
            logger.exception('Unexpected error during IRN generation for invoice %s', pk)
            record.status       = EInvoiceRecord.STATUS_FAILED
            record.error_message = str(exc)
            record.save(update_fields=['status', 'error_message', 'updated_at'])
            return Response(
                {'error': 'Unexpected server error during IRN generation.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ── 4. Persist success ────────────────────────────────────────────
        record.status          = EInvoiceRecord.STATUS_ACTIVE
        record.irn             = irn_data['irn']
        record.ack_no          = irn_data['ack_no']
        record.ack_date        = irn_data['ack_date']
        record.signed_qr_data  = irn_data['signed_qr_data']
        record.signed_invoice  = irn_data.get('signed_invoice')
        record.request_payload = irn_data.get('request_payload')
        record.raw_response    = irn_data.get('raw_response')
        record.submitted_at    = timezone.now()
        record.error_message   = ''
        record.save()

        return Response(_serialize_einvoice(record), status=status.HTTP_201_CREATED)


# ─────────────────────────────────────────────────────────────────────────────
# Cancel IRN
# ─────────────────────────────────────────────────────────────────────────────

class EInvoiceCancelView(APIView):
    """
    POST /api/logistics/invoices/{id}/einvoice/cancel/

    Body (JSON):
    {
        "reason": "1",       // 1=Duplicate, 2=Data Error, 3=Order Cancelled, 4=Other
        "remarks": "..."     // optional free text
    }

    Can only be called within 24 hours of IRN generation.
    """

    permission_classes = [IsAuthenticated]

    def post(self, request, pk):
        invoice = _get_invoice(pk, request.tenant)

        try:
            record = EInvoiceRecord.objects.get(
                invoice=invoice,
                tenant=request.tenant,
                status=EInvoiceRecord.STATUS_ACTIVE,
            )
        except EInvoiceRecord.DoesNotExist:
            return Response(
                {'error': 'No active IRN found for this invoice.'},
                status=status.HTTP_404_NOT_FOUND,
            )

        if not record.can_cancel:
            return Response(
                {'error': 'IRN can only be cancelled within 24 hours of acknowledgement.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        reason  = request.data.get('reason', '4')
        remarks = request.data.get('remarks', '')

        try:
            client = GSPClient.for_tenant(request.tenant)
            client.cancel_irn(record.irn, reason, remarks)
        except GSPError as exc:
            return Response(
                {'error': str(exc), 'details': exc.raw_response},
                status=status.HTTP_502_BAD_GATEWAY,
            )

        record.status        = EInvoiceRecord.STATUS_CANCELLED
        record.cancel_reason  = reason
        record.cancel_remarks = remarks
        record.cancelled_at   = timezone.now()
        record.save(update_fields=[
            'status', 'cancel_reason', 'cancel_remarks', 'cancelled_at', 'updated_at'
        ])

        return Response({'success': True, 'irn': record.irn, 'status': 'CANCELLED'})


# ─────────────────────────────────────────────────────────────────────────────
# E-Invoice PDF (with QR code + IRN printed)
# ─────────────────────────────────────────────────────────────────────────────

class EInvoicePDFView(APIView):
    """
    GET /api/logistics/invoices/{id}/einvoice/pdf/

    Returns the invoice as a PDF that includes:
      • IRN
      • Acknowledgement number + date
      • QR code (rendered from signed_qr_data)

    The invoice must already have an ACTIVE EInvoiceRecord (i.e. you must have
    called /einvoice/generate/ first). Returns 400 if IRN is not yet generated.

    Query params:
      ?download=true   → Content-Disposition: attachment
      (default)        → Content-Disposition: inline
    """

    permission_classes = [IsAuthenticated]

    def get(self, request, pk):
        invoice = _get_invoice(pk, request.tenant)

        # ── Require active IRN ────────────────────────────────────────────
        try:
            einvoice_record = EInvoiceRecord.objects.get(
                invoice=invoice,
                tenant=request.tenant,
                status=EInvoiceRecord.STATUS_ACTIVE,
            )
        except EInvoiceRecord.DoesNotExist:
            return Response(
                {
                    'error': (
                        'This invoice does not have an active IRN yet. '
                        'Call POST /einvoice/generate/ first.'
                    )
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        # ── Build context ─────────────────────────────────────────────────
        context = _build_einvoice_context(invoice, einvoice_record)

        # ── Render HTML template ──────────────────────────────────────────
        html = render_to_string('documents/einvoice.html', context)

        # ── Get letterhead ────────────────────────────────────────────────
        try:
            lh = invoice.tenant.letterhead
            letterhead_file = lh.letterhead_pdf if lh and lh.letterhead_pdf else None
        except TenantLetterhead.DoesNotExist:
            letterhead_file = None

        # ── Generate PDF ──────────────────────────────────────────────────
        try:
            pdf_bytes = generate_quotation_pdf(
                html=html,
                base_url=request.build_absolute_uri('/'),
                letterhead_pdf_file=letterhead_file,
            )
        except ImportError as exc:
            return Response({'error': str(exc)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        except Exception as exc:
            return Response(
                {'error': f'PDF generation failed: {exc}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        # ── Return PDF ────────────────────────────────────────────────────
        filename    = f"EInvoice_{invoice.invoice_number}.pdf"
        as_download = request.query_params.get('download', 'false').lower() == 'true'
        disposition = 'attachment' if as_download else 'inline'

        response = StreamingHttpResponse(
            streaming_content=iter([pdf_bytes]),
            content_type='application/pdf',
        )
        response['Content-Disposition'] = (
            f'{disposition}; filename="{filename}"; '
            f"filename*=UTF-8''{filename}"
        )
        response['Content-Length']  = len(pdf_bytes)
        response['X-Frame-Options'] = 'SAMEORIGIN'
        response['Cache-Control']   = 'private, no-transform'
        return response


# ─────────────────────────────────────────────────────────────────────────────
# Context builder for the e-invoice HTML template
# ─────────────────────────────────────────────────────────────────────────────

def _build_einvoice_context(invoice, einvoice_record: EInvoiceRecord) -> dict:
    """
    Assembles every variable the einvoice.html template needs.
    Mirrors build_invoice_context from invoice_pdf_view.py but also injects
    the IRN, QR data, and ack details.
    """

    order    = invoice.order
    oa       = getattr(order, 'oa', None)
    quotation = oa.quotation if oa else None
    tenant   = invoice.tenant

    # ── Letterhead ────────────────────────────────────────────────────────
    try:
        lh = tenant.letterhead
    except TenantLetterhead.DoesNotExist:
        lh = None

    company_name    = (lh and lh.company_name)    or getattr(tenant, 'company_name', '') or ''
    company_address = (lh and lh.company_address) or ''
    company_phone   = (lh and lh.company_phone)   or ''
    company_email   = (lh and lh.company_email)   or ''
    company_gstin   = (lh and lh.company_gstin)   or getattr(tenant, 'gstin', '') or ''
    company_pan     = (lh and lh.company_pan)     or ''
    company_state   = (lh and lh.company_state)   or ''
    # Reuse GSPClient's state-name→code map so seller state code is derived
    # identically here and in the actual IRP payload — one source of truth.
    company_state_code = GSPClient._resolve_state_code(lh)
    bank_name           = (lh and lh.bank_name) or ''
    bank_account_name   = (lh and lh.bank_account_name) or ''
    bank_branch         = (lh and lh.bank_branch) or ''
    bank_account_number = (lh and lh.bank_account_number) or ''
    bank_ifsc           = (lh and lh.bank_ifsc) or ''

    # ── Buyer state code ─────────────────────────────────────────────────
    # invoice.state_code may hold a name or an already-numeric code — resolve
    # it the same way build_irn_payload() does, so the PDF's CGST/SGST-vs-IGST
    # split always matches what was actually submitted to IRP.
    buyer_state_code = resolve_state_code(invoice.state_code)

    # ── Addresses ─────────────────────────────────────────────────────────
    # Read from the invoice's own snapshot, captured at entry time
    # (CreateInvoice.jsx AddressCard: entity_name, address_line, city,
    # state, pincode, country) — NOT from the customer's live address book,
    # which may have changed since this invoice was raised.
    bill_to = invoice.bill_to or {}
    ship_to = invoice.ship_to or bill_to

    # ── Line items ────────────────────────────────────────────────────────
    line_items_qs  = invoice.line_items.all()
    line_items_ctx = []

    intra = bool(buyer_state_code) and buyer_state_code == str(company_state_code or '').strip()

    for item in line_items_qs:
        qty      = Decimal(str(item.quantity or 0))
        price    = Decimal(str(item.unit_price or 0))
        taxable  = qty * price
        tax_amt  = Decimal(str(item.tax_amount or 0))
        cgst_amt = (tax_amt / 2).quantize(Decimal('0.01'))
        sgst_amt = (tax_amt / 2).quantize(Decimal('0.01'))

        line_items_ctx.append({
            'part_no':       getattr(item, 'part_no', '') or '',
            'hsn_code':      getattr(item, 'hsn_code', '') or '',
            'description':   item.description or '',
            'job_code':      getattr(item, 'job_code', '') or '',
            'quantity':      item.quantity,
            'unit':          item.unit or 'NOS',
            'unit_price':    item.unit_price,
            'taxable_amount': taxable,
            'tax_percent':   item.tax_percent,
            'tax_amount':    tax_amt,
            'cgst_amount':   cgst_amt if intra else Decimal('0'),
            'sgst_amount':   sgst_amt if intra else Decimal('0'),
            'igst_amount':   tax_amt  if not intra else Decimal('0'),
            'total':         taxable + tax_amt,
        })

    # ── GST split ─────────────────────────────────────────────────────────
    cgst, sgst, igst, cgst_rate, sgst_rate, igst_rate = split_gst(
        line_items_qs,
        customer_state=(ship_to or {}).get('state', '') or (bill_to or {}).get('state', '') or '',
        company_state=company_state,
    )

    # ── Back-order reference ───────────────────────────────────────────────
    back_order_number = ''
    if invoice.back_order:
        back_order_number = invoice.back_order.back_order_number

    # ── Date formatter ────────────────────────────────────────────────────
    def fmt(d):
        if not d:
            return '—'
        return d.strftime('%d %b %Y') if hasattr(d, 'strftime') else str(d)

    def fmt_dt(d):
        if not d:
            return '—'
        return d.strftime('%d %b %Y %H:%M') if hasattr(d, 'strftime') else str(d)

    # ── Currency — lives on Order, not SalesInvoice ─────────────────────────
    invoice_currency = (order.currency if order else '') or 'INR'

    return {
        # Company
        'company_name':    company_name,
        'company_address': company_address,
        'company_phone':   company_phone,
        'company_email':   company_email,
        'company_gstin':   company_gstin,
        'company_pan':     company_pan,

        # Bank
        'bank_name':           bank_name,
        'bank_account_name':   bank_account_name,
        'bank_branch':         bank_branch,
        'bank_account_number': bank_account_number,
        'bank_ifsc':           bank_ifsc,

        # Invoice
        'invoice':          invoice,
        'invoice_date':     fmt(invoice.invoice_date),
        'payment_due_date': fmt(invoice.payment_due_date) if hasattr(invoice, 'payment_due_date') else '—',
        'invoice_currency': invoice_currency,

        # References
        'oa_number':        oa.oa_number if oa else '—',
        'order_number':     order.order_number if order else '—',
        'back_order_number': back_order_number,

        # Transport / dispatch (Rule 46 requires these whenever goods move,
        # independent of e-way bill generation)
        'transporter_name':  invoice.transporter or '',
        'vehicle_number':    invoice.vehicle_number or '',
        'lr_number':         invoice.lr_number or '',
        'mode_of_transport': invoice.mode_of_transport or '',
        'date_of_removal':   fmt(invoice.date_of_removal),
        'time_of_removal':   invoice.time_of_removal.strftime('%H:%M') if invoice.time_of_removal else '—',

        # Customer
        'customer_name': (bill_to or {}).get('entity_name', '') or '',
        'bill_to':       bill_to,
        'ship_to':       ship_to,

        # Line items
        'line_items': line_items_ctx,

        # GST
        'cgst_amount': cgst,
        'sgst_amount': sgst,
        'igst_amount': igst,
        'cgst_rate':   cgst_rate,
        'sgst_rate':   sgst_rate,
        'igst_rate':   igst_rate,

        # Totals
        'net_amount':  invoice.net_amount,
        'grand_total': invoice.grand_total,

        'amount_in_words': amount_in_words(invoice.grand_total, invoice_currency),

        # ── E-invoice specific ─────────────────────────────────────────────
        'irn':            einvoice_record.irn,
        'ack_no':         einvoice_record.ack_no,
        'ack_date':       fmt_dt(einvoice_record.ack_date),
        'signed_qr_data': einvoice_record.signed_qr_data,
        # qr_image_base64 is injected below
        'qr_image_base64': _qr_to_base64(einvoice_record.signed_qr_data),
    }


def _qr_to_base64(qr_data: str) -> str:
    """
    Render qr_data string as a PNG QR code and return it as a base64 data URI.
    Returns empty string if qrcode / segno is not installed or qr_data is empty.

    Install:  pip install qrcode[pil]
              — or —
              pip install segno
    """
    if not qr_data:
        return ''

    # Try segno first (lighter, no Pillow required)
    try:
        import segno
        import io
        qr  = segno.make(qr_data, error='L')
        buf = io.BytesIO()
        qr.save(buf, kind='png', scale=3)
        buf.seek(0)
        import base64
        b64 = base64.b64encode(buf.read()).decode('ascii')
        return f'data:image/png;base64,{b64}'
    except ImportError:
        pass

    # Fallback: qrcode + Pillow
    try:
        import qrcode
        import io, base64
        img = qrcode.make(qr_data)
        buf = io.BytesIO()
        img.save(buf, format='PNG')
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode('ascii')
        return f'data:image/png;base64,{b64}'
    except ImportError:
        return ''


# ─────────────────────────────────────────────────────────────────────────────
# Serializer helper
# ─────────────────────────────────────────────────────────────────────────────

def _serialize_einvoice(record: EInvoiceRecord) -> dict:
    return {
        'id':             record.id,
        'invoice_id':     str(record.invoice_id),
        'invoice_number': record.invoice.invoice_number,
        'status':         record.status,
        'irn':            record.irn,
        'ack_no':         record.ack_no,
        'ack_date':       record.ack_date.isoformat() if record.ack_date else None,
        'signed_qr_data': record.signed_qr_data,
        'can_cancel':     record.can_cancel,
        'error_message':  record.error_message,
        'submitted_at':   record.submitted_at.isoformat() if record.submitted_at else None,
        'created_at':     record.created_at.isoformat(),
    }