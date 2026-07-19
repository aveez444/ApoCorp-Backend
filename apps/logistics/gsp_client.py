# apps/logistics/gsp_client.py
#
# GSP / IRP client for Indian e-invoice (GST e-invoicing).
#
# ── What this file does ───────────────────────────────────────────────────────
#  1. Builds the IRP-compliant JSON payload from a SalesInvoice ORM object.
#  2. Authenticates with the GSP (fetches + caches an auth token).
#  3. Submits the payload → receives IRN + signed QR.
#  4. Optionally cancels an IRN within the 24-hour window.
#
# ── GSP compatibility ────────────────────────────────────────────────────────
#  The NIC sandbox and most GSPs share an identical REST contract.
#  Provider-specific differences (base URL, auth endpoint path) are handled
#  by the _BASE_URLS / _AUTH_PATHS / _EINV_PATHS dicts below.
#  For a new GSP just add its entries to those dicts and set gsp_provider in
#  TenantGSPConfig accordingly.
#
# ── Install ──────────────────────────────────────────────────────────────────
#   pip install requests
#
# ── Usage (from a view) ──────────────────────────────────────────────────────
#   from .gsp_client import GSPClient, GSPError
#   client = GSPClient.for_tenant(request.tenant)
#   irn_data = client.generate_irn(invoice)        # raises GSPError on failure
#   # irn_data keys: irn, ack_no, ack_date, signed_qr_data, signed_invoice, raw_response
#
# ── State-code resolution (CHANGED) ────────────────────────────────────────
#  Both the supplier's and the buyer's GST state code are now resolved
#  through apps.logistics.state_codes.resolve_state_code(), which accepts
#  either a state name or an already-numeric code and normalizes to the
#  2-digit code IRP requires. If a state can't be resolved, this file now
#  raises GSPError instead of silently defaulting to Maharashtra ('27') or
#  Telangana ('36') as earlier drafts did — a silent wrong default produces
#  either a wrong CGST/SGST-vs-IGST split or an opaque IRP rejection, so we
#  fail fast with a message that tells you exactly what's unset.
#
# ── Seller Loc/Pin (FIXED) ──────────────────────────────────────────────────
#  generate_irn() previously patched SellerDtls with LglNm/TrdNm/Addr1 only,
#  leaving Loc (city) and Pin (pincode) at build_irn_payload()'s defaults of
#  '' and 0. Both are mandatory in IRP's SellerDtls schema — every submission
#  would have been rejected once GSP credentials were in place. Now sourced
#  from TenantLetterhead.company_city / company_pincode (new fields — run
#  makemigrations/migrate on apps.documents), with the same fail-fast
#  behaviour as the state-code check above rather than silently sending
#  Pin=0.

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import requests
from django.utils import timezone

from .state_codes import resolve_state_code

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Provider routing tables
# ─────────────────────────────────────────────────────────────────────────────

_BASE_URLS: dict[str, str] = {
    'NIC_SANDBOX': 'https://einv-apisandbox.nic.in',
    'NIC_PROD':    'https://einvoice1-prod.nic.in',
    'MASTERS':     'https://api.mastersindia.co',
    'CLEAR':       'https://einvoice.clear.in',
    # GENERIC: base URL comes from TenantGSPConfig.gsp_base_url
}

# Auth (token) endpoint path per provider
_AUTH_PATHS: dict[str, str] = {
    'NIC_SANDBOX': '/eivital/v1.04/auth',
    'NIC_PROD':    '/eivital/v1.04/auth',
    'MASTERS':     '/commonapi/v1.0/authenticate',
    'CLEAR':       '/ims/otp/v1/generate',
    'GENERIC':     '/auth',
}

# Generate-IRN endpoint path per provider
_EINV_PATHS: dict[str, str] = {
    'NIC_SANDBOX': '/eicore/v1.03/Invoice',
    'NIC_PROD':    '/eicore/v1.03/Invoice',
    'MASTERS':     '/eInvoiceAPI/ei/api/invoice',
    'CLEAR':       '/ims/einvoice/v1/generate',
    'GENERIC':     '/Invoice',
}

