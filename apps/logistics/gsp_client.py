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

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from decimal import Decimal

import requests
from django.utils import timezone

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
    """

    # ── Pull related objects ──────────────────────────────────────────────────
    order    = invoice.order
    oa       = getattr(order, 'oa', None)
    quotation = oa.quotation if oa else None
    enquiry  = quotation.enquiry if quotation else None
    customer = enquiry.customer if enquiry else None

    # Billing & shipping address
    bill_addr = None
    ship_addr = None
    if customer:
        bill_addr = (
            customer.addresses.filter(address_type='BILLING', is_default=True).first()
            or customer.addresses.filter(address_type='BILLING').first()
        )
        ship_addr = (
            customer.addresses.filter(address_type='SHIPPING', is_default=True).first()
            or customer.addresses.filter(address_type='SHIPPING').first()
            or bill_addr
        )

    customer_gstin = getattr(customer, 'gst_number', '') or 'URP'  # URP = Unregistered Person

    # ── Address helper ────────────────────────────────────────────────────────
    def _addr_block(addr_obj, gstin: str, legal_name: str, state_code: str) -> dict:
        if not addr_obj:
            return {
                'Gstin': gstin,
                'LglNm': legal_name,
                'Addr1': '',
                'Loc':   '',
                'Pin':   '000000',
                'Stcd': state_code or '36',
            }
        return {
            'Gstin': gstin,
            'LglNm': legal_name,
            'TrdNm': legal_name,
            'Addr1': getattr(addr_obj, 'address_line', '') or '',
            'Addr2': getattr(addr_obj, 'address_line_2', '') or '',
            'Loc':   getattr(addr_obj, 'city', '') or '',
            'Pin':   int(getattr(addr_obj, 'pincode', 0) or 0),
            'Stcd': getattr(addr_obj, 'state_code', '') or state_code or '36',
            'Ph':    getattr(customer, 'telephone_primary', '') or '',
            'Em':    getattr(customer, 'email', '') or '',
        }

    # ── Line items ────────────────────────────────────────────────────────────
    intra_state = (
        (getattr(customer, 'state', '') or '').strip().lower()
        ==
        (company_state_code or '').strip().lower()
    )

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
            'Stcd':  company_state_code or '27',
        },
        'BuyerDtls': _addr_block(
            bill_addr,
            customer_gstin,
            getattr(customer, 'company_name', '') or '',
            getattr(bill_addr, 'state_code', '') if bill_addr else '',
        ),
        'DispDtls': None,   # dispatch details (optional)
        'ShipDtls': _addr_block(
            ship_addr,
            customer_gstin,
            getattr(customer, 'company_name', '') or '',
            getattr(ship_addr, 'state_code', '') if ship_addr else '',
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
            'Info': getattr(quotation, 'po_number', '') or '',
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
        company_state_code  = self._resolve_state_code(lh)

        # 2. Build payload
        payload = build_irn_payload(invoice, company_gstin, company_state_code)

        # Patch seller details with letterhead info
        payload['SellerDtls'].update({
            'LglNm': company_name,
            'TrdNm': company_name,
            'Addr1': company_address[:100] if company_address else '',
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
        Map state name to 2-digit GST state code.
        The IRP requires the numeric code, not the state name.
        """
        _STATE_CODES = {
            'jammu and kashmir': '01', 'himachal pradesh': '02', 'punjab': '03',
            'chandigarh': '04', 'uttarakhand': '05', 'haryana': '06',
            'delhi': '07', 'rajasthan': '08', 'uttar pradesh': '09',
            'bihar': '10', 'sikkim': '11', 'arunachal pradesh': '12',
            'nagaland': '13', 'manipur': '14', 'mizoram': '15',
            'tripura': '16', 'meghalaya': '17', 'assam': '18',
            'west bengal': '19', 'jharkhand': '20', 'odisha': '21',
            'chhattisgarh': '22', 'madhya pradesh': '23', 'gujarat': '24',
            'daman and diu': '25', 'dadra and nagar haveli': '26',
            'maharashtra': '27', 'andhra pradesh': '28', 'karnataka': '29',
            'goa': '30', 'lakshadweep': '31', 'kerala': '32',
            'tamil nadu': '33', 'puducherry': '34', 'andaman and nicobar': '35',
            'telangana': '36', 'andhra pradesh (new)': '37',
        }
        state_name = (getattr(lh, 'company_state', '') or '').strip().lower()
        return _STATE_CODES.get(state_name, '27')   # default: Maharashtra