# Cancel-IRN endpoint path per provider
_CANCEL_PATHS: dict[str, str] = {
    'NIC_SANDBOX': '/eicore/v1.03/Invoice/Cancel',
    'NIC_PROD':    '/eicore/v1.03/Invoice/Cancel',
    'MASTERS':     '/eInvoiceAPI/ei/api/invoice/cancel',
    'CLEAR':       '/ims/einvoice/v1/cancel',
    'GENERIC':     '/Invoice/Cancel',
}

# ─────────────────────────────────────────────────────────────────────────────
# Errors
# ─────────────────────────────────────────────────────────────────────────────

class GSPError(Exception):
    """Raised when the GSP returns an error or the request fails."""

    def __init__(self, message: str, raw_response: dict | None = None):
        super().__init__(message)
        self.raw_response = raw_response or {}


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _d(value, places: int = 2) -> float:
    """Convert any numeric value to a rounded float for JSON serialisation."""
    try:
        return round(float(Decimal(str(value or 0))), places)
    except Exception:
        return 0.0


def _fmt_date(d) -> str:
    """Return date as DD/MM/YYYY — the format IRP expects."""
    if not d:
        return ''
    if hasattr(d, 'strftime'):
        return d.strftime('%d/%m/%Y')
    return str(d)


def _supply_type(invoice) -> str:
    """
    Map invoice_type to IRP SupTyp code.
    B2B — regular business-to-business (most common)
    EXPWP / EXPWOP — export with / without payment
    SEZWP / SEZWOP — SEZ supply
    Adjust as needed for your invoice_type choices.
    """
    mapping = {
        'EXPORT':    'EXPWP',
        'SEZ':       'SEZWP',
        'B2B':       'B2B',
        'DEEMED':    'DEXPWP',
    }
    return mapping.get((invoice.invoice_type or '').upper(), 'B2B')


# ─────────────────────────────────────────────────────────────────────────────
# Payload builder
# ─────────────────────────────────────────────────────────────────────────────

def build_irn_payload(invoice, company_gstin: str, company_state_code: str) -> dict:
    """
    Construct the IRP-compliant e-invoice JSON from a SalesInvoice instance.

    The schema follows NIC's e-invoice API schema v1.1 (2023+).
    All field names and nesting match IRP exactly — do not rename.

    Args:
        invoice           : SalesInvoice ORM object (with .line_items prefetched)
        company_gstin     : Supplier GSTIN (from TenantGSPConfig or TenantLetterhead)
        company_state_code: 2-digit state code of supplier, e.g. '27' for Maharashtra
                             (already resolved by the caller — see generate_irn)
    """

    # ── Pull related objects ──────────────────────────────────────────────────
    # NOTE: order/oa/quotation are only used for descriptive/reference fields
    # (PO number etc). Address + GSTIN + state MUST come from the invoice
    # snapshot below — never from the customer's live records — because the
    # invoice reflects the address as it was at billing time, and the
    # customer's address/GSTIN may have changed since.
    order    = invoice.order
    oa       = getattr(order, 'oa', None)
    quotation = oa.quotation if oa else None

    # ── Address snapshots (captured on the invoice at entry time) ─────────────
    # Shape saved by the frontend (CreateInvoice.jsx AddressCard):
    #   { entity_name, address_line, city, state, pincode, country }
    bill_snap = invoice.bill_to or {}
    ship_snap = invoice.ship_to or bill_snap or {}

    buyer_gstin = (invoice.consignee_gst or '').strip() or 'URP'  # URP = Unregistered Person

    # ── Buyer state code ────────────────────────────────────────────────────
    # invoice.state_code may hold either a state name (e.g. populated from
    # customer.state as a fallback) or an already-numeric code (e.g. sent
    # explicitly by the frontend). resolve_state_code() normalizes either.
    # This is the authoritative value for e-invoice purposes — never
    # re-derived from the customer's live record here.
    buyer_state_code = resolve_state_code(invoice.state_code)
    if not buyer_state_code:
        raise GSPError(
            f'Cannot determine buyer GST state code for invoice '
            f'{invoice.invoice_number}: invoice.state_code is empty or not '
            f'recognized ("{invoice.state_code}"). Set a valid state/state '
            f'code on the invoice before generating the e-invoice.'
        )

    # ── Address helper ────────────────────────────────────────────────────────
    def _addr_block(snap: dict, gstin: str, state_code: str,
                     phone: str = '', email: str = '') -> dict:
        legal_name = (snap.get('entity_name') or '').strip()
        pincode_raw = snap.get('pincode') or 0
        try:
            pin = int(str(pincode_raw).strip() or 0)
        except ValueError:
            pin = 0
        return {
            'Gstin': gstin,
            'LglNm': legal_name,
            'TrdNm': legal_name,
            'Addr1': (snap.get('address_line') or '')[:100],
            'Loc':   (snap.get('city') or '')[:50],
            'Pin':   pin,
            'Stcd':  state_code,
            'Ph':    phone or '',
            'Em':    email or '',
        }

    # ── Intra vs inter state ────────────────────────────────────────────────
    intra_state = buyer_state_code == (company_state_code or '').strip()

    item_list = []
    for idx, item in enumerate(invoice.line_items.all(), start=1):
        qty      = _d(item.quantity, 3)
        price    = _d(item.unit_price)
        taxable  = round(qty * price, 2)
        tax_pct  = _d(item.tax_percent)
        tax_amt  = _d(item.tax_amount)
        total    = round(taxable + tax_amt, 2)

        # GST split
        if intra_state:
            cgst_rt  = tax_pct / 2
            sgst_rt  = tax_pct / 2
            cgst_amt = round(tax_amt / 2, 2)
            sgst_amt = round(tax_amt / 2, 2)
            igst_rt  = 0.0
            igst_amt = 0.0
        else:
            cgst_rt = cgst_amt = sgst_rt = sgst_amt = 0.0
            igst_rt  = tax_pct
            igst_amt = tax_amt

        item_list.append({
            'SlNo':     str(idx),
            'PrdDesc':  item.description or '',
            'IsServc':  'N',
            'HsnCd':    item.hsn_code or '',
            'Qty':      qty,
            'Unit':     (item.unit or 'NOS').upper(),
            'UnitPrice': price,
            'TotAmt':   taxable,
            'Discount': 0,
            'AssAmt':   taxable,
            'GstRt':    tax_pct,
            'IgstAmt':  igst_amt,
            'CgstAmt':  cgst_amt,
            'SgstAmt':  sgst_amt,
            'CesRt':    0,
            'CesAmt':   0,
            'TotItemVal': total,
        })

    # ── Invoice-level tax totals ───────────────────────────────────────────────
    net_amt   = _d(invoice.net_amount)
    tax_amt   = _d(invoice.tax_amount)
    grand_tot = _d(invoice.grand_total)

    if intra_state:
        val_details = {
            'AssVal': net_amt,
            'CgstVal': round(tax_amt / 2, 2),
            'SgstVal': round(tax_amt / 2, 2),
            'IgstVal': 0.0,
            'CesVal':  0.0,
            'TotInvVal': grand_tot,
        }
    else:
        val_details = {
            'AssVal':    net_amt,
            'CgstVal':   0.0,
            'SgstVal':   0.0,
            'IgstVal':   tax_amt,
            'CesVal':    0.0,
            'TotInvVal': grand_tot,
        }

    # ── Full payload ──────────────────────────────────────────────────────────
    payload = {
        'Version': '1.1',
        'TranDtls': {
            'TaxSch': 'GST',
            'SupTyp':  _supply_type(invoice),
            'RegRev': 'N',
            'EcmGstin': None,
            'IgstOnIntra': 'N',
        },
        'DocDtls': {
            'Typ':  'INV',
            'No':   invoice.invoice_number,
            'Dt':   _fmt_date(invoice.invoice_date),
        },
        'SellerDtls': {
            'Gstin': company_gstin,
            'LglNm': '',          # filled by caller (view) from letterhead
            'TrdNm': '',
            'Addr1': '',
            'Loc':   '',
            'Pin':   0,
            'Stcd':  company_state_code,
        },
        'BuyerDtls': _addr_block(
            bill_snap,
            buyer_gstin,
            buyer_state_code,
            phone=invoice.contact_number or '',
            email=invoice.contact_email or '',
        ),
        'DispDtls': None,   # dispatch details (optional)
        'ShipDtls': _addr_block(
            ship_snap,
            buyer_gstin,
            buyer_state_code,
            phone=invoice.contact_number or '',
            email=invoice.contact_email or '',
        ),
        'ItemList': item_list,
        'ValDtls': val_details,
        'PayDtls': {
            'Nm':   '',
            'Mode': 'Cash',
        },
        'RefDtls': {
            'PrecDocDtls': [],
            'ContrDtls':   [],
        },
        'AddlDocDtls': {
            'Url':  '',
            'Docs': '',
            'Info': invoice.po_number or getattr(quotation, 'po_number', '') or '',
        },
    }

    return payload


# ─────────────────────────────────────────────────────────────────────────────
# GSP Client
# ─────────────────────────────────────────────────────────────────────────────

class GSPClient:
    """
    Thin wrapper around the GSP REST API.

    Instantiate with GSPClient.for_tenant(tenant) — it reads TenantGSPConfig
    automatically.

    Main methods:
        generate_irn(invoice)  → dict with irn, ack_no, ack_date, signed_qr_data, ...
        cancel_irn(irn, reason, remarks)
    """

    def __init__(self, config):
        """
        config: TenantGSPConfig instance
        """
        self.config   = config
        self.provider = config.gsp_provider

        if self.provider == 'GENERIC':
            self.base_url = config.gsp_base_url.rstrip('/')
        else:
            self.base_url = _BASE_URLS.get(self.provider, _BASE_URLS['NIC_SANDBOX'])

        self._session = requests.Session()
        self._session.headers.update({
            'Content-Type': 'application/json',
            'Accept':        'application/json',
        })

    # ── Factory ────────────────────────────────────────────────────────────

    @classmethod
    def for_tenant(cls, tenant) -> 'GSPClient':
        """
        Load TenantGSPConfig for tenant and return a ready client.
        Raises GSPError if no config is found or config is inactive.
        """
        try:
            from .einvoice_models import TenantGSPConfig
            config = tenant.gsp_config
        except Exception:
            raise GSPError(
                'E-invoice is not configured for this tenant. '
                'Please set up GSP credentials in the admin panel.'
            )

        if not config.is_active:
            raise GSPError('E-invoice GSP config is marked inactive for this tenant.')

        return cls(config)

    # ── Authentication ─────────────────────────────────────────────────────

    def _get_auth_token(self) -> str:
        """
        Return a valid auth token, refreshing it via the GSP if expired.
        Tokens are cached in TenantGSPConfig to avoid a login on every request.
        """
        if self.config.is_token_valid():
            return self.config.auth_token

        logger.info('GSP: refreshing auth token for tenant %s', self.config.tenant_id)

        url = self.base_url + _AUTH_PATHS.get(self.provider, '/auth')

        # NIC / most GSPs use this body shape; adapt for your GSP if different.
        body = {
            'UserName': self.config.irp_username,
            'Password': self.config.irp_password,
            'AppKey':   self.config.client_id,
            'ForceRefreshAccessToken': True,
        }

        try:
            resp = self._session.post(url, json=body, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise GSPError(f'GSP auth request failed: {exc}')

        # NIC returns: {"Status": 1, "AuthDtls": {"AuthToken": "...", "TokenExpiry": "..."}}
        # Adapt the key names for your GSP.
        status = data.get('Status') or data.get('status')
        if str(status) != '1':
            err = data.get('ErrorDetails') or data.get('message') or str(data)
            raise GSPError(f'GSP authentication failed: {err}', raw_response=data)

        auth_details = (
            data.get('AuthDtls')
            or data.get('auth_details')
            or data
        )
        token  = auth_details.get('AuthToken') or auth_details.get('access_token') or ''
        expiry_str = auth_details.get('TokenExpiry') or ''

        # Parse expiry; default to 6 hours from now if not provided
        try:
            expiry = datetime.strptime(expiry_str, '%d/%m/%Y %H:%M:%S')
            expiry = timezone.make_aware(expiry)
        except (ValueError, TypeError):
            expiry = timezone.now() + timedelta(hours=6)

        self.config.cache_token(token, expiry)
        return token

    # ── Generate IRN ───────────────────────────────────────────────────────

    def generate_irn(self, invoice) -> dict:
        """
        Submit the invoice to IRP and return a dict with:
            irn            : str   — 64-char Invoice Reference Number
            ack_no         : str   — acknowledgement number
            ack_date       : datetime (aware, IST → UTC-stored by Django)
            signed_qr_data : str   — QR string to render as QR code in the PDF
            signed_invoice : dict  — full signed invoice JSON from IRP
            raw_response   : dict  — complete raw response (for audit)
        Raises GSPError on failure.
        """
        # 1. Get company details from tenant's letterhead config
        try:
            lh = invoice.tenant.letterhead
        except Exception:
            lh = None

        company_gstin       = (lh and lh.company_gstin) or self.config.gstin
        company_name        = (lh and lh.company_name)  or ''
        company_address     = (lh and lh.company_address) or ''
        company_city        = (lh and lh.company_city) or ''
        company_pincode_raw = (lh and lh.company_pincode) or ''
        company_state_code  = self._resolve_state_code(lh)

        if not company_state_code:
            raise GSPError(
                'Cannot determine supplier GST state code — set a valid '
                'company_state on the tenant letterhead before generating '
                'e-invoices.'
            )

        # Loc (city) and Pin (6-digit pincode) are mandatory in IRP's
        # SellerDtls block. A missing/invalid pincode here fails silently
        # as Pin=0 if left unchecked — fail fast instead, same as state code.
        if not company_city:
            raise GSPError(
                'Cannot generate e-invoice: supplier city is not set on the '
                'tenant letterhead (required as SellerDtls.Loc by IRP).'
            )
        try:
            company_pincode = int(str(company_pincode_raw).strip())
            if not (100000 <= company_pincode <= 999999):
                raise ValueError
        except (ValueError, TypeError):
            raise GSPError(
                f'Cannot generate e-invoice: supplier pincode '
                f'("{company_pincode_raw}") is missing or not a valid 6-digit '
                f'PIN code (required as SellerDtls.Pin by IRP).'
            )

        # 2. Build payload
        payload = build_irn_payload(invoice, company_gstin, company_state_code)

        # Patch seller details with letterhead info
        payload['SellerDtls'].update({
            'LglNm': company_name,
            'TrdNm': company_name,
            'Addr1': company_address[:100] if company_address else '',
            'Loc':   company_city[:50],
            'Pin':   company_pincode,
        })

        # 3. Authenticate
        token = self._get_auth_token()

        # 4. Submit
        url  = self.base_url + _EINV_PATHS.get(self.provider, '/Invoice')
        hdrs = {
            'user_name':   self.config.irp_username,
            'authtoken':   token,
            'gstin':       company_gstin,
            'client_id':   self.config.client_id,
            'client_secret': self.config.client_secret,
        }

        logger.info('GSP: submitting invoice %s to IRP', invoice.invoice_number)

        try:
            resp = self._session.post(url, json=payload, headers=hdrs, timeout=60)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise GSPError(f'GSP generate-IRN request failed: {exc}')

        # 5. Parse response
        # NIC response shape:
        # {
        #   "Status": 1,
        #   "EInvDtls": [{
        #     "Irn": "...", "AckNo": "...", "AckDt": "2024-01-01 12:00:00",
        #     "SignedQRCode": "...", "SignedInvoice": "..."
        #   }]
        # }
        status_code = str(data.get('Status') or data.get('status') or '')
        if status_code != '1':
            err_details = data.get('ErrorDetails') or data.get('message') or str(data)
            raise GSPError(
                f'IRP rejected the invoice: {err_details}',
                raw_response=data,
            )

        einv_list = data.get('EInvDtls') or [data]
        einv      = einv_list[0] if einv_list else {}

        irn = einv.get('Irn') or einv.get('irn') or ''
        ack_no = str(einv.get('AckNo') or einv.get('ack_no') or '')

        ack_dt_str = einv.get('AckDt') or einv.get('ack_dt') or ''
        try:
            ack_date = datetime.strptime(ack_dt_str, '%Y-%m-%d %H:%M:%S')
            ack_date = timezone.make_aware(ack_date)
        except (ValueError, TypeError):
            ack_date = timezone.now()

        signed_qr   = einv.get('SignedQRCode') or einv.get('signed_qr') or ''
        signed_inv  = einv.get('SignedInvoice') or einv.get('signed_invoice') or {}

        if not irn:
            raise GSPError('IRP returned success but IRN is missing.', raw_response=data)

        logger.info('GSP: IRN %s issued for invoice %s', irn, invoice.invoice_number)

        return {
            'irn':             irn,
            'ack_no':          ack_no,
            'ack_date':        ack_date,
            'signed_qr_data':  signed_qr,
            'signed_invoice':  signed_inv,
            'raw_response':    data,
            'request_payload': payload,
        }

    # ── Cancel IRN ─────────────────────────────────────────────────────────

    def cancel_irn(self, irn: str, reason: str, remarks: str = '') -> dict:
        """
        Cancel an IRN via the GSP.
        reason must be one of: '1' (Duplicate), '2' (Data Error), '3' (Order Cancelled),
        '4' (Other).
        Returns the raw response dict.
        Raises GSPError on failure.
        """
        try:
            lh = self.config.tenant.letterhead
            company_gstin = lh.company_gstin or self.config.gstin
        except Exception:
            company_gstin = self.config.gstin

        token = self._get_auth_token()
        url   = self.base_url + _CANCEL_PATHS.get(self.provider, '/Invoice/Cancel')
        hdrs  = {
            'user_name':     self.config.irp_username,
            'authtoken':     token,
            'gstin':         company_gstin,
            'client_id':     self.config.client_id,
            'client_secret': self.config.client_secret,
        }
        body = {
            'Irn':     irn,
            'CnlRsn':  reason,
            'CnlRem':  remarks or '',
        }

        try:
            resp = self._session.post(url, json=body, headers=hdrs, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            raise GSPError(f'GSP cancel-IRN request failed: {exc}')

        status_code = str(data.get('Status') or data.get('status') or '')
        if status_code != '1':
            err = data.get('ErrorDetails') or data.get('message') or str(data)
            raise GSPError(f'IRP cancellation failed: {err}', raw_response=data)

        return data

    # ── Internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _resolve_state_code(lh) -> str:
        """
        Map the tenant letterhead's company_state to a 2-digit GST state code.
        Thin wrapper around the shared resolve_state_code() so callers that
        already reference GSPClient._resolve_state_code(lh) (e.g.
        einvoice_views.py) don't need to change.
        """
        state_value = getattr(lh, 'company_state', '') if lh else ''
        return resolve_state_code(state_value